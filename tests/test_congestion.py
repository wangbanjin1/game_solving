import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from game_solving.application.services import Services
from game_solving.application.pipeline import Pipeline
from game_solving.domain.entities import Action, Bandwidth, Scene
from game_solving.infrastructure.config import load_config
from game_solving.evaluation.reference import evaluate_reference
from game_solving.evaluation.validation import check_actions
from game_solving.evaluation.history import validate_history
from game_solving.optimization.budget import Budget
from game_solving.optimization.coordinator import ResourceCoordinator
from game_solving.optimization.prices import update_prices
from game_solving.simulation.population import generate_people


class CongestionTests(unittest.TestCase):
    def test_iperf_reference_guides_initial_sampling_not_hard_floor_or_capacity(self):
        for users in (4, 8):
            c = load_config("configs/congestion_exact.json", {
                "population": {"users_per_cell": users},
                "generation": {"quota_mode": "reachable", "require_target_reachable": False,
                    "history": {"enabled": False},
                    "high_load_sampling": {"probability": 1.0, "business_min_fraction": {"shortvideo": 0.125}}}})
            svc = Services(c)
            scenes, audits, report = svc.generator().generate()
            self.assertEqual(report["failed"], 0)
            self.assertEqual(report["high_load_samples"], users * 5)
            for ratio, scene in zip(c["generation"]["headroom_ratios"], scenes):
                self.assertEqual(len(scene.users), users)
                self.assertAlmostEqual(scene.capacity.dl, sum(u.current.dl for u in scene.users) * (1 + ratio))
                self.assertAlmostEqual(scene.capacity.ul, users * 100 * (1 + ratio))
                for u in scene.users:
                    self.assertGreaterEqual(u.current.dl, 3000)
                    media = next(m for m in c["businesses"][u.business]["media"] if m["resolution"] == u.streams["dl"].resolution)
                    self.assertEqual(u.streams["dl"].min_kbps, media["min_kbps"])
                    self.assertLessEqual(u.current.dl, u.streams["dl"].max_kbps)
                    self.assertIsNotNone(svc.policy.make_action(u, Bandwidth(100, u.streams["dl"].min_kbps)))

    def test_reference_does_not_rescale_regular_users(self):
        original = self.services.generator().generate()[0]
        c = load_config("configs/congestion_exact.json", {"generation": {
            "throughput_reference": {"aggregate_ul_kbps": 240000, "aggregate_dl_kbps": 240000}}})
        changed = Services(c).generator().generate()[0]
        self.assertEqual([s.users for s in original], [s.users for s in changed])
        self.assertEqual([s.capacity for s in original], [s.capacity for s in changed])

    def setUp(self):
        self.config = load_config("configs/congestion_exact.json")
        self.services = Services(self.config)

    def test_paired_capacity_keeps_users_history_and_offsets(self):
        c = load_config("configs/congestion_exact.json", {"cell": {"reserve_dl_kbps": 17, "unmanaged_ul_kbps": 23}})
        svc = Services(c)
        scenes, audits, report = svc.generator().generate()
        self.assertEqual((report["requested"], report["successful"], report["failed"]), (5, 5, 0))
        base = scenes[0]
        for ratio, scene, audit in zip(c["generation"]["headroom_ratios"], scenes, audits):
            self.assertEqual(scene.users, base.users)
            self.assertEqual(audit["base_scene_id"], "scene_000000")
            for d in ("ul", "dl"):
                self.assertAlmostEqual(getattr(scene.available, d), sum(getattr(u.current, d) for u in scene.users) * (1 + ratio))
            self.assertEqual(validate_history(scene, audit["history"], svc.policy), 20)

    def test_qoe_counts_keep_original_categories(self):
        c = load_config("configs/congestion_qoe.json")
        users = generate_people(c, 0)
        counts = {category: sum(u["qoe_category"] == category for u in users) for category in c["population"]["qoe_counts"]}
        self.assertEqual(sum(counts.values()), 100)
        self.assertGreaterEqual(counts["qoe_openlive"], 2)
        self.assertNotIn("sta", counts)
        self.assertNotIn("sa", counts)
        self.assertTrue(all(u["business"] not in ("browsing", "download") for u in users))
        self.assertEqual(sum(c["population"]["qoe_counts"].values()), 594370)
        self.assertEqual(sum(u["package"] == "normal" for u in users), 60)
        self.assertEqual(users, generate_people(c, 0))

    def test_conditional_business_and_quota_distribution(self):
        c = load_config("configs/congestion_exact.json", {
            "population": {"users_per_cell": 10, "business_probs_by_package": {"normal": {"shortvideo": 1.0}, "vip": {"video": 1.0}}},
            "generation": {"compliance_probs_by_package": {"normal": {"met": 1.0}, "vip": {"unmet": 1.0}}}})
        users = generate_people(c, 0)
        self.assertTrue(all(u["business"] == "video" for u in users if u["package"] == "vip"))
        scenes, audits, report = Services(c).generator().generate()
        self.assertEqual(report["failed"], 0)
        for u, row in zip(scenes[0].users, audits[0]["users"]):
            if u.package == "normal":
                self.assertEqual(row["realized_band"], "met")
            if u.package == "vip":
                self.assertEqual(row["realized_band"], "unmet")

    def test_ordered_weight_and_components(self):
        c = load_config("configs/congestion_qoe.json", {"num_scenes": 1})
        services = Services(c)
        scene = services.generator().generate()[0][0]
        u = scene.users[0]
        high = replace(u, business="download", package="super_vip", position="near", tolerance="high")
        low = replace(u, business="meeting", package="normal", position="far", tolerance="low")
        self.assertGreater(services.policy.weight(high), services.policy.weight(low))
        a = services.policy.make_action(u, u.current)
        parts = services.policy.components(u, a)
        self.assertAlmostEqual(parts["experience_benefit"] + parts["fairness_compensation"] - parts["stability_cost"], a.h)

    def test_price_damping_sign_and_bounds(self):
        c = self.config
        before = [.1, .1]
        after, signal = update_prices(before, Bandwidth(200, 0), Bandwidth(100, 100), 0, [0, 0], c)
        self.assertGreater(after[0], before[0])
        self.assertLess(after[1], before[1])
        self.assertTrue(all(abs(a - b) <= c["solver"]["price_max_step"] for a, b in zip(after, before)))
        equal, _ = update_prices(before, Bandwidth(100, 100), Bandwidth(100, 100), 0, [0, 0], c)
        self.assertEqual(equal, before)
        c["solver"]["price_update"] = "monotone"
        monotone, _ = update_prices(before, Bandwidth(0, 0), Bandwidth(100, 100), 0, [0, 0], c)
        self.assertEqual(monotone, before)

    def test_two_donors_unlock_receiver_gain(self):
        c = self.config
        c["solver"]["exchange_pairs_per_iteration"] = 100
        users = self.services.generator().generate()[0][0].users[:3]
        def action(i, rate, h):
            return Action(users[i].user_id, f"{i}-{rate}", Bandwidth(rate, rate), {}, None, h, h, 0, True, True, 0)
        a0, a1 = action(0, 3, 3), action(0, 2, 2.5)
        b0, b1 = action(1, 3, 3), action(1, 2, 2.5)
        c0, c1 = action(2, 1, 1), action(2, 3, 4)
        scene = Scene("exchange", "test", Bandwidth(7, 7), users)
        coordinator = ResourceCoordinator(c, self.services.policy)
        result, _ = coordinator.coordinate(scene, [a0, b0, c0], [[a0, a1], [b0, b1], [c0, c1]], Budget(10000, 1000), "UTILITY", [.1, .1])
        self.assertEqual(result, [a1, b1, c1])
        self.assertEqual(sum(a.h for a in result), 9)
        self.assertTrue(any(e["reason"] == "net_utility_exchange" for e in coordinator.events))

    def test_reference_witness_and_best_history(self):
        scene = self.services.generator().generate()[0][-1]
        result = self.services.solver().solve(scene)
        self.assertEqual(result.mode, "UTILITY")
        self.assertEqual(check_actions(scene, result.decisions, self.services.policy, recompute=True), [])
        h = sum(a.h for a in result.decisions)
        self.assertGreaterEqual(h + 1e-9, max(row["H"] for row in result.trace))
        self.assertEqual(result.returned_matches_terminal, [a.action_id for a in result.decisions] == [a.action_id for a in result.terminal_decisions])
        self.assertTrue(all(len(row["users"]) == 4 for row in result.trace))
        reference = evaluate_reference(scene, result, self.config, self.services.policy)
        self.assertEqual(reference["status"], "exact_discrete")
        self.assertTrue(reference["complete"])
        self.assertGreaterEqual(reference["H_reference"] + 1e-9, h)
        decisions = [self.services.policy.make_action(u, Bandwidth(**row["bandwidth"])) for u, row in zip(scene.users, reference["policy_decisions"])]
        self.assertEqual(check_actions(scene, decisions, self.services.policy), [])
        self.assertAlmostEqual(sum(a.h for a in decisions), reference["H_reference"])
        c = copy.deepcopy(self.config)
        c["reference"]["max_nodes"] = 1
        partial = evaluate_reference(scene, result, c, self.services.policy)
        self.assertFalse(partial["complete"])
        self.assertEqual(partial["status"], "best_known")
        self.assertTrue(partial["warm_started_from_algorithm"])

    def test_new_config_guards(self):
        for overrides in (
            {"generation": {"headroom_ratios": [-.1]}},
            {"generation": {"headroom_ratios": [.1, .1]}},
            {"policy": {"weight_dimensions": ["package"]}},
            {"solver": {"exchange_max_donors": 3}},
            {"solver": {"price_smoothing": 0}},
            {"population": {"qoe_counts": {"sa": 10}}},
            {"population": {"qoe_counts": {"sa": 10}, "qoe_business_mapping": {"sa": {"browsing": 1}}}},
            {"population": {"qoe_counts": {"qoe_video": 10}, "qoe_business_mapping": {"qoe_video": {"browsing": 1}}}},
            {"population": {"minimum_qoe_counts": {"qoe_openlive": 1}}},
            {"population": {"users_per_cell": 1, "qoe_counts": {"qoe_video": 10}, "minimum_qoe_counts": {"qoe_video": 2}, "qoe_business_mapping": {"qoe_video": {"video": 1.0}}}},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                load_config(overrides=overrides)

    def test_fixed_quota_anchor_does_not_duplicate_floor_or_block_convergence(self):
        c = load_config("configs/congestion_exact.json", {
            "population": {"business_probs": {"browsing": 1.0}},
            "generation": {"headroom_ratios": [0.0], "non_key_current_multiplier": [1.0, 1.0]}})
        svc = Services(c)
        scene = svc.generator().generate()[0][0]
        result = svc.solver().solve(scene)
        self.assertEqual(result.stop_reason, "CONVERGED_LOCAL")
        self.assertLess(result.iterations, 50)
        self.assertTrue(all(u["candidate_count"] == 1 for row in result.trace for u in row["users"]))

    def test_serialized_detail_trace_and_standalone_solve(self):
        c = load_config("configs/congestion_exact.json", {"generation": {"headroom_ratios": [0.0]}})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(Pipeline(c).execute("generate", root / "data")[0], 0)
            self.assertEqual(Pipeline(c).execute("solve", root / "solve", root / "data" / "solver_inputs.jsonl")[0], 0)
            read = lambda name: [json.loads(row) for row in (root / "solve" / name).read_text(encoding="utf-8").splitlines()]
            details = read("iteration_trace.jsonl")
            results = read("solve_results.jsonl")
            self.assertIn("users", details[0])
            self.assertNotIn("users", results[0]["trace"][0])
            self.assertEqual(len(details), results[0]["iterations"])
            self.assertTrue(read("reference_results.jsonl")[0]["policy_witness_validated"])
            for row in details:
                self.assertAlmostEqual(row["H"], sum(u["allocated"]["H"] for u in row["users"]))
                for u in row["users"]:
                    candidates = u["candidates"]
                    self.assertEqual(len(candidates), u["candidate_count"])
                    selected = max(candidates, key=lambda a: (a["selection_score"], a["anchor"], -a["bandwidth"]["ul"] - a["bandwidth"]["dl"], a["action_id"]))
                    self.assertEqual(selected["action_id"], u["requested"]["action_id"])
                    for a in candidates:
                        self.assertAlmostEqual(a["selection_score"], a["h"] - a["shadow_cost"])
            from game_solving.visualization import generate_report
            import re
            html = generate_report(root / "solve", root / "report.html").read_text(encoding="utf-8")
            payload = json.loads(re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S).group(1))
            saved = payload["scenes"][0]
            self.assertEqual(saved["detail_trace"], details)
            self.assertEqual(saved["reference"]["policy_decisions"], read("reference_results.jsonl")[0]["policy_decisions"])
            self.assertEqual(saved["decisions"], results[0]["decisions"])
            self.assertEqual(read("initial_distribution.jsonl")[0]["groups"]["all"]["users"], 4)
