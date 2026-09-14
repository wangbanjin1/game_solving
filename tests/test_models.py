import math
import unittest
from dataclasses import replace
from game_solving.infrastructure.config import load_config
from game_solving.models.mos import MosModel
from game_solving.domain.entities import Stream


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.c = load_config()
        self.m = MosModel(self.c)
        self.s = Stream(2000, 720, 1280, 720, 60, 0.001, 0.01, 800, 6000)

    def test_meeting_golden_forward_inverse(self):
        self.assertAlmostEqual(
            self.m.forward("meeting", self.s), 3.938857211173444, places=10
        )
        inv = self.m.inverse("meeting", 4, self.s)
        self.assertTrue(inv.feasible)
        self.assertEqual(inv.bandwidth_kbps, 2148)
        self.assertAlmostEqual(inv.mos, 4.0003442163288, places=10)
        self.assertFalse(self.m.inverse("meeting", 4.5, self.s).feasible)

    def test_lower_bound_and_quantization(self):
        inv = self.m.inverse("meeting", 1, self.s)
        self.assertEqual(inv.bandwidth_kbps, 800)
        previous = 0
        for target in (3.1, 3.3, 3.5, 3.7, 4.0):
            a = self.m.inverse("meeting", target, self.s)
            self.assertTrue(a.feasible)
            self.assertGreaterEqual(a.bandwidth_kbps, previous)
            self.assertGreaterEqual(a.mos + 1e-6, target)
            previous = a.bandwidth_kbps
            if a.bandwidth_kbps > 800:
                self.assertLess(
                    self.m.forward(
                        "meeting", replace(self.s, bitrate_kbps=a.bandwidth_kbps - 1)
                    ),
                    target,
                )

    def test_business_formulas_independent_expansion(self):
        coefficients = {
            "openlive": (0.25, 0.05, 0.25, 0.1, "A"),
            "watchlive": (0.25, 0.05, 0.25, 0.1, "A"),
            "shortvideo": (0, 0, 0.04, 0.25, "A"),
            "video": (0.04, 0.25, 0.04, 0.25, "A"),
            "meeting": (0.25, 0.05, 0.05, 0.25, "A"),
            "cloudgame": (0.25, 0.05, 0.05, 0.25, "B"),
            "game": (0, 0, 0.25, 0.04, "B"),
        }
        for name, (w1, w2, g1, g2, kind) in coefficients.items():
            sb = 5 / (1 + math.exp(-2000 / 928.984))
            sr = 5 / (1 + math.exp(-720 / 410))
            q = (
                4.5
                if name == "game"
                else (
                    sb
                    if name == "shortvideo"
                    else max(1, min(5, 5 - 4 * w1 * (5 - sb) - 4 * w2 * (5 - sr)))
                )
            )
            i = 1 + 4 * math.exp(-0.0035 * 60)
            v = max(
                1,
                min(
                    5,
                    5
                    - 4 * g1 * (5 - (1 + 4 * math.exp(-180.94 * 0.001)))
                    - 4 * g2 * (5 - (5 - 4 * 0.01)),
                ),
            )
            a = 0.1 * (1 + 2 * math.exp(-i / 2))
            b = 0.1 * (1 + 2 * math.exp(-v / 2))
            f = (
                (a * (i - 1) + b * (v - 1)) / (4 * (a + b))
                if kind == "A"
                else 1 - a * (5 - i) - b * (5 - v)
            )
            expected = max(1, min(5, 1 + (q - 1) * f))
            self.assertAlmostEqual(self.m.forward(name, self.s), expected, places=12)

    def test_call_formula_independent(self):
        P = 1280 * 720
        D = 0.0975 * 30**1.2667 * P**0.3177
        sb = 1 + 4.1192 - 4.1192 / (1 + (2000 / D) ** 2.1276)
        sr = 1 - 0.6571 + 0.6571 / (1 + (P / 232000) ** -1.295)
        q = max(1, min(5, sb * sr))
        i = max(1, min(5, 1 + 3.615 - 3.615 / (1 + (60 / 396.6 + 0.256) ** -2.016)))
        sl = max(1, min(5, 5 * math.exp(-100 * 0.001 / 1.383)))
        v = max(1, min(5, 5 - 4 * 0.15 * (5 - sl) - 4 * 0.15 * (5 - 4.96)))
        a = 0.1 * (1 + 2 * math.exp(-i / 2))
        b = 0.1 * (1 + 2 * math.exp(-v / 2))
        self.assertAlmostEqual(
            self.m.forward("voip", self.s),
            max(1, min(5, 1 + (q - 1) * (1 - a * (5 - i) - b * (5 - v)))),
            places=12,
        )

    def test_every_media_business_roundtrip(self):
        for business in (
            "openlive",
            "watchlive",
            "shortvideo",
            "video",
            "meeting",
            "cloudgame",
            "voip",
        ):
            original = self.m.forward(business, self.s)
            inv = self.m.inverse(business, original, self.s)
            self.assertTrue(inv.feasible, business)
            self.assertLessEqual(inv.bandwidth_kbps, 2001)
            self.assertGreaterEqual(inv.mos + 1e-6, original)

    def test_phase_fixed_and_game(self):
        initial = replace(self.s, phase="initial", buffer_ms=5000)
        self.assertNotEqual(
            self.m.forward("video", initial), self.m.forward("video", self.s)
        )
        with self.assertRaisesRegex(ValueError, "INITIAL_BUFFER"):
            self.m.forward("meeting", initial)
        self.assertEqual(
            self.m.forward("game", self.s),
            self.m.forward("game", replace(self.s, bitrate_kbps=3000)),
        )
        self.assertFalse(self.m.inverse("game", 4, self.s).feasible)

    def test_invalid_kqi(self):
        for val in (-1, 2, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.m.forward("meeting", replace(self.s, loss_ratio=val))
        with self.assertRaises(ValueError):
            self.m.forward("voip", replace(self.s, width=0))

    def test_monotonic_and_unreachable_bad_environment(self):
        values = [
            self.m.forward("meeting", replace(self.s, bitrate_kbps=b))
            for b in (800, 1000, 2000, 4000, 6000)
        ]
        self.assertEqual(values, sorted(values))
        bad = replace(self.s, rtt_ms=300, loss_ratio=0.05, stall_ratio=1)
        self.assertFalse(self.m.inverse("cloudgame", 4, bad).feasible)
