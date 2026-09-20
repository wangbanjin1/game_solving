import importlib.util
import csv
from pathlib import Path
import unittest

from game_solving.application.services import Services
from game_solving.domain.entities import Stream
from game_solving.infrastructure.config import load_config


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "kqi_mos_lookup" / "generate_mos_kqi_relation.py"
SPEC = importlib.util.spec_from_file_location("generate_mos_kqi_relation", SCRIPT)
LOOKUP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LOOKUP)


class KqiMosLookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = LOOKUP.generate_forward(load_config())
        LOOKUP.validate_forward(cls.rows)

    def test_watchlive_copies_openlive_kqi_track_but_uses_downlink(self):
        self.assertEqual(LOOKUP.SPECS["watchlive"]["direction"], "DL")
        self.assertEqual(LOOKUP.SPECS["watchlive"]["bands"], LOOKUP.SPECS["openlive"]["bands"])
        self.assertEqual(LOOKUP.SPECS["watchlive"]["res"], LOOKUP.SPECS["openlive"]["res"])
        self.assertEqual(LOOKUP.SPECS["watchlive"]["rtt"], LOOKUP.SPECS["openlive"]["rtt"])

    def test_video_and_shortvideo_use_supplied_bitrate_bands(self):
        expected = ((0.0, 1.0, "差"), (1.0, 5.0, "一般"), (5.0, 12.0, "好"))
        self.assertEqual(LOOKUP.SPECS["video"]["bands"], expected)
        self.assertEqual(LOOKUP.SPECS["shortvideo"]["bands"], expected)

    def test_shortvideo_has_no_resolution_and_uses_bitrate_model(self):
        config = load_config()
        rows = [row for row in self.rows if row["business"] == "shortvideo" and row["legal_action"]]
        self.assertTrue(rows)
        self.assertEqual(config["businesses"]["shortvideo"]["quality_model"], "bitrate")
        self.assertTrue(all(row["resolution_ref"] == 0 for row in rows))
        self.assertEqual(min(row["bandwidth_mbps"] for row in rows), 0.2)
        self.assertEqual(max(row["bandwidth_mbps"] for row in rows), 12.0)

    def test_game_uses_point_one_to_one_mbps_and_point_three_is_not_poor(self):
        rows = [row for row in self.rows if row["business"] == "game" and row["legal_action"]]
        self.assertEqual([row["bandwidth_mbps"] for row in rows], [step / 10 for step in range(1, 11)])
        self.assertTrue(all(row["kqi_grade"] == "差" for row in rows if row["bandwidth_mbps"] < 0.3))
        self.assertEqual(next(row for row in rows if row["bandwidth_mbps"] == 0.3)["kqi_grade"], "一般")
        self.assertEqual(rows[-1]["bandwidth_mbps"], 1.0)
        self.assertTrue(all(a["mos_ref"] <= b["mos_ref"] for a, b in zip(rows, rows[1:])))

    def test_runtime_model_uses_forward_and_reverse_lookup_tables(self):
        config = load_config(overrides={"models": {"name": "lookup_table_v1"}})
        model = Services(config).model
        stream = Stream(1000, 360, 640, 360, 999, 0.9, 0.9, 200, 12000, 9000, "steady")
        row = model.lookup_row("video", stream)
        self.assertAlmostEqual(model.forward("video", stream), row["mos"])
        projected = model.project_stream("video", stream, 1000)
        self.assertAlmostEqual(projected.rtt_ms, row["rtt_ms"])
        self.assertAlmostEqual(projected.loss_ratio, row["loss_ratio"])
        inverse = model.inverse("video", 4.0, stream)
        self.assertTrue(inverse.feasible)
        choice_path = Path(config["models"]["lookup_directory"]) / "mos_choice_typical" / "video.csv"
        with choice_path.open(encoding="utf-8-sig", newline="") as handle:
            expected = next(r for r in csv.DictReader(handle) if r["阶段"] == "steady" and r["目标MOS"] == "4.0")
        self.assertEqual(inverse.bandwidth_kbps, float(expected["带宽Mbps"]) * 1000)

    def test_runtime_shortvideo_reports_missing_resolution(self):
        config = load_config(overrides={"models": {"name": "lookup_table_v1"}})
        services = Services(config)
        stream = Stream(1000, 720, 1280, 720, 100, 0.01, 0.1, 200, 12000, None, "steady")
        projected = services.model.project_stream("shortvideo", stream, 1000)
        self.assertEqual(projected.resolution, 0)
        self.assertFalse(services.model.resolution_available("shortvideo"))

    def test_runtime_game_lookup_has_variable_bandwidth(self):
        config = load_config(overrides={"models": {"name": "lookup_table_v1"}})
        model = Services(config).model
        self.assertEqual(model.rate_bounds("game"), (100.0, 1000.0))
        stream = Stream(300, 0, 0, 0, 999, 0.9, 0.9, 100, 1000, None, "steady", 50)
        projected = model.project_stream("game", stream, 300)
        self.assertEqual(projected.bitrate_kbps, 300)
        self.assertLess(projected.rtt_ms, stream.rtt_ms)


if __name__ == "__main__":
    unittest.main()
