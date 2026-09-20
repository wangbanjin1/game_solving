"""Generate fine-grained forward and reverse MOS/bandwidth/KQI relations.

The forward relation uses 0.1 Mbps bandwidth points. Within each source KQI band,
RTT and stall are linearly interpolated from the bad endpoint to the good endpoint;
loss is geometrically interpolated when both endpoints are positive. The reverse
relation groups the resulting rows into 0.1 MOS bins.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from game_solving.domain.entities import Stream
from game_solving.infrastructure.config import load_config, model_hash
from game_solving.models.mos import MosModel

GRADES = ("差", "一般", "好")

LOSS = {"差": (0.005, 0.05), "一般": (0.0015, 0.005), "好": (0.0, 0.0015)}
STALL = {"差": (0.30, 1.00), "一般": (0.10, 0.30), "好": (0.0, 0.10)}
RTT_INTERACTIVE = {"差": (150.0, 300.0), "一般": (80.0, 150.0), "好": (0.0, 80.0)}
RTT_VIDEO = {"差": (200.0, 500.0), "一般": (100.0, 200.0), "好": (0.0, 100.0)}
RTT_GAME = {"差": (100.0, 460.0), "一般": (50.0, 100.0), "好": (0.0, 50.0)}
BUFFER = {"差": (2000.0, 10000.0), "一般": (1000.0, 2000.0), "好": (0.0, 1000.0)}
JITTER = {"差": (20.0, 50.0), "一般": (10.0, 20.0), "好": (0.0, 10.0)}


SPECS = {
    "openlive": {
        "name": "开直播", "direction": "UL", "phases": ("steady",),
        "bands": ((0.0, 0.7, "差"), (0.7, 1.5, "一般"), (1.5, 6.0, "好")),
        "res": {"差": (480,), "一般": (540, 720), "好": (1080,)},
        "rtt": RTT_INTERACTIVE, "mapping": "用户码率档；档内连续插值",
    },
    "watchlive": {
        "name": "看直播", "direction": "DL", "phases": ("steady",),
        "bands": ((0.0, 0.7, "差"), (0.7, 1.5, "一般"), (1.5, 6.0, "好")),
        "res": {"差": (480,), "一般": (540, 720), "好": (1080,)},
        "rtt": RTT_INTERACTIVE,
        "mapping": "与开直播一致；媒体方向由UL改为DL",
    },
    "video": {
        "name": "视频", "direction": "DL", "phases": ("steady", "initial"),
        "bands": ((0.0, 1.0, "差"), (1.0, 5.0, "一般"), (5.0, 12.0, "好")),
        "res": {"差": (360, 480), "一般": (540, 720), "好": (1080, 2160)},
        "rtt": RTT_VIDEO, "buffer": BUFFER,
        "mapping": "用户给定码率档：0–1/1–5/5–12 Mbps；档内连续插值",
    },
    "shortvideo": {
        "name": "短视频", "direction": "DL", "phases": ("steady",),
        "bands": ((0.0, 1.0, "差"), (1.0, 5.0, "一般"), (5.0, 12.0, "好")),
        "rtt": RTT_VIDEO, "no_resolution": True,
        "mapping": "沿用视频码率/KQI档；无分辨率输入，MOS质量项仅使用码率",
    },
    "cloudgame": {
        "name": "云游", "direction": "DL", "phases": ("steady",),
        "bands": ((0.0, 3.0, "差"), (3.0, 6.0, "一般"), (6.0, 20.0, "好")),
        "res": {"差": (480,), "一般": (540, 720), "好": (1080,)},
        "rtt": RTT_INTERACTIVE, "mapping": "用户码率档；档内连续插值",
    },
    "meeting": {
        "name": "视频会议", "direction": "UL/DL对称", "phases": ("steady",),
        "bands": ((0.0, 0.7, "差"), (0.7, 1.5, "一般"), (1.5, 6.0, "好")),
        "res": {"差": (270,), "一般": (360,), "好": (720,)},
        "rtt": RTT_INTERACTIVE, "mapping": "用户码率档；档内连续插值",
    },
    "voip": {
        "name": "视频通话", "direction": "UL/DL对称", "phases": ("steady",),
        "bands": ((0.0, 0.7, "差"), (0.7, 1.5, "一般"), (1.5, 6.0, "好")),
        "res": {"差": (270,), "一般": (360,), "好": (720,)},
        "rtt": RTT_INTERACTIVE, "mapping": "用户码率档；档内连续插值",
    },
    "game": {
        "name": "手游", "direction": "UL/DL对称", "phases": ("steady",),
        # Solver actions use a 0.1 Mbps grid.  Therefore 0.1/0.2 are the
        # points strictly below the 0.3 Mbps VIP guarantee threshold.
        "bands": ((0.0, 0.2, "差"), (0.2, 0.5, "一般"), (0.5, 1.0, "好")),
        "rtt": RTT_GAME, "jitter": JITTER,
        "mapping": "UL/DL各0.1～1.0 Mbps；0.3 Mbps以下为差，档内连续插值",
    },
}


FORWARD_FIELDS = [
    "business", "business_name", "direction", "phase", "bandwidth_mbps",
    "bandwidth_kbps", "bandwidth_band_min_mbps", "bandwidth_band_max_mbps",
    "bandwidth_band_left_inclusive", "bandwidth_band_right_inclusive",
    "kqi_grade", "quality_position", "mapping_method",
    "legal_action", "legal_resolutions", "resolution_min", "resolution_ref",
    "resolution_max", "rtt_min_ms", "rtt_ref_ms", "rtt_max_ms",
    "loss_min_ratio", "loss_ref_ratio", "loss_max_ratio", "stall_min_ratio",
    "stall_ref_ratio", "stall_max_ratio", "buffer_min_ms", "buffer_ref_ms",
    "buffer_max_ms", "jitter_min_ms", "jitter_ref_ms", "jitter_max_ms",
    "mos_min", "mos_ref", "mos_max", "mos_grade_ref",
]

REVERSE_FIELDS = [
    "business", "business_name", "direction", "phase", "mos_bin_min",
    "mos_bin_max", "feasible", "sample_count", "actual_mos_min",
    "actual_mos_max", "bandwidth_min_mbps", "bandwidth_max_mbps",
    "kqi_grades", "resolutions", "rtt_ref_min_ms", "rtt_ref_max_ms",
    "rtt_allowed_min_ms", "rtt_allowed_max_ms", "loss_ref_min_ratio",
    "loss_ref_max_ratio", "loss_allowed_min_ratio", "loss_allowed_max_ratio",
    "stall_ref_min_ratio", "stall_ref_max_ratio", "stall_allowed_min_ratio",
    "stall_allowed_max_ratio", "buffer_ref_min_ms", "buffer_ref_max_ms",
    "buffer_allowed_min_ms", "buffer_allowed_max_ms", "jitter_ref_min_ms",
    "jitter_ref_max_ms", "jitter_allowed_min_ms", "jitter_allowed_max_ms",
    "relation_kind", "interpretation",
]


def clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


def linear_good(lo: float, hi: float, quality: float) -> float:
    """Interpolate from the bad endpoint hi to the good endpoint lo."""
    return hi - clip01(quality) * (hi - lo)


def loss_good(lo: float, hi: float, quality: float) -> float:
    quality = clip01(quality)
    if lo == 0:
        return hi * (1.0 - quality) ** 2
    return math.exp(math.log(hi) * (1.0 - quality) + math.log(lo) * quality)


def grade_for_bandwidth(spec: dict, bandwidth: float):
    for index, (lo, hi, grade) in enumerate(spec["bands"]):
        if bandwidth <= hi + 1e-12 and (index == 0 or bandwidth > lo + 1e-12):
            return grade, clip01((bandwidth - lo) / (hi - lo)), lo, hi, index == 0
    raise ValueError(f"bandwidth outside bands: {bandwidth}")


def game_grade(quality: float):
    if quality < 1 / 3:
        return "差", quality * 3
    if quality < 2 / 3:
        return "一般", (quality - 1 / 3) * 3
    return "好", (quality - 2 / 3) * 3


def profile_map(config: dict, business: str):
    return {int(x["resolution"]): x for x in config["businesses"][business]["media"]}


def legal_resolutions(config: dict, business: str, candidates: tuple[int, ...], bitrate: float):
    profiles = profile_map(config, business)
    return tuple(
        r for r in candidates
        if r in profiles and profiles[r]["min_kbps"] <= bitrate <= profiles[r]["max_kbps"]
    )


def choose_resolution(candidates: tuple[int, ...], quality: float):
    if len(candidates) == 1:
        return candidates[0]
    index = min(len(candidates) - 1, int(clip01(quality) * len(candidates)))
    return candidates[index]


def dims(config: dict, business: str, resolution: int):
    if business in ("game", "shortvideo"):
        return 0, 0
    profile = profile_map(config, business)[int(resolution)]
    return int(profile["width"]), int(profile["height"])


def mos_value(model, config, business, phase, bitrate, resolution, rtt, loss, stall, buffer, jitter):
    width, height = dims(config, business, resolution)
    stream = Stream(
        bitrate_kbps=bitrate, resolution=resolution, width=width, height=height,
        rtt_ms=rtt, loss_ratio=loss, stall_ratio=stall, min_kbps=0.1,
        max_kbps=20000.0, buffer_ms=buffer, phase=phase, jitter_ms=jitter,
    )
    return model.forward(business, stream)


def mos_grade(value: float):
    if value < 2:
        return "很差"
    if value < 3:
        return "差"
    if value < 3.5:
        return "一般"
    if value < 4.5:
        return "良好"
    return "优秀"


def forward_row(model, config, business, spec, phase, bandwidth, grade, quality,
                band_lo, band_hi, band_left_inclusive):
    bitrate = bandwidth * 1000
    rtt_lo, rtt_hi = spec["rtt"][grade]
    loss_lo, loss_hi = LOSS[grade]
    stall_lo, stall_hi = STALL[grade]
    buffer_lo, buffer_hi = spec.get("buffer", {g: (0.0, 0.0) for g in GRADES})[grade]
    jitter_lo, jitter_hi = spec.get("jitter", {g: (0.0, 0.0) for g in GRADES})[grade]
    rtt_ref = linear_good(rtt_lo, rtt_hi, quality)
    loss_ref = loss_good(loss_lo, loss_hi, quality)
    stall_ref = linear_good(stall_lo, stall_hi, quality)
    buffer_ref = linear_good(buffer_lo, buffer_hi, quality)
    jitter_ref = linear_good(jitter_lo, jitter_hi, quality)

    if spec.get("no_resolution"):
        source_res = legal = usable = (0,)
        res_min = res_ref = res_max = 0
        media = config["businesses"][business]["media"]
        minimum_rate = min((item["min_kbps"] for item in media), default=0.0)
        is_legal = minimum_rate <= bitrate <= spec["bands"][-1][1] * 1000
    else:
        source_res = spec.get("res", {}).get(grade, (0,))
        legal = legal_resolutions(config, business, source_res, bitrate) if business != "game" else (0,)
        usable = legal or source_res
        res_min, res_max = min(usable), max(usable)
        res_ref = choose_resolution(usable, quality)
        is_legal = bool(legal)

    mos_min = mos_value(model, config, business, phase, bitrate, res_min, rtt_hi,
                        loss_hi, stall_hi, buffer_hi, jitter_hi)
    mos_ref = mos_value(model, config, business, phase, bitrate, res_ref, rtt_ref,
                        loss_ref, stall_ref, buffer_ref, jitter_ref)
    mos_max = mos_value(model, config, business, phase, bitrate, res_max, rtt_lo,
                        loss_lo, stall_lo, buffer_lo, jitter_lo)
    lo, hi = sorted((mos_min, mos_max))
    return {
        "business": business, "business_name": spec["name"],
        "direction": spec["direction"], "phase": phase,
        "bandwidth_mbps": bandwidth, "bandwidth_kbps": bitrate,
        "bandwidth_band_min_mbps": band_lo, "bandwidth_band_max_mbps": band_hi,
        "bandwidth_band_left_inclusive": band_left_inclusive,
        "bandwidth_band_right_inclusive": True,
        "kqi_grade": grade, "quality_position": quality,
        "mapping_method": spec["mapping"], "legal_action": is_legal,
        "legal_resolutions": "|".join(map(str, legal)),
        "resolution_min": res_min, "resolution_ref": res_ref, "resolution_max": res_max,
        "rtt_min_ms": rtt_lo, "rtt_ref_ms": rtt_ref, "rtt_max_ms": rtt_hi,
        "loss_min_ratio": loss_lo, "loss_ref_ratio": loss_ref, "loss_max_ratio": loss_hi,
        "stall_min_ratio": stall_lo, "stall_ref_ratio": stall_ref, "stall_max_ratio": stall_hi,
        "buffer_min_ms": buffer_lo, "buffer_ref_ms": buffer_ref, "buffer_max_ms": buffer_hi,
        "jitter_min_ms": jitter_lo, "jitter_ref_ms": jitter_ref, "jitter_max_ms": jitter_hi,
        "mos_min": lo, "mos_ref": mos_ref, "mos_max": hi, "mos_grade_ref": mos_grade(mos_ref),
    }


def generate_forward(config: dict):
    model = MosModel(config)
    rows = []
    for business, spec in SPECS.items():
        max_bandwidth = spec["bands"][-1][1]
        for phase in spec["phases"]:
            for step in range(1, round(max_bandwidth * 10) + 1):
                bandwidth = step / 10
                grade, quality, band_lo, band_hi, left_inclusive = grade_for_bandwidth(spec, bandwidth)
                rows.append(forward_row(model, config, business, spec, phase, bandwidth,
                                        grade, quality, band_lo, band_hi, left_inclusive))
    return rows


def validate_forward(rows: list[dict]):
    groups = {}
    for row in rows:
        if not row["mos_min"] - 1e-9 <= row["mos_ref"] <= row["mos_max"] + 1e-9:
            raise ValueError(f"MOS corner order failed: {row}")
        if row["legal_action"]:
            groups.setdefault((row["business"], row["phase"]), []).append(row["mos_ref"])
    violations = []
    for key, values in groups.items():
        for previous, current in zip(values, values[1:]):
            if current + 1e-9 < previous:
                violations.append((key, previous, current))
    if violations:
        raise ValueError(f"non-monotone typical relation: {violations[:10]}")


def mos_bins():
    for step in range(40):
        lo = 1.0 + step / 10
        yield round(lo, 1), round(lo + 0.1, 1)


def aggregate(rows, business, spec, phase, lo, hi, kind):
    legal = [r for r in rows if r["business"] == business and r["phase"] == phase and r["legal_action"]]
    if kind == "typical":
        chosen = [r for r in legal if lo <= r["mos_ref"] < hi or (hi == 5 and r["mos_ref"] == 5)]
        interpretation = "典型轨迹：MOS_ref落入该0.1区间的细分点"
    else:
        chosen = [r for r in legal if r["mos_min"] < hi and r["mos_max"] >= lo]
        interpretation = "可能包络：该行MOS_min～MOS_max与目标区间相交；各参数边界不可任意笛卡尔组合"
    base = {
        "business": business, "business_name": spec["name"], "direction": spec["direction"],
        "phase": phase, "mos_bin_min": lo, "mos_bin_max": hi,
        "feasible": bool(chosen), "sample_count": len(chosen), "relation_kind": kind,
        "interpretation": interpretation,
    }
    fields = {
        "actual_mos_min": "mos_ref" if kind == "typical" else "mos_min",
        "actual_mos_max": "mos_ref" if kind == "typical" else "mos_max",
        "bandwidth_min_mbps": "bandwidth_mbps", "bandwidth_max_mbps": "bandwidth_mbps",
        "rtt_ref_min_ms": "rtt_ref_ms", "rtt_ref_max_ms": "rtt_ref_ms",
        "rtt_allowed_min_ms": "rtt_min_ms", "rtt_allowed_max_ms": "rtt_max_ms",
        "loss_ref_min_ratio": "loss_ref_ratio", "loss_ref_max_ratio": "loss_ref_ratio",
        "loss_allowed_min_ratio": "loss_min_ratio", "loss_allowed_max_ratio": "loss_max_ratio",
        "stall_ref_min_ratio": "stall_ref_ratio", "stall_ref_max_ratio": "stall_ref_ratio",
        "stall_allowed_min_ratio": "stall_min_ratio", "stall_allowed_max_ratio": "stall_max_ratio",
        "buffer_ref_min_ms": "buffer_ref_ms", "buffer_ref_max_ms": "buffer_ref_ms",
        "buffer_allowed_min_ms": "buffer_min_ms", "buffer_allowed_max_ms": "buffer_max_ms",
        "jitter_ref_min_ms": "jitter_ref_ms", "jitter_ref_max_ms": "jitter_ref_ms",
        "jitter_allowed_min_ms": "jitter_min_ms", "jitter_allowed_max_ms": "jitter_max_ms",
    }
    if not chosen:
        base.update({key: "" for key in fields})
        base.update({"kqi_grades": "", "resolutions": ""})
        return base
    for output, source in fields.items():
        values = [r[source] for r in chosen]
        base[output] = min(values) if "_min_" in output or output.endswith("_min") else max(values)
    base["kqi_grades"] = "|".join(g for g in GRADES if any(r["kqi_grade"] == g for r in chosen))
    base["resolutions"] = "|".join(map(str, sorted({r["resolution_ref"] for r in chosen})))
    return base


def write_csv(path: Path, rows: list[dict], fields: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            output = {}
            for key in fields:
                value = row.get(key, "")
                if isinstance(value, float):
                    if "bandwidth_mbps" in key or key.startswith("mos_bin"):
                        value = f"{value:.1f}"
                    elif "ratio" in key:
                        value = f"{value:.8f}"
                    elif "mos" in key:
                        value = f"{value:.6f}"
                    elif key == "quality_position":
                        value = f"{value:.6f}"
                    else:
                        value = f"{value:.3f}"
                output[key] = value
            writer.writerow(output)


def compact_row(row: dict, business: str):
    result = {
        "业务": row["business_name"],
        "方向": row["direction"],
        "阶段": row["phase"],
        "MOS区间下界": f"{row['mos_bin_min']:.1f}",
        "MOS区间上界": f"{row['mos_bin_max']:.1f}",
        "实际MOS最小": f"{row['actual_mos_min']:.3f}",
        "实际MOS最大": f"{row['actual_mos_max']:.3f}",
        "带宽最小Mbps": f"{row['bandwidth_min_mbps']:.1f}",
        "带宽最大Mbps": f"{row['bandwidth_max_mbps']:.1f}",
        "KQI档位": row["kqi_grades"],
        "分辨率": "" if SPECS[business].get("no_resolution") else row["resolutions"],
        "典型时延最小ms": f"{row['rtt_ref_min_ms']:.3f}",
        "典型时延最大ms": f"{row['rtt_ref_max_ms']:.3f}",
        "典型丢包率最小%": f"{100 * row['loss_ref_min_ratio']:.4f}",
        "典型丢包率最大%": f"{100 * row['loss_ref_max_ratio']:.4f}",
        "典型卡顿率最小%": f"{100 * row['stall_ref_min_ratio']:.3f}",
        "典型卡顿率最大%": f"{100 * row['stall_ref_max_ratio']:.3f}",
        "匹配带宽点数": row["sample_count"],
    }
    if business == "video":
        result.update({
            "典型首缓最小ms": f"{row['buffer_ref_min_ms']:.1f}",
            "典型首缓最大ms": f"{row['buffer_ref_max_ms']:.1f}",
        })
    if business == "game":
        result.update({
            "典型抖动最小ms": f"{row['jitter_ref_min_ms']:.3f}",
            "典型抖动最大ms": f"{row['jitter_ref_max_ms']:.3f}",
        })
    return result


def write_compact_csv(path: Path, rows: list[dict]):
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    content = buffer.getvalue()
    if path.exists() and path.read_text(encoding="utf-8-sig") == content:
        return
    path.write_text(content, encoding="utf-8-sig", newline="")


def solver_row(row: dict, business: str):
    result = {
        "业务代码": row["business"],
        "业务": row["business_name"],
        "方向": row["direction"],
        "阶段": row["phase"],
        "带宽Mbps": f"{row['bandwidth_mbps']:.1f}",
        "KQI档位": row["kqi_grade"],
        "档内质量位置": f"{row['quality_position']:.6f}",
        "分辨率": "" if SPECS[business].get("no_resolution") else row["resolution_ref"],
        "典型时延ms": f"{row['rtt_ref_ms']:.3f}",
        "典型丢包率%": f"{100 * row['loss_ref_ratio']:.4f}",
        "典型卡顿率%": f"{100 * row['stall_ref_ratio']:.3f}",
        "MOS保守": f"{row['mos_min']:.6f}",
        "MOS典型": f"{row['mos_ref']:.6f}",
        "MOS乐观": f"{row['mos_max']:.6f}",
        "MOS等级": row["mos_grade_ref"],
    }
    if business == "video":
        result["典型首缓ms"] = f"{row['buffer_ref_ms']:.1f}"
    if business == "game":
        result["典型抖动ms"] = f"{row['jitter_ref_ms']:.3f}"
    return result


def write_solver_tables(out: Path, forward: list[dict]):
    for business in SPECS:
        rows = [solver_row(row, business) for row in forward
                if row["business"] == business and row["legal_action"]]
        write_compact_csv(out / "solver_lookup" / f"{business}.csv", rows)


def mos_bin_for(value: float):
    index = min(39, max(0, int(math.floor((value - 1.0) * 10 + 1e-9))))
    return 1.0 + index / 10, 1.1 + index / 10


def mos_lookup_row(row: dict, business: str):
    bin_lo, bin_hi = mos_bin_for(row["mos_ref"])
    result = {
        "MOS区间下界": f"{bin_lo:.1f}",
        "MOS区间上界": f"{bin_hi:.1f}",
        "MOS典型": f"{row['mos_ref']:.6f}",
        "MOS保守": f"{row['mos_min']:.6f}",
        "MOS乐观": f"{row['mos_max']:.6f}",
        "MOS等级": row["mos_grade_ref"],
        "业务代码": row["business"],
        "业务": row["business_name"],
        "方向": row["direction"],
        "阶段": row["phase"],
        "带宽Mbps": f"{row['bandwidth_mbps']:.1f}",
        "KQI档位": row["kqi_grade"],
        "档内质量位置": f"{row['quality_position']:.6f}",
        "分辨率": "" if SPECS[business].get("no_resolution") else row["resolution_ref"],
        "时延ms": f"{row['rtt_ref_ms']:.3f}",
        "丢包率%": f"{100 * row['loss_ref_ratio']:.4f}",
        "卡顿率%": f"{100 * row['stall_ref_ratio']:.3f}",
    }
    if business == "video":
        result["首缓ms"] = f"{row['buffer_ref_ms']:.1f}"
    if business == "game":
        result["抖动ms"] = f"{row['jitter_ref_ms']:.3f}"
    return result


def write_mos_lookup_tables(out: Path, forward: list[dict]):
    for business in SPECS:
        source = [row for row in forward if row["business"] == business and row["legal_action"]]
        source.sort(key=lambda row: (row["phase"], mos_bin_for(row["mos_ref"])[0],
                                     row["mos_ref"], row["bandwidth_mbps"]))
        rows = [mos_lookup_row(row, business) for row in source]
        write_compact_csv(out / "mos_lookup" / f"{business}.csv", rows)


def target_mos_values():
    return [round(1.0 + step / 10, 1) for step in range(41)]


def choice_row(target: float, row: dict | None, business: str, criterion: str, metric: str):
    base = {
        "目标MOS": f"{target:.1f}",
        "可达": bool(row),
        "选择口径": criterion,
        "业务代码": business,
        "业务": SPECS[business]["name"],
        "方向": SPECS[business]["direction"],
    }
    if row is None:
        base["不可达原因"] = "该口径下没有合法动作达到目标MOS"
        return base
    actual = row[metric]
    if metric == "mos_min":
        parameter_label = "保守端：最大时延/丢包/卡顿，最低合法分辨率"
        resolution = row["resolution_min"]
        rtt = row["rtt_max_ms"]
        loss = row["loss_max_ratio"]
        stall = row["stall_max_ratio"]
        buffer = row["buffer_max_ms"]
        jitter = row["jitter_max_ms"]
    elif metric == "mos_max":
        parameter_label = "乐观端：最小时延/丢包/卡顿，最高合法分辨率"
        resolution = row["resolution_max"]
        rtt = row["rtt_min_ms"]
        loss = row["loss_min_ratio"]
        stall = row["stall_min_ratio"]
        buffer = row["buffer_min_ms"]
        jitter = row["jitter_min_ms"]
    else:
        parameter_label = "典型值：按带宽在档内位置连续插值"
        resolution = row["resolution_ref"]
        rtt = row["rtt_ref_ms"]
        loss = row["loss_ref_ratio"]
        stall = row["stall_ref_ratio"]
        buffer = row["buffer_ref_ms"]
        jitter = row["jitter_ref_ms"]
    base.update({
        "阶段": row["phase"],
        "带宽Mbps": f"{row['bandwidth_mbps']:.1f}",
        "KQI档位": row["kqi_grade"],
        "档内质量位置": f"{row['quality_position']:.6f}",
        "参数口径": parameter_label,
        "分辨率": "" if SPECS[business].get("no_resolution") else resolution,
        "时延ms": f"{rtt:.3f}",
        "丢包率%": f"{100 * loss:.4f}",
        "卡顿率%": f"{100 * stall:.3f}",
        "MOS保守": f"{row['mos_min']:.6f}",
        "MOS典型": f"{row['mos_ref']:.6f}",
        "MOS乐观": f"{row['mos_max']:.6f}",
        "用于达标的MOS": f"{actual:.6f}",
        "超过目标MOS": f"{actual - target:.6f}",
        "不可达原因": "",
    })
    if business == "video":
        base["首缓ms"] = f"{buffer:.1f}"
    if business == "game":
        base["抖动ms"] = f"{jitter:.3f}"
    return base


def write_choice_tables(out: Path, forward: list[dict], criteria_keys=("typical",)):
    criteria = {
        "typical": ("典型：MOS典型不低于目标", "mos_ref"),
        "guaranteed": ("保守：MOS保守不低于目标", "mos_min"),
        "possible": ("乐观：MOS乐观不低于目标", "mos_max"),
    }
    for business, spec in SPECS.items():
        for key in criteria_keys:
            label, metric = criteria[key]
            output = []
            for phase in spec["phases"]:
                candidates = [row for row in forward if row["business"] == business
                              and row["phase"] == phase and row["legal_action"]]
                for target in target_mos_values():
                    matched = [row for row in candidates if row[metric] + 1e-9 >= target]
                    chosen = min(matched, key=lambda row: (row["bandwidth_mbps"], row[metric])) if matched else None
                    output.append(choice_row(target, chosen, business, label, metric))
            fields = []
            for row in output:
                for field in row:
                    if field not in fields:
                        fields.append(field)
            path = out / f"mos_choice_{key}" / f"{business}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=fields)
            writer.writeheader()
            writer.writerows(output)
            content = buffer.getvalue()
            if path.exists() and path.read_text(encoding="utf-8-sig") == content:
                continue
            path.write_text(content, encoding="utf-8-sig", newline="")


def fmt_range(rows, key):
    values = [r[key] for r in rows if r["legal_action"]]
    return f"{min(values):.3f}～{max(values):.3f}" if values else "不可达"


def write_readme(out: Path, config: dict, forward: list[dict], include_audit=False):
    lines = [
        "# MOS—带宽—KQI 细粒度关系", "",
        "本目录保留正式运行使用的正向动作表和典型MOS反查表。", "",
        "## 取值规则", "",
        "1. 每个码率档内部定义质量位置 `q∈[0,1]`：档位下端接近0，上端等于1。",
        "2. 时延、卡顿和首缓从档位上界向下界线性改善。",
        "3. 丢包率在两个正端点之间按几何方式改善；好档下界为0时使用二次衰减并在档位上端到0。",
        "4. 有分辨率数据的业务只从真实离散档位选择；短视频无分辨率数据，分辨率留空且不进入MOS。",
        "5. 正式反查使用典型口径：选择 MOS典型不低于目标的最小合法带宽。", "",
        "## 文件", "",
        "- `solver_lookup/<business>.csv`：博弈和暴搜的权威正向查表；按0.1 Mbps动作读取KQI和MOS。",
        "- `mos_choice_typical/<business>.csv`：每个目标MOS只选一个最小带宽动作，默认反查表。",
        "- `manifest.json`：公式版本、模型哈希、步长和生成规则。", "",
        "## 各业务参考轨迹的可达 MOS", "",
        "| 业务 | 阶段 | 合法点数 | MOS_ref范围 | 说明 |", "|---|---|---:|---:|---|",
    ]
    for business, spec in SPECS.items():
        for phase in spec["phases"]:
            subset = [r for r in forward if r["business"] == business and r["phase"] == phase]
            count = sum(bool(r["legal_action"]) for r in subset)
            lines.append(f"| {spec['name']} | {phase} | {count} | {fmt_range(subset, 'mos_ref')} | {spec['mapping']} |")
    lines += [
        "", "## 解释边界", "",
        "MOS 是多变量函数，同一 MOS 可能由不同组合产生。正式流程固定使用典型插值轨迹，使每个目标MOS只有一个最小带宽选择。", "",
        "视频和短视频使用用户给定的 0～1、1～5、5～12 Mbps 码率档。看直播沿用开直播规则，仅把媒体方向由上行改为下行。短视频没有分辨率输入，分辨率列留空且不进入MOS。手游使用 UL/DL 对称的 0.1～1.0 Mbps 动作域；0.1～0.2 Mbps 属于质差档，0.3 Mbps 是 VIP 双向带宽保障门槛，0.3～0.5 Mbps 为一般档，0.6～1.0 Mbps 为好档。", "",
        f"模型哈希：`{model_hash(config)}`", "",
    ]
    if include_audit:
        lines += ["审计目录仅在使用 `--include-audit` 时生成。", ""]
    (out / "关系模型说明.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="outputs/9_20_1/mos_kqi_relation_v2")
    parser.add_argument("--solver-only", action="store_true")
    parser.add_argument("--choice-only", action="store_true")
    parser.add_argument("--include-audit", action="store_true")
    args = parser.parse_args()
    out = (ROOT / args.output_dir).resolve()
    config = load_config()
    forward = generate_forward(config)
    validate_forward(forward)
    if args.choice_only:
        write_choice_tables(out, forward, ("typical",))
        print(json.dumps({"output": str(out), "choice_targets_per_phase": 41},
                         ensure_ascii=False, indent=2))
        return
    if args.solver_only:
        write_solver_tables(out, forward)
        write_choice_tables(out, forward, ("typical",))
        print(json.dumps({"output": str(out / "solver_lookup"),
                          "solver_rows": sum(bool(row["legal_action"]) for row in forward)},
                         ensure_ascii=False, indent=2))
        return
    if args.include_audit:
        for business, spec in SPECS.items():
            business_rows = [r for r in forward if r["business"] == business]
            write_csv(out / "forward" / f"{business}.csv", business_rows, FORWARD_FIELDS)
            typical_rows = []
            for kind, folder in (("typical", "reverse_typical"), ("possible", "reverse_possible")):
                reverse = []
                for phase in spec["phases"]:
                    for lo, hi in mos_bins():
                        reverse.append(aggregate(forward, business, spec, phase, lo, hi, kind))
                write_csv(out / folder / f"{business}.csv", reverse, REVERSE_FIELDS)
                if kind == "typical":
                    typical_rows = reverse
            compact = [compact_row(row, business) for row in typical_rows if row["feasible"]]
            write_compact_csv(out / "compact" / f"{business}.csv", compact)
        write_mos_lookup_tables(out, forward)
        write_choice_tables(out, forward, ("guaranteed", "possible"))
    manifest = {
        "formula_version": config["models"]["name"], "model_hash": model_hash(config),
        "bandwidth_step_mbps": 0.1, "mos_bin_step": 0.1,
        "businesses": list(SPECS), "forward_rows": len(forward),
        "rules": {"rtt_stall_buffer": "linear", "loss_positive": "geometric", "loss_to_zero": "quadratic"},
    }
    out.mkdir(parents=True, exist_ok=True)
    write_solver_tables(out, forward)
    write_choice_tables(out, forward, ("typical",))
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    write_readme(out, config, forward, args.include_audit)
    print(json.dumps({"output": str(out), **manifest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
