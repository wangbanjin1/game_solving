import unittest
from types import SimpleNamespace

from game_solving.application.services import Services
from game_solving.domain.entities import Bandwidth
from game_solving.infrastructure.config import load_config
from game_solving.optimization.feasibility import minimum_action
from game_solving.simulation.population import generate_people


class AppGroupingTests(unittest.TestCase):
    def test_vip_game_guarantee_requires_point_three_mbps_in_both_directions(self):
        config = load_config()
        policy = Services(config).policy
        user = SimpleNamespace(package="vip", app_id="hepingjingying_game")
        kqi = {"ul": {"avg_qoe": 100, "service_delay_ms": 0, "stalling_duration_seconds_proxy": 0},
               "dl": {"avg_qoe": 100, "service_delay_ms": 0, "stalling_duration_seconds_proxy": 0}}
        met, errors = policy.quality_guarantee(user, Bandwidth(299, 300), {}, kqi)
        self.assertFalse(met)
        self.assertIn("BANDWIDTH_UL", errors)
        met, errors = policy.quality_guarantee(user, Bandwidth(300, 299), {}, kqi)
        self.assertFalse(met)
        self.assertIn("BANDWIDTH_DL", errors)
        self.assertEqual(policy.quality_guarantee(user, Bandwidth(300, 300), {}, kqi), (True, ()))

    def test_minimum_app_coverage_within_business(self):
        config = load_config(overrides={
            "population": {
                "users_per_cell": 20,
                "minimum_app_coverage": True,
                "business_probs": {"shortvideo": 1.0},
            }
        })
        people = generate_people(config, 0)
        apps = {p["app_id"] for p in people}
        expected = {
            app_id for app_id, rule in config["applications"].items()
            if rule["business"] == "shortvideo"
        }
        self.assertEqual(apps, expected)
        self.assertEqual(config["policy"]["package_order"], ["vip", "normal"])

    def test_normal_users_have_no_target_or_quality_evaluation(self):
        config = load_config(overrides={
            "num_scenes": 1,
            "population": {"users_per_cell": 2, "package_probs": {"normal": 1.0}, "business_probs": {"shortvideo": 1.0}},
            "generation": {"capacity_mode": "current_headroom", "headroom_ratios": [0.0], "require_target_reachable": False},
        })
        services = Services(config)
        scene = services.generator().generate()[0][0]
        action = services.policy.make_action(scene.users[0], scene.users[0].current)
        self.assertFalse(action.target_evaluated)
        self.assertTrue(action.target_met)
        self.assertTrue(action.quality_guarantee_met)
        self.assertEqual(action.quality_violations, ())

    def test_group_round_and_kqi_change_are_traced(self):
        config = load_config(overrides={
            "num_scenes": 1,
            "population": {
                "users_per_cell": 4,
                "package_probs": {"vip": 1.0},
                "business_probs": {"shortvideo": 1.0},
            },
            "generation": {
                "capacity_mode": "current_headroom",
                "headroom_ratios": [0.0],
                "quota_mode": "reachable",
                "require_target_reachable": False,
            },
            "policy": {"objective": "vip_guarantee"},
            "solver": {
                "max_iterations": 2,
                "stable_rounds": 2,
                "trace_users": True,
                "group_start_iteration": 2,
            },
        })
        services = Services(config)
        scene = services.generator().generate()[0][0]
        result = services.solver().solve(scene)
        self.assertEqual(result.trace[0]["decision_mode"], "individual_alignment")
        self.assertEqual(result.trace[1]["decision_mode"], "grouped_common_mos")
        user = result.trace[1]["users"][0]
        self.assertIn("KQI", user["allocated"])
        self.assertIn("kqi_change_from_initial", user)
        self.assertIn("quality_guarantee_met", user["allocated"])

    def test_bandwidth_drives_resolution_delay_loss_and_stall(self):
        config = load_config(overrides={
            "num_scenes": 1,
            "population": {"users_per_cell": 4, "business_probs": {"shortvideo": 1.0}},
            "generation": {"capacity_mode": "current_headroom", "headroom_ratios": [0.0], "require_target_reachable": False},
            "kqi_response": {"enabled": True, "resolution_adaptation": True},
        })
        services = Services(config)
        scene = services.generator().generate()[0][0]
        user = next(u for u in scene.users if u.current.dl < 20000)
        initial = services.policy.make_action(user, user.current)
        upgraded = services.policy.make_action(user, Bandwidth(user.current.ul, 20000))
        before, after = initial.predicted_kqi["dl"], upgraded.predicted_kqi["dl"]
        self.assertGreaterEqual(after["resolution"], before["resolution"])
        self.assertLess(after["service_delay_ms"], before["service_delay_ms"])
        self.assertLess(after["loss_ratio"], before["loss_ratio"])
        self.assertLess(after["stall_ratio"], before["stall_ratio"])

    def test_avg_qoe_uses_twenty_to_one_hundred_scale(self):
        config = load_config(overrides={
            "num_scenes": 1,
            "population": {
                "users_per_cell": 2,
                "package_probs": {"vip": 1.0},
                "business_probs": {"shortvideo": 1.0},
            },
            "generation": {
                "capacity_mode": "current_headroom",
                "headroom_ratios": [0.0],
                "require_target_reachable": False,
            },
        })
        services = Services(config)
        scene = services.generator().generate()[0][0]
        user = scene.users[0]
        action = services.policy.make_action(user, user.current)
        for direction, mos in action.direction_mos.items():
            self.assertAlmostEqual(
                action.predicted_kqi[direction]["avg_qoe"], mos * 20.0
            )

    def test_dynamic_kqi_reachability_agrees_with_maximum_legal_action(self):
        config = load_config(overrides={
            "num_scenes": 1,
            "population": {
                "users_per_cell": 12,
                "package_probs": {"vip": 1.0},
                "business_probs": {"shortvideo": 1.0},
            },
            "generation": {
                "capacity_mode": "current_headroom",
                "headroom_ratios": [0.0],
                "require_target_reachable": False,
            },
            "kqi_response": {"enabled": True, "resolution_adaptation": True},
        })
        services = Services(config)
        scene = services.generator().generate()[0][0]
        media_max = max(m["max_kbps"] for m in config["businesses"]["shortvideo"]["media"])
        for user in scene.users:
            target, _ = minimum_action(user, "target", services.policy)
            maximum = services.policy.make_action(
                user, Bandwidth(user.current.ul, media_max)
            )
            self.assertEqual(target is not None, maximum.target_met)


if __name__ == "__main__":
    unittest.main()
