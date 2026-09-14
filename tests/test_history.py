import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from game_solving.application.pipeline import Pipeline
from game_solving.application.services import Services
from game_solving.domain.entities import Bandwidth, Scene, Stream, User, scene_from_dict
from game_solving.evaluation.history import validate_history
from game_solving.evaluation.validation import check_actions
from game_solving.infrastructure.config import load_config, model_hash
from game_solving.infrastructure.json_store import read_jsonl
from game_solving.optimization.budget import Budget, BudgetExceeded
from game_solving.optimization.coordinator import ResourceCoordinator
from game_solving.simulation.history import HistoryGenerator


class HistoryTests(unittest.TestCase):
    def config(self, **history):
        return load_config("configs/tiny.json", {
            "generation": {"history": {"enabled": True, **history}}
        })

    def pair(self, config):
        services = Services(config)
        stream = Stream(2000, 720, 1280, 720, 60, 0.001, 0.01, 800, 6000)
        users = tuple(User(
            user_id=name, business="meeting", package="vip", position="near",
            tolerance="low", profile="demo_non_gbr", current=Bandwidth(2000, 2000),
            streams={"ul": stream, "dl": stream},
            observed_mos=services.model.forward("meeting", stream), target=4, baseline=4,
        ) for name in ("A", "B"))
        return services, Scene("pair", model_hash(config), Bandwidth(5500, 5500), users)

    def test_configuration_guards(self):
        for history in (
            {"enabled": "yes"}, {"periods": 0}, {"periods": True},
            {"interval_seconds": 0}, {"interval_seconds": float("inf")},
            {"bandwidth_multiplier": []}, {"bandwidth_multiplier": [2, 1]},
            {"bandwidth_multiplier": [-0.1, 1]}, {"bandwidth_multiplier": [0, 0]},
        ):
            with self.subTest(history=history), self.assertRaises(ValueError):
                self.config(**history)
        with self.assertRaisesRegex(ValueError, "POSITIVE_HISTORY_WINDOW"):
            load_config(overrides={
                "generation": {"history": {"enabled": True}},
                "utility": {"history_window_seconds": 0},
            })

    def test_reproducibility_and_current_state_isolation(self):
        config = self.config()
        scenes, audits, report = Services(config).generator().generate()
        repeated = Services(config).generator().generate()
        self.assertEqual((scenes, audits, report), repeated)
        old_scenes, old_audits, _ = Services(load_config("configs/tiny.json")).generator().generate()
        self.assertEqual(report["failed"], 0)
        self.assertTrue(all(u.history_mos is not None for s in scenes for u in s.users))
        self.assertEqual(
            [replace(s, users=tuple(replace(u, history_mos=None) for u in s.users)) for s in scenes],
            old_scenes,
        )
        self.assertTrue(all("history" not in a for a in old_audits))
        for scene, audit in zip(scenes, audits):
            self.assertEqual(validate_history(scene, audit["history"], Services(config).policy), 15)

    def test_window_clips_partial_period(self):
        config = self.config()
        config["utility"]["history_window_seconds"] = 90
        services, scene = self.pair(config)
        enriched, audit = HistoryGenerator(config, services.policy).generate(scene, 0, Budget(10000, 1000))
        for i, user in enumerate(enriched.users):
            expected = (
                audit["frames"][-2]["users"][i]["mos"] * 30
                + audit["frames"][-1]["users"][i]["mos"] * 60
            ) / 90
            self.assertAlmostEqual(user.history_mos, expected)
        self.assertEqual(validate_history(enriched, audit, services.policy), 10)

    def test_capacities_caps_contracts_and_fixed_users(self):
        config = self.config(periods=12, bandwidth_multiplier=[0.2, 3.0])
        services, scene = self.pair(config)
        adaptive = replace(
            scene.users[0], baseline=3.5, hard_mos=True,
            contract=Bandwidth(1000, 1000), cap=Bandwidth(2500, 2500), total_cap_kbps=4500,
        )
        fixed = replace(scene.users[1], bitrate_adaptation=False)
        browser = replace(
            fixed, user_id="browser", business="browsing", streams={},
            current=Bandwidth(50, 2000), observed_mos=None, contract=Bandwidth(50, 1800),
        )
        game_stream = Stream(100, 0, 0, 0, 30, 0.001, 0.01, 100, 100)
        game = replace(
            fixed, user_id="game", business="game", streams={"session": game_stream},
            current=Bandwidth(100, 100), observed_mos=services.model.forward("game", game_stream),
        )
        scene = replace(scene, capacity=Bandwidth(5000, 7000), users=(adaptive, fixed, browser, game))
        original = copy.deepcopy(scene)
        enriched, audit = HistoryGenerator(config, services.policy).generate(scene, 0, Budget(50000, 1000))
        self.assertEqual(scene, original)
        self.assertIsNone(enriched.users[2].history_mos)
        self.assertEqual(enriched.users[3].history_mos, game.observed_mos)
        self.assertEqual(validate_history(enriched, audit, services.policy), 48)
        varied = set()
        for frame in audit["frames"]:
            records = frame["users"]
            a = records[0]
            self.assertGreaterEqual(a["mos"] + 1e-6, 3.5)
            self.assertLessEqual(sum(a["bandwidth"].values()), 4500)
            self.assertTrue(all(1000 <= b <= 2500 for b in a["bandwidth"].values()))
            varied.add(tuple(a["bandwidth"].values()))
            self.assertEqual(records[1]["bandwidth"], {"ul": 2000, "dl": 2000})
            self.assertEqual(records[2]["bandwidth"], {"ul": 50, "dl": 2000})
            self.assertEqual(records[3]["bandwidth"], {"ul": 100, "dl": 100})
            self.assertLessEqual(sum(r["bandwidth"]["ul"] for r in records), 5000)
            self.assertLessEqual(sum(r["bandwidth"]["dl"] for r in records), 7000)
        self.assertGreater(len(varied), 1)

    def test_generation_budget_is_shared_and_failure_is_reported(self):
        base = load_config("configs/tiny.json", {"num_scenes": 1})
        _, audits, _ = Services(base).generator().generate()
        config = load_config("configs/tiny.json", {
            "num_scenes": 1,
            "generation": {
                "max_model_evaluations": audits[0]["generation_steps"],
                "history": {"enabled": True},
            },
        })
        scenes, _, report = Services(config).generator().generate()
        self.assertEqual(scenes, [])
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["failures"][0]["reason"], "MAX_TOTAL_STEPS")
        services, scene = self.pair(config)
        with self.assertRaises(BudgetExceeded):
            HistoryGenerator(config, services.policy).generate(scene, 0, Budget(1, 1000))
        self.assertTrue(all(u.history_mos is None for u in scene.users))

    def test_serialized_tampering_is_detected(self):
        config = self.config()
        services, scene = self.pair(config)
        scene, audit = HistoryGenerator(config, services.policy).generate(scene, 0, Budget(10000, 1000))
        for field in ("mos", "time", "hash", "users"):
            modified = copy.deepcopy(audit)
            if field == "mos":
                modified["frames"][0]["users"][0]["mos"] += 0.1
            elif field == "time":
                modified["frames"][-1]["end"] = 1
            elif field == "hash":
                modified["model_hash"] = "wrong"
            else:
                modified["frames"][0]["users"].reverse()
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_history(scene, modified, services.policy)
        with self.assertRaisesRegex(ValueError, "HISTORY_MEAN_MISMATCH"):
            validate_history(replace(scene, users=(replace(scene.users[0], history_mos=1), scene.users[1])), audit, services.policy)
        with self.assertRaisesRegex(ValueError, "HISTORY_CONSTRAINT_VIOLATION"):
            validate_history(replace(scene, capacity=Bandwidth(1, 1)), audit, services.policy)

    def test_generated_history_changes_fairness_allocation(self):
        config = self.config(bandwidth_multiplier=[0.4, 0.8])
        services, scene = self.pair(config)
        scene, audit = HistoryGenerator(config, services.policy).generate(scene, 0, Budget(10000, 1000))
        validate_history(scene, audit, services.policy)
        self.assertNotAlmostEqual(scene.users[0].history_mos, scene.users[1].history_mos)
        self.assertTrue(all(u.history_mos < u.target for u in scene.users))
        outcomes = {}
        for swapped in (False, True):
            users = scene.users
            if swapped:
                users = tuple(replace(u, history_mos=scene.users[1-i].history_mos) for i, u in enumerate(users))
            trial = replace(scene, users=users)
            for eta in (0, 0.5):
                current_config = copy.deepcopy(config)
                current_config["utility"]["eta_fair"] = eta
                current = Services(current_config)
                pools = [[current.policy.make_action(u, Bandwidth(b, b)) for b in (2148, 3000)] for u in users]
                actions, _ = ResourceCoordinator(current_config, current.policy).coordinate(
                    trial, [pool[1] for pool in pools], pools, Budget(10000, 1000), "NORMAL", [0.01, 0.01]
                )
                self.assertEqual(check_actions(trial, actions, current.policy, recompute=True), [])
                winner = max(actions, key=lambda a: a.bandwidth.ul).user_id
                outcomes[swapped, eta] = winner
                if eta:
                    self.assertEqual(winner, min(users, key=lambda u: u.history_mos).user_id)
        self.assertEqual(outcomes[False, 0], outcomes[True, 0])
        self.assertNotEqual(outcomes[False, 0.5], outcomes[True, 0.5])

    def test_pipeline_roundtrip_and_standalone_solve(self):
        config = load_config("configs/history.json")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated, solved = root / "generated", root / "solved"
            code, summary = Pipeline(config).execute("run", generated)
            self.assertEqual(code, 0)
            self.assertEqual(summary["successful_scenes"], 2)
            inputs = read_jsonl(generated / "solver_inputs.jsonl")
            histories = read_jsonl(generated / "history.jsonl")
            for row, audit in zip(inputs, histories):
                self.assertEqual(validate_history(scene_from_dict(row), audit, Services(config).policy), 15)
            validation = json.loads((generated / "validation_report.json").read_text(encoding="utf-8"))
            self.assertEqual(validation["history_observations_validated"], 30)
            code, _ = Pipeline(config).execute("solve", solved, generated / "solver_inputs.jsonl")
            self.assertEqual(code, 0)
            self.assertEqual(
                [r["decisions"] for r in read_jsonl(generated / "solve_results.jsonl")],
                [r["decisions"] for r in read_jsonl(solved / "solve_results.jsonl")],
            )
