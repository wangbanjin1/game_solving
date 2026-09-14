import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from game_solving.application.services import Services, ModelRegistry
from game_solving.application.pipeline import Pipeline
from game_solving.application.cli import main
from game_solving.infrastructure.config import load_config, validate, model_hash
from game_solving.infrastructure.json_store import read_jsonl
from game_solving.domain.entities import User, Stream, Scene, Bandwidth, scene_from_dict
from game_solving.optimization.budget import Budget, BudgetExceeded
from game_solving.optimization.feasibility import certificate
from game_solving.optimization.history import time_weighted_mos
from game_solving.evaluation.validation import check_actions, total
from game_solving.evaluation.reference import evaluate_reference
from game_solving.evaluation.metrics import evaluate
from game_solving.simulation.population import counts


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = load_config("configs/tiny.json")
        cls.services = Services(cls.c)
        cls.scenes, cls.audits, cls.report = cls.services.generator().generate()

    def golden_scene(self, n=60, target=4):
        c = load_config()
        services = Services(c)
        stream = Stream(2000, 720, 1280, 720, 60, 0.001, 0.01, 800, 6000)
        mos = services.model.forward("meeting", stream)
        users = tuple(
            User(
                f"u{i}",
                "meeting",
                "vip",
                "near",
                "low",
                "demo_non_gbr",
                Bandwidth(2000, 2000),
                {"ul": stream, "dl": stream},
                mos,
                target,
                target,
            )
            for i in range(n)
        )
        return (
            c,
            services,
            Scene("golden", model_hash(c), Bandwidth(120000, 120000), users),
        )

    def test_population_counts_reproducibility_and_isolation(self):
        self.assertEqual(
            sum(counts(30, {"a": 0.333, "b": 0.333, "c": 0.334}).values()), 30
        )
        scenes, audits, report = Services(self.c).generator().generate()
        self.assertEqual(scenes, self.scenes)
        self.assertEqual(audits, self.audits)
        self.assertEqual(report["failed"], 0)
        self.assertFalse(hasattr(scenes[0].users[0], "requested_band"))
        self.assertIn("requested_band", audits[0]["users"][0])

    def test_golden_target_capacity_proof(self):
        c, services, scene = self.golden_scene()
        proof, actions = certificate(scene, "target", services.policy)
        self.assertEqual(proof["status"], "infeasible")
        self.assertEqual(proof["required_ul_kbps"], 128880)
        self.assertIsNone(actions)
        small = replace(scene, users=scene.users[:50])
        proof, actions = certificate(small, "target", services.policy)
        self.assertEqual(proof["status"], "feasible")
        self.assertEqual(proof["required_dl_kbps"], 107400)

    def test_normal_solver_and_reference(self):
        scene = self.scenes[0]
        result = Services(self.c).solver().solve(scene)
        self.assertEqual(result.solution_status, "FEASIBLE_NORMAL")
        self.assertEqual(
            check_actions(
                scene, result.decisions, self.services.policy, recompute=True
            ),
            [],
        )
        reference = evaluate_reference(scene, result, self.c, self.services.policy)
        self.assertEqual(reference["status"], "exact_discrete")
        labels, m = evaluate(scene, result, reference, self.c, self.services.policy)
        self.assertLessEqual(
            m["weighted_mos_algorithm"], reference["U_reference"] + 1e-8
        )
        self.assertLessEqual(m["H_algorithm"], reference["H_reference"] + 1e-8)
        self.assertEqual(m["N_improved"] + m["N_worsened"] + m["N_unchanged"], 3)

    def test_iteration_limit_and_input_immutability(self):
        c = load_config("configs/tiny.json", {"solver": {"max_iterations": 1}})
        scene = copy.deepcopy(self.scenes[0])
        before = copy.deepcopy(scene)
        a = Services(c).solver().solve(scene)
        b = Services(c).solver().solve(scene)
        self.assertEqual(a.stop_reason, "MAX_ITERATIONS")
        self.assertEqual(a.iterations, 1)
        self.assertEqual(a.decisions, b.decisions)
        self.assertEqual(scene, before)

    def test_work_budget_returns_valid_incumbent(self):
        c = load_config("configs/tiny.json", {"solver": {"max_total_steps": 100}})
        result = Services(c).solver().solve(self.scenes[0])
        self.assertEqual(result.stop_reason, "MAX_TOTAL_STEPS")
        self.assertLessEqual(result.total_steps, 100)
        self.assertTrue(result.decisions)
        self.assertEqual(
            check_actions(
                self.scenes[0], result.decisions, Services(c).policy, recompute=True
            ),
            [],
        )

    def test_too_small_budget_and_zero_time(self):
        c = load_config("configs/tiny.json", {"solver": {"max_total_steps": 1}})
        with self.assertRaisesRegex(ValueError, "INVALID_WORK_BUDGET"):
            Services(c).solver().solve(self.scenes[0])
        c = load_config("configs/tiny.json", {"solver": {"time_budget_ms": 0}})
        r = Services(c).solver().solve(self.scenes[0])
        self.assertEqual(r.stop_reason, "TIME_BUDGET")
        self.assertEqual(r.solution_status, "NO_FEASIBLE_FOUND")

    def test_hard_capacity_infeasible_and_soft_basic(self):
        scene = replace(self.scenes[0], capacity=Bandwidth(1, 1))
        result = Services(self.c).solver().solve(scene)
        self.assertEqual(result.solution_status, "INFEASIBLE_HARD")
        self.assertFalse(result.decisions)
        c, services, scene = self.golden_scene(n=1, target=4.5)
        result = services.solver().solve(scene)
        self.assertEqual(result.solution_status, "FEASIBLE_DEGRADED")
        self.assertEqual(result.certificates["basic"]["status"], "infeasible")
        self.assertEqual(result.certificates["hard"]["status"], "feasible")

    def test_fixed_capacity_and_quota_failure_reports(self):
        c = load_config("configs/strict_failure.json")
        scenes, _, report = Services(c).generator().generate()
        self.assertEqual(scenes, [])
        self.assertEqual(report["failed"], 1)
        self.assertIn("EMPTY_REQUESTED", report["failures"][0]["reason"])
        c = load_config(
            "configs/tiny.json",
            {"cell": {"capacity_ul_kbps": 1, "capacity_dl_kbps": 1}},
        )
        scenes, _, report = Services(c).generator().generate()
        self.assertFalse(scenes)
        self.assertEqual(report["failed"], 2)

    def test_relaxed_empty_quota_reports_gap(self):
        c = load_config(
            "configs/strict_failure.json", {"generation": {"strict_quotas": False}}
        )
        scenes, audits, report = Services(c).generator().generate()
        self.assertEqual(report["failed"], 0)
        self.assertGreater(report["quota_gaps"], 0)
        self.assertTrue(scenes)

    def test_config_guards(self):
        for override in (
            {"unexpected": 1},
            {"solver": {"max_iterations": 0}},
            {"population": {"package_probs": {"normal": 0.4}}},
        ):
            with self.assertRaises(ValueError):
                load_config(overrides=override)

    def test_history_is_real_time_and_versioned(self):
        rows = [
            {"start": 0, "end": 10, "mos": 2, "model_hash": "a"},
            {"start": 10, "end": 30, "mos": 4, "model_hash": "a"},
            {"start": 30, "end": 40, "mos": 1, "model_hash": "b"},
        ]
        self.assertAlmostEqual(time_weighted_mos(rows, 30, 30, "a"), 10 / 3)
        self.assertIsNone(time_weighted_mos(rows, 60, 10, "a"))

    def test_cli_run_and_separate_solve(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            solve = Path(tmp) / "solve"
            self.assertEqual(
                main(
                    [
                        "run",
                        "--config",
                        "configs/tiny.json",
                        "--output",
                        str(out),
                        "--max-iterations",
                        "1",
                    ]
                ),
                3,
            )
            self.assertTrue(json.loads((out / "manifest.json").read_text())["complete"])
            self.assertEqual(len(read_jsonl(out / "solve_results.jsonl")), 2)
            self.assertEqual(
                main(
                    [
                        "solve",
                        "--config",
                        "configs/tiny.json",
                        "--input",
                        str(out / "solver_inputs.jsonl"),
                        "--output",
                        str(solve),
                    ]
                ),
                0,
            )
            failed_results = read_jsonl(out / "solve_results.jsonl")
            self.assertTrue(all(r["run_status"] == "FAILED" and r["decisions"] for r in failed_results))
            self.assertEqual(json.loads((out / "summary.json").read_text())["failed_scenes"], 2)
            self.assertTrue(all(r["run_status"] == "SUCCESS" for r in read_jsonl(solve / "solve_results.jsonl")))
            comparisons = read_jsonl(solve / "comparison.jsonl")
            self.assertEqual(len(comparisons), 2)
            for report in comparisons:
                self.assertEqual(report["improved"] + report["worsened"] + report["unchanged"], report["mos_users"])
                for row in report["rows"]:
                    self.assertAlmostEqual(row["change"], row["after"] - row["before"])
            self.assertIn("求解前", (solve / "comparison.md").read_text(encoding="utf-8"))
            self.assertEqual(main(["generate", "--output", str(out)]), 2)

    def test_model_fingerprint_rejects_mixed_formulas(self):
        c = load_config("configs/tiny.json")
        c["models"]["rtt_decay"] *= 2
        with self.assertRaisesRegex(ValueError, "MODEL_CONFIG_MISMATCH"):
            Services(c).solver().solve(self.scenes[0])

    def test_model_registry_extension(self):
        from game_solving.models.mos import MosModel

        class NextModel(MosModel):
            pass

        registry = ModelRegistry()
        registry.register("next_model", NextModel)
        c = load_config()
        c["models"]["name"] = "next_model"
        self.assertIsInstance(Services(c, registry=registry).model, NextModel)

    def test_reference_budget_does_not_claim_exact(self):
        result = Services(self.c).solver().solve(self.scenes[0])
        c = copy.deepcopy(self.c)
        c["reference"]["max_nodes"] = 1
        ref = evaluate_reference(self.scenes[0], result, c, self.services.policy)
        self.assertEqual(ref["status"], "not_computed")

    def test_no_adaptation_keeps_actual_bandwidth(self):
        scene = self.scenes[0]
        users = tuple(replace(u, bitrate_adaptation=False) for u in scene.users)
        scene = replace(scene, users=users)
        result = Services(self.c).solver().solve(scene)
        self.assertEqual(
            [a.bandwidth for a in result.decisions], [u.current for u in scene.users]
        )

    def test_direction_asymmetry_not_two_users(self):
        c, services, scene = self.golden_scene(n=1)
        user = scene.users[0]
        streams = dict(user.streams)
        streams["dl"] = replace(streams["dl"], rtt_ms=100)
        mos = min(services.model.forward("meeting", s) for s in streams.values())
        user = replace(user, streams=streams, observed_mos=mos)
        scene = replace(scene, users=(user,))
        action = services.policy.make_action(user, user.current)
        self.assertNotEqual(action.direction_mos["ul"], action.direction_mos["dl"])
        self.assertEqual(action.mos, min(action.direction_mos.values()))

    def test_degraded_best_not_worse_than_current(self):
        c = load_config(overrides={"num_scenes": 1})
        services = Services(c)
        scenes, _, _ = services.generator().generate()
        scene = scenes[0]
        anchors = [
            services.policy.make_action(u, u.current, anchor=True) for u in scene.users
        ]
        result = services.solver().solve(scene)
        self.assertLessEqual(
            services.policy.rank(scene.users, result.decisions, "DEGRADED"),
            services.policy.rank(scene.users, anchors, "DEGRADED"),
        )

    def test_invalid_snapshot_kqi_and_upper_caps(self):
        scene = self.scenes[0]
        u = scene.users[0]
        streams = dict(u.streams)
        streams["ul"] = replace(streams["ul"], loss_ratio=2)
        with self.assertRaises(ValueError):
            Services(self.c).solver().solve(
                replace(scene, users=(replace(u, streams=streams),))
            )
        capped = replace(scene, users=(replace(u, total_cap_kbps=1),))
        self.assertEqual(
            Services(self.c).solver().solve(capped).solution_status, "INFEASIBLE_HARD"
        )

    def test_non_gbr_repair_and_directional_price(self):
        from game_solving.optimization.coordinator import ResourceCoordinator
        from game_solving.optimization.solver import Solver

        c, services, scene = self.golden_scene(n=2, target=3.0)
        users = (scene.users[0], replace(scene.users[1], profile="demo_gbr"))
        scene = replace(scene, users=users, capacity=Bandwidth(2800, 6000))
        pools = [
            [
                services.policy.make_action(u, Bandwidth(800, 2000)),
                services.policy.make_action(u, Bandwidth(2000, 2000), anchor=True),
            ]
            for u in users
        ]
        repaired, _ = ResourceCoordinator(c, services.policy).coordinate(
            scene,
            [pool[1] for pool in pools],
            pools,
            Budget(10000, 1000),
            "NORMAL",
            [0, 0],
        )
        self.assertEqual(repaired[0].bandwidth.ul, 800)
        self.assertEqual(repaired[1].bandwidth.ul, 2000)

        class FixedCandidates:
            def build(self, user, budget, required=()):
                return pools[0] if user.user_id == users[0].user_id else pools[1]

            def scope(self, user, pool, round_index):
                return pool

        c["solver"]["lambda_initial"] = [0.0, 0.0]
        c["solver"]["max_iterations"] = 1
        result = Solver(c, services.policy, candidate_provider=FixedCandidates()).solve(
            scene
        )
        self.assertEqual(result.trace[0]["raw"]["ul"], 4000)
        self.assertGreater(result.trace[0]["next_prices"][0], 0)
        self.assertEqual(result.trace[0]["next_prices"][1], 0)

    def test_joint_distribution(self):
        c = load_config(
            overrides={
                "num_scenes": 1,
                "population": {
                    "users_per_cell": 3,
                    "distribution_mode": "joint",
                    "joint_rows": [
                        {
                            "package": "vip",
                            "business": "game",
                            "position": "far",
                            "tolerance": "low",
                            "probability": 1.0,
                        }
                    ],
                },
            }
        )
        scenes, _, report = Services(c).generator().generate()
        self.assertEqual(report["failed"], 0)
        self.assertTrue(
            all(u.package == "vip" and u.tolerance == "low" for u in scenes[0].users)
        )
