"""CSV-backed MOS/KQI model used by the solver and data generator."""

from __future__ import annotations

import csv
import math
from dataclasses import replace
from pathlib import Path

from game_solving.domain.entities import InverseResult
from game_solving.domain.validation import number, validate_stream


class LookupMosModel:
    def __init__(self, config):
        self.c = config
        self.p = config["models"]
        root = Path(__file__).resolve().parents[2]
        configured = Path(self.p["lookup_directory"])
        self.root = configured if configured.is_absolute() else root / configured
        self.forward_rows = {}
        self.choice_rows = {}
        self._load_forward()
        self._load_choices()
        missing = set(config["businesses"]) - set(self.forward_rows)
        missing = {b for b in missing if config["businesses"][b]["mos_type"]}
        if missing:
            raise ValueError("LOOKUP_BUSINESS_MISSING: " + ",".join(sorted(missing)))

    @staticmethod
    def _optional_float(value):
        return None if value in (None, "") else float(value)

    def _load_forward(self):
        folder = self.root / self.p["lookup_forward_directory"]
        if not folder.is_dir():
            raise ValueError("LOOKUP_FORWARD_DIRECTORY_MISSING: " + str(folder))
        for path in sorted(folder.glob("*.csv")):
            with path.open(encoding="utf-8-sig", newline="") as stream:
                parsed = []
                for source in csv.DictReader(stream):
                    parsed.append({
                        "business": source["业务代码"],
                        "phase": source["阶段"],
                        "bandwidth_kbps": float(source["带宽Mbps"]) * 1000.0,
                        "resolution": self._optional_float(source.get("分辨率")),
                        "rtt_ms": float(source["典型时延ms"]),
                        "loss_ratio": float(source["典型丢包率%"]) / 100.0,
                        "stall_ratio": float(source["典型卡顿率%"]) / 100.0,
                        "buffer_ms": self._optional_float(source.get("典型首缓ms")),
                        "jitter_ms": self._optional_float(source.get("典型抖动ms")),
                        "mos": float(source["MOS典型"]),
                    })
            if parsed:
                self.forward_rows[path.stem] = parsed

    def _load_choices(self):
        folder = self.root / self.p["lookup_reverse_directory"]
        if not folder.is_dir():
            raise ValueError("LOOKUP_REVERSE_DIRECTORY_MISSING: " + str(folder))
        for path in sorted(folder.glob("*.csv")):
            with path.open(encoding="utf-8-sig", newline="") as stream:
                for source in csv.DictReader(stream):
                    if source["可达"].lower() != "true":
                        continue
                    key = (path.stem, source.get("阶段", "steady"), float(source["目标MOS"]))
                    self.choice_rows[key] = {
                        "bandwidth_kbps": float(source["带宽Mbps"]) * 1000.0,
                        "mos": float(source["用于达标的MOS"]),
                    }

    def _phase_rows(self, business, phase):
        rows = [r for r in self.forward_rows[business] if r["phase"] == phase]
        if not rows and phase != "steady":
            rows = [r for r in self.forward_rows[business] if r["phase"] == "steady"]
        if not rows:
            raise ValueError(f"LOOKUP_PHASE_MISSING: {business}:{phase}")
        return rows

    def rate_bounds(self, business, phase="steady"):
        rows = self._phase_rows(business, phase)
        rates = [r["bandwidth_kbps"] for r in rows]
        return min(rates), max(rates)

    def lookup_row(self, business, stream):
        rows = self._phase_rows(business, stream.phase)
        rate = stream.bitrate_kbps
        same_rate = [r for r in rows if abs(r["bandwidth_kbps"] - rate) <= 1e-6]
        candidates = same_rate or rows
        if business == "game":
            def distance(row):
                values = [
                    (row["rtt_ms"], stream.rtt_ms, 460.0),
                    (row["loss_ratio"], stream.loss_ratio, 0.05),
                    (row["stall_ratio"], stream.stall_ratio, 1.0),
                ]
                if stream.jitter_ms is not None and row["jitter_ms"] is not None:
                    values.append((row["jitter_ms"], stream.jitter_ms, 50.0))
                return sum(((a - b) / scale) ** 2 for a, b, scale in values)
            return min(candidates, key=distance)
        return min(candidates, key=lambda row: abs(row["bandwidth_kbps"] - rate))

    def project_stream(self, business, stream, rate):
        row = self.lookup_row(business, replace(stream, bitrate_kbps=rate))
        lower, upper = self.rate_bounds(business, stream.phase)
        resolution = row["resolution"]
        width = height = 0
        profiles = self.c["businesses"][business]["media"]
        if resolution is not None and profiles:
            profile = min(profiles, key=lambda item: abs(item["resolution"] - resolution))
            width, height = int(profile["width"]), int(profile["height"])
        return replace(
            stream,
            bitrate_kbps=row["bandwidth_kbps"],
            min_kbps=lower,
            max_kbps=upper,
            resolution=0 if resolution is None else resolution,
            width=width,
            height=height,
            rtt_ms=row["rtt_ms"],
            loss_ratio=row["loss_ratio"],
            stall_ratio=row["stall_ratio"],
            buffer_ms=row["buffer_ms"],
            jitter_ms=row["jitter_ms"],
        )

    def resolution_available(self, business):
        return any(r["resolution"] is not None for r in self.forward_rows[business])

    def forward(self, business, stream, budget=None):
        if budget:
            budget.consume(kind="model_forward")
        validate_stream(stream)
        return self.lookup_row(business, stream)["mos"]

    def inverse(self, business, target, stream, budget=None):
        if budget:
            budget.consume(kind="model_inverse")
        number(target, "target_mos", self.p["minimum_mos"], self.p["maximum_mos"])
        phase = stream.phase
        target_key = min(5.0, math.ceil((target - 1e-9) * 10) / 10)
        row = self.choice_rows.get((business, phase, target_key))
        if row is None and phase != "steady":
            row = self.choice_rows.get((business, "steady", target_key))
        if row is None:
            maximum = max(r["mos"] for r in self._phase_rows(business, phase))
            return InverseResult(False, None, maximum, "TARGET_UNREACHABLE_IN_LOOKUP")
        if not stream.min_kbps <= row["bandwidth_kbps"] <= stream.max_kbps:
            return InverseResult(False, None, row["mos"], "LOOKUP_BANDWIDTH_OUTSIDE_STREAM_DOMAIN")
        return InverseResult(True, row["bandwidth_kbps"], row["mos"])
