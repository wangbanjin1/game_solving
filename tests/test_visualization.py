import json
import re
import tempfile
import unittest
from pathlib import Path
from game_solving.visualization import generate_report


class VisualizationTests(unittest.TestCase):
    def test_offline_report_and_output_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            record = {"scene_id": "</script><img src=x>", "stop_reason": "TIME_BUDGET", "decisions": [], "trace": []}
            (folder / "solve_results.jsonl").write_text(json.dumps(record), encoding="utf-8")
            output = generate_report(folder, folder / "report.html")
            text = output.read_text(encoding="utf-8")
            raw = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', text, re.S).group(1)
            self.assertNotIn("<", raw)
            scene = json.loads(raw)["scenes"][0]
            self.assertEqual(scene["id"], record["scene_id"])
            self.assertEqual(scene["status"], "FAILED")
            self.assertIsNone(scene["capacity"]["ul"])
            self.assertIsNone(scene["comparison"])
            self.assertNotIn('<script src=', text)
            with self.assertRaisesRegex(ValueError, "OUTPUT_EXISTS"):
                generate_report(folder, output)

    def test_empty_and_duplicate_inputs_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / "solve_results.jsonl"
            source.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "EMPTY_RESULTS"):
                generate_report(folder, folder / "report.html")
            source.write_text('{"scene_id":"a"}\n{"scene_id":"a"}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "DUPLICATE_SCENE"):
                generate_report(folder, folder / "report.html")
