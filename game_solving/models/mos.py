"""Versioned A/B MOS and video-call formulas. All fitted constants come from config."""

import math
from dataclasses import replace
from game_solving.domain.entities import InverseResult
from game_solving.domain.validation import validate_stream, number


class MosModel:
    def __init__(self, config):
        self.c = config
        self.p = config["models"]
        self.businesses = config["businesses"]

    def clip(self, x):
        return min(self.p["maximum_mos"], max(self.p["minimum_mos"], x))

    def dimensions(self, business, s):
        validate_stream(s)
        p = self.p
        b = self.businesses[business]
        lo = p["minimum_mos"]
        hi = p["maximum_mos"]
        span = p["score_span"]
        w1, w2, g1, g2 = b["coefficients"]
        if b["quality_model"] == "call":
            c = p["call"]
            pixels = s.width * s.height
            if pixels <= 0:
                raise ValueError("PIXEL_DIMENSIONS_REQUIRED")
            D = (
                c["d_coefficient"]
                * c["fps"] ** c["fps_power"]
                * pixels ** c["pixels_power"]
            )
            sb = (
                lo
                + c["bitrate_amplitude"]
                - c["bitrate_amplitude"]
                / (1 + (s.bitrate_kbps / D) ** c["bitrate_power"])
            )
            sr = (
                lo
                - c["resolution_amplitude"]
                + c["resolution_amplitude"]
                / (1 + (pixels / c["resolution_reference"]) ** c["resolution_power"])
            )
            q = self.clip(sb * sr)
            i = self.clip(
                lo
                + c["interaction_amplitude"]
                - c["interaction_amplitude"]
                / (
                    1
                    + (s.rtt_ms / c["interaction_scale"] + c["interaction_offset"])
                    ** c["interaction_power"]
                )
            )
            sl = self.clip(
                hi * math.exp(-c["loss_percent_scale"] * s.loss_ratio / c["loss_scale"])
            )
        else:
            sb = hi / (1 + math.exp(-s.bitrate_kbps / p["bitrate_scale"]))
            sr = hi / (1 + math.exp(-s.resolution / p["resolution_scale"]))
            q = (
                p["game_quality"]
                if b["quality_model"] == "game"
                else (
                    sb
                    if b["quality_model"] == "bitrate"
                    else self.clip(hi - span * w1 * (hi - sb) - span * w2 * (hi - sr))
                )
            )
            if s.phase == "initial" and business in ("video", "meeting"):
                if not b["initial_enabled"] or s.buffer_ms is None:
                    raise ValueError("INITIAL_BUFFER_UNAVAILABLE")
                i = lo + span * math.exp(-p["buffer_decay"] * s.buffer_ms)
            else:
                i = lo + span * math.exp(-p["rtt_decay"] * s.rtt_ms)
            sl = lo + span * math.exp(-p["loss_decay"] * s.loss_ratio)
        ss = hi - span * s.stall_ratio
        v = self.clip(hi - span * g1 * (hi - sl) - span * g2 * (hi - ss))
        alpha = p["dynamic_base"] * (
            1 + p["dynamic_multiplier"] * math.exp(-i / p["dynamic_decay_divisor"])
        )
        beta = p["dynamic_base"] * (
            1 + p["dynamic_multiplier"] * math.exp(-v / p["dynamic_decay_divisor"])
        )
        factor = (
            (alpha * (i - lo) + beta * (v - lo)) / (span * (alpha + beta))
            if b["mos_type"] == "A"
            else 1 - alpha * (hi - i) - beta * (hi - v)
        )
        return {
            "Q": q,
            "I": i,
            "V": v,
            "factor": factor,
            "alpha": alpha,
            "beta": beta,
            "s_r": sr if b["quality_model"] != "game" else None,
        }

    def forward(self, business, stream, budget=None):
        if budget:
            budget.consume(kind="model_forward")
        v = self.dimensions(business, stream)
        return self.clip(
            self.p["minimum_mos"] + (v["Q"] - self.p["minimum_mos"]) * v["factor"]
        )

    def inverse(self, business, target, stream, budget=None):
        if budget:
            budget.consume(kind="model_inverse")
        number(target, "target_mos", self.p["minimum_mos"], self.p["maximum_mos"])
        validate_stream(stream)
        if self.businesses[business]["quality_model"] == "game":
            return InverseResult(False, None, None, "FIXED_QUOTA_NO_INVERSE")
        eps = self.c["solver"]["epsilon_mos"]
        quantum = self.c["solver"]["bandwidth_quantum_kbps"]
        lower = math.ceil(stream.min_kbps / quantum) * quantum
        upper = math.floor(stream.max_kbps / quantum) * quantum
        if lower > upper:
            return InverseResult(False, None, None, "EMPTY_QUANTIZED_INTERVAL")
        minimum = self.forward(business, replace(stream, bitrate_kbps=lower), budget)
        if minimum + eps >= target:
            return InverseResult(True, lower, minimum)
        maximum = self.forward(business, replace(stream, bitrate_kbps=upper), budget)
        if maximum + eps < target:
            return InverseResult(False, None, maximum, "TARGET_UNREACHABLE_IN_DOMAIN")
        v = self.dimensions(business, stream)
        factor = v["factor"]
        p = self.p
        if factor <= 0:
            return InverseResult(False, None, maximum, "NONPOSITIVE_FACTOR")
        q = p["minimum_mos"] + (target - p["minimum_mos"]) / factor
        if q > p["maximum_mos"] + eps:
            return InverseResult(False, None, maximum, "QUALITY_UNREACHABLE")
        kind = self.businesses[business]["quality_model"]
        if kind == "call":
            call = p["call"]
            y = (q / v["s_r"] - p["minimum_mos"]) / call["bitrate_amplitude"]
            if not 0 < y < 1:
                return InverseResult(False, None, maximum, "INVERSE_DOMAIN")
            D = (
                call["d_coefficient"]
                * call["fps"] ** call["fps_power"]
                * (stream.width * stream.height) ** call["pixels_power"]
            )
            rate = D * (y / (1 - y)) ** (1 / call["bitrate_power"])
        else:
            w1, w2, _, _ = self.businesses[business]["coefficients"]
            sb = (
                q
                if kind == "bitrate"
                else p["maximum_mos"]
                - (
                    p["maximum_mos"]
                    - q
                    - p["score_span"] * w2 * (p["maximum_mos"] - v["s_r"])
                )
                / (p["score_span"] * w1)
            )
            if not p["maximum_mos"] / 2 < sb < p["maximum_mos"]:
                return InverseResult(False, None, maximum, "INVERSE_DOMAIN")
            rate = p["bitrate_scale"] * math.log(sb / (p["maximum_mos"] - sb))
        rate = max(lower, math.ceil(rate / quantum) * quantum)
        for _ in range(2):
            if rate > upper:
                return InverseResult(False, None, maximum, "BANDWIDTH_UPPER_BOUND")
            actual = self.forward(business, replace(stream, bitrate_kbps=rate), budget)
            if actual + eps >= target:
                return InverseResult(True, rate, actual)
            rate += quantum
        return InverseResult(False, None, None, "MODEL_ROUNDTRIP_FAILURE")
