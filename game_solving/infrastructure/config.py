"""Versioned JSON defaults, strict override names, validation and model fingerprints."""

import copy
import hashlib
import json
from importlib.resources import files
from pathlib import Path
from game_solving.domain.validation import number

REPLACE_MAPS = {
    "business_min_fraction",
    "qoe_counts",
    "minimum_qoe_counts",
    "qoe_business_mapping",
    "business_probs_by_package",
    "compliance_probs_by_package",
    "extensions",
    "package_probs",
    "business_probs",
    "compliance_probs",
    "direction_targets",
    "direction_baselines",
    "applications",
    "business_basic_kbps",
}


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def merge(base, overrides, prefix=""):
    for key, value in overrides.items():
        if key not in base:
            raise ValueError("UNKNOWN_CONFIG_KEY: " + prefix + key)
        if (
            isinstance(value, dict)
            and isinstance(base[key], dict)
            and key not in REPLACE_MAPS
        ):
            merge(base[key], value, prefix + key + ".")
        else:
            base[key] = copy.deepcopy(value)
    return base


def validate(config):
    c = config
    if c["logging"]["level"] not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ValueError("INVALID_LOG_LEVEL")
    if c["schema_version"] != "2.0":
        raise ValueError("UNSUPPORTED_SCHEMA")

    def walk(value, path=""):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, path + "." + k)
        elif isinstance(value, list):
            for v in value:
                walk(v, path)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            number(value, path, low=-float("inf"))

    walk(c)
    ref = c["generation"]["throughput_reference"]
    if type(ref["devices"]) is not int or ref["devices"] < 1:
        raise ValueError("INVALID_REFERENCE_DEVICE_COUNT")
    for direction in ("ul", "dl"):
        number(ref["aggregate_" + direction + "_kbps"], "reference_throughput")
        if ref["aggregate_" + direction + "_kbps"] <= 0:
            raise ValueError("POSITIVE_REFERENCE_THROUGHPUT_REQUIRED")
    if type(ref["simultaneous_directions_confirmed"]) is not bool:
        raise ValueError("INVALID_REFERENCE_SIMULTANEITY")
    high_load = c["generation"]["high_load_sampling"]
    number(high_load["probability"], "high_load_probability", high=1)
    for business, fraction in high_load["business_min_fraction"].items():
        if business not in c["businesses"] or not c["businesses"][business]["media_directions"]:
            raise ValueError("HIGH_LOAD_REQUIRES_MEDIA_BUSINESS")
        number(fraction, "high_load_min_fraction", high=1)
        if fraction <= 0:
            raise ValueError("POSITIVE_HIGH_LOAD_FRACTION_REQUIRED")
        for direction in c["businesses"][business]["media_directions"]:
            floor = ref["aggregate_" + direction + "_kbps"] / ref["devices"] * fraction
            if not any(m["max_kbps"] >= floor for m in c["businesses"][business]["media"]):
                raise ValueError("HIGH_LOAD_OUTSIDE_MEDIA_MODEL_RANGE")
    qoe = c["population"]["qoe_counts"]
    minimum_qoe = c["population"]["minimum_qoe_counts"]
    mapping = c["population"]["qoe_business_mapping"]
    if qoe:
        allowed_qoe = {"qoe_shortvideo", "qoe_video", "qoe_meeting", "qoe_cloudgame", "qoe_voip", "qoe_live", "qoe_game", "qoe_openlive"}
        if set(qoe) - allowed_qoe:
            raise ValueError("NON_QOE_CATEGORY_NOT_ALLOWED")
        if set(qoe) != set(mapping) or c["population"]["distribution_mode"] != "independent" or c["population"]["business_probs_by_package"]:
            raise ValueError("INVALID_QOE_MAPPING_OR_DISTRIBUTION_CONFLICT")
        if any(type(v) is not int or v < 0 for v in qoe.values()) or sum(qoe.values()) <= 0:
            raise ValueError("INVALID_QOE_COUNTS")
        for category, table in mapping.items():
            if not table or set(table) - set(c["businesses"]):
                raise ValueError("INVALID_QOE_BUSINESS")
            if any(not c["businesses"][business]["mos_type"] for business in table):
                raise ValueError("NON_QOE_BUSINESS_MAPPING_NOT_ALLOWED")
            for probability in table.values():
                number(probability, category, high=1)
            if abs(sum(table.values()) - 1) > 1e-9:
                raise ValueError("INVALID_QOE_PROBABILITIES")
    if minimum_qoe:
        if not qoe or set(minimum_qoe) - set(qoe):
            raise ValueError("INVALID_MINIMUM_QOE_CATEGORY")
        if any(type(v) is not int or v < 0 for v in minimum_qoe.values()):
            raise ValueError("INVALID_MINIMUM_QOE_COUNT")
        if sum(minimum_qoe.values()) > c["population"]["users_per_cell"]:
            raise ValueError("MINIMUM_QOE_COUNTS_EXCEED_POPULATION")
    if c["generation"]["capacity_mode"] not in ("fixed", "current_headroom"):
        raise ValueError("INVALID_CAPACITY_MODE")
    ratios = c["generation"]["headroom_ratios"]
    if not ratios or len(set(ratios)) != len(ratios):
        raise ValueError("INVALID_HEADROOM_RATIOS")
    for ratio in ratios:
        number(ratio, "headroom_ratio", high=1)
    for section, field, categories in (
        ("population", "business_probs_by_package", c["businesses"]),
        ("generation", "compliance_probs_by_package", ("over", "met", "unmet", "severe")),
    ):
        for package, table in c[section][field].items():
            if package not in c["policy"]["weights"] or not table or set(table) - set(categories):
                raise ValueError("INVALID_CONDITIONAL_DISTRIBUTION")
            for probability in table.values():
                number(probability, field, high=1)
            if abs(sum(table.values()) - 1) > 1e-9:
                raise ValueError("INVALID_CONDITIONAL_PROBABILITIES")
    if c["population"]["distribution_mode"] == "joint" and c["population"]["business_probs_by_package"]:
        raise ValueError("JOINT_AND_CONDITIONAL_DISTRIBUTIONS_CONFLICT")
    if sorted(c["policy"]["priority_order"]) != ["business", "package"]:
        raise ValueError("INVALID_PRIORITY_ORDER")
    if c["policy"]["objective"] not in ("legacy_guarantee", "total_utility", "vip_guarantee"):
        raise ValueError("INVALID_OBJECTIVE")
    if c["policy"]["weight_mode"] not in ("legacy", "ordered"):
        raise ValueError("INVALID_WEIGHT_MODE")
    response = c["kqi_response"]
    for field in ("enabled", "resolution_adaptation"):
        if type(response[field]) is not bool:
            raise ValueError("BOOLEAN_REQUIRED: kqi_response." + field)
    for field in ("rtt_elasticity", "loss_elasticity", "stall_elasticity", "minimum_rtt_ratio", "minimum_loss_ratio", "minimum_stall_ratio", "maximum_degradation_ratio"):
        number(response[field], "kqi_response." + field)
        if response[field] < 0:
            raise ValueError("INVALID_KQI_RESPONSE: " + field)
    if sorted(c["policy"]["weight_dimensions"]) != ["business", "package", "position", "tolerance"]:
        raise ValueError("INVALID_WEIGHT_DIMENSIONS")
    evaluated = c["policy"]["evaluation_packages"]
    if not evaluated or len(evaluated) != len(set(evaluated)) or set(evaluated) - set(c["policy"]["weights"]):
        raise ValueError("INVALID_EVALUATION_PACKAGES")
    cap = c["policy"].get("normal_request_mos_cap")
    if cap is not None:
        number(cap, "normal_request_mos_cap", 1, 5)
    for dimension, table in (("package", c["policy"]["weights"]), ("position", c["policy"]["position_factors"]), ("tolerance", c["policy"]["tolerance_factors"])):
        order = c["policy"][dimension + "_order"]
        if len(order) != len(set(order)) or set(order) != set(table):
            raise ValueError("INVALID_WEIGHT_ORDER")
    if type(c["solver"]["exchange_max_donors"]) is not int or c["solver"]["exchange_max_donors"] not in (1, 2):
        raise ValueError("INVALID_DONOR_LIMIT")
    if type(c["solver"]["exchange_shortlist"]) is not int or c["solver"]["exchange_shortlist"] < 1:
        raise ValueError("INVALID_EXCHANGE_SHORTLIST")
    number(c["solver"]["epsilon_price"], "epsilon_price")
    for section, field in (("generation", "require_target_reachable"), ("solver", "trace_users"), ("solver", "trace_candidates"), ("population", "minimum_app_coverage")):
        if type(c[section][field]) is not bool:
            raise ValueError("BOOLEAN_REQUIRED: " + field)
    if c["solver"]["price_update"] not in ("legacy", "smoothed", "monotone"):
        raise ValueError("INVALID_PRICE_UPDATE")
    number(c["solver"]["price_smoothing"], "price_smoothing", high=1)
    if c["solver"]["price_smoothing"] <= 0:
        raise ValueError("POSITIVE_PRICE_SMOOTHING_REQUIRED")
    for field in ("price_deadband", "price_max_step"):
        number(c["solver"][field], field)
    for offset in c["solver"]["candidate_mos_offsets"]:
        number(offset, "candidate_mos_offset", high=4)
    history = c["generation"]["history"]
    if type(history["enabled"]) is not bool:
        raise ValueError("INVALID_HISTORY_ENABLED")
    if type(history["periods"]) is not int or history["periods"] < 1:
        raise ValueError("INVALID_HISTORY_PERIODS")
    number(history["interval_seconds"], "history_interval_seconds")
    if history["interval_seconds"] <= 0:
        raise ValueError("POSITIVE_HISTORY_INTERVAL_REQUIRED")
    number(history["periods"] * history["interval_seconds"], "history_duration")
    multipliers = history["bandwidth_multiplier"]
    if not isinstance(multipliers, list) or len(multipliers) != 2:
        raise ValueError("INVALID_HISTORY_MULTIPLIER")
    for value in multipliers:
        number(value, "history_bandwidth_multiplier")
    if multipliers[0] > multipliers[1] or multipliers[1] <= 0:
        raise ValueError("INVALID_HISTORY_MULTIPLIER")
    if history["enabled"] and c["utility"]["history_window_seconds"] <= 0:
        raise ValueError("POSITIVE_HISTORY_WINDOW_REQUIRED")
    for section, keys in [
        (
            "population",
            [
                "package_probs",
                "business_probs",
                "position_probs",
                "tolerance_probs",
                "profile_probs",
            ],
        ),
        ("generation", ["compliance_probs", "quality_probs", "split_probs"]),
    ]:
        for key in keys:
            table = c[section][key]
            if (
                not table
                or any(v < 0 or v > 1 for v in table.values())
                or abs(sum(table.values()) - 1) > 1e-9
            ):
                raise ValueError("INVALID_PROBABILITIES: " + key)
    for section, keys in [
        ("population", ["users_per_cell", "max_users"]),
        (
            "generation",
            [
                "role_max_attempts",
                "state_pool_size",
                "scene_max_attempts",
                "assembly_max_nodes",
                "max_model_evaluations",
            ],
        ),
        (
            "solver",
            [
                "max_iterations",
                "max_total_steps",
                "raw_candidates_per_user",
                "kept_candidates_per_user",
                "stable_rounds",
            ],
        ),
        ("reference", ["max_nodes"]),
    ]:
        for key in keys:
            if type(c[section][key]) is not int or c[section][key] < 1:
                raise ValueError("POSITIVE_INTEGER_REQUIRED: " + key)
    if type(c["num_scenes"]) is not int or c["num_scenes"] < 1:
        raise ValueError("INVALID_SCENE_COUNT")
    if c["population"]["users_per_cell"] > c["population"]["max_users"]:
        raise ValueError("MAX_USERS_EXCEEDED")
    if c["population"]["distribution_mode"] not in ("independent", "joint"):
        raise ValueError("UNSUPPORTED_DISTRIBUTION_MODE")
    if c["population"]["distribution_mode"] == "joint":
        rows = c["population"]["joint_rows"]
        if not rows or abs(sum(r["probability"] for r in rows) - 1) > 1e-9:
            raise ValueError("INVALID_JOINT_PROBABILITIES")
        for row in rows:
            number(row["probability"], "joint_probability", high=1)
            for field, table in [
                ("business", c["businesses"]),
                ("package", c["policy"]["weights"]),
                ("position", c["policy"]["position_factors"]),
                ("tolerance", c["policy"]["tolerance_factors"]),
            ]:
                if row[field] not in table:
                    raise ValueError("UNKNOWN_JOINT_CATEGORY")
    if c["generation"]["quota_mode"] not in ("reachable", "specified"):
        raise ValueError("INVALID_QUOTA_MODE")
    if c["generation"]["stream_phase"] not in ("steady", "initial"):
        raise ValueError("INVALID_PHASE")
    if set(c["population"]["business_probs"]) - set(c["businesses"]):
        raise ValueError("UNKNOWN_BUSINESS")
    if set(c["population"]["profile_probs"]) - set(c["policy"]["profiles"]):
        raise ValueError("UNKNOWN_PROFILE")
    for key in c["cell"]:
        number(c["cell"][key], key)
    for d in ("ul", "dl"):
        if (
            c["cell"]["capacity_" + d + "_kbps"]
            < c["cell"]["unmanaged_" + d + "_kbps"]
            + c["cell"]["reserve_" + d + "_kbps"]
        ):
            raise ValueError("NEGATIVE_AVAILABLE_CAPACITY")
    for p in c["population"]["package_probs"]:
        number(c["policy"]["targets"][p], "target", 1, 5)
        number(c["policy"]["baselines"][p], "baseline", 1, c["policy"]["targets"][p])
    if c["policy"]["session_aggregation"] != "min":
        raise ValueError("UNSUPPORTED_AGGREGATION")
    for key in (
        "time_budget_ms",
        "gamma0",
        "price_floor",
        "epsilon_strategy",
        "epsilon_resource",
        "epsilon_utility",
        "epsilon_gain",
        "epsilon_mos",
        "epsilon_bandwidth_kbps",
    ):
        number(c["solver"][key], key)
    for value in c["solver"]["lambda_initial"]:
        number(value, "initial_price")
    if len(c["solver"]["lambda_initial"]) != 2:
        raise ValueError("TWO_PRICES_REQUIRED")
    if (
        type(c["solver"]["exchange_pairs_per_iteration"]) is not int
        or c["solver"]["exchange_pairs_per_iteration"] < 0
    ):
        raise ValueError("INVALID_EXCHANGE_LIMIT")
    for key in ("bandwidth_quantum_kbps",):
        if c["solver"][key] <= 0:
            raise ValueError("POSITIVE_QUANTUM_REQUIRED")
    for value in c["solver"]["mos_grid"]:
        number(value, "mos_grid", 1, 5)
    if not c["solver"]["mos_grid"]:
        raise ValueError("EMPTY_MOS_GRID")
    for key in (
        "eta_fair",
        "debt_cap",
        "beta_change",
        "switch_cost",
        "history_window_seconds",
    ):
        number(c["utility"][key], key)
    if (
        c["utility"]["bandwidth_reference_kbps"] <= 0
        or c["policy"]["weight_reference"] <= 0
    ):
        raise ValueError("POSITIVE_REFERENCE_REQUIRED")
    for name, b in c["businesses"].items():
        if b["mos_type"] not in ("A", "B", None) or len(b["coefficients"]) != 4:
            raise ValueError("INVALID_MODEL_PROFILE")
        for v in b["coefficients"]:
            number(v, "coefficient")
        for m in b["media"]:
            if m["min_kbps"] <= 0 or m["max_kbps"] < m["min_kbps"]:
                raise ValueError("INVALID_MEDIA_INTERVAL")
        if b["media_directions"] and not b["media"]:
            raise ValueError("MISSING_MEDIA")
    for field in ("position_factors", "tolerance_factors"):
        for value in c["policy"][field].values():
            number(value, field)
    for row in c["policy"]["weights"].values():
        for value in row.values():
            number(value, "weight")
    for key in ("contract_ul_kbps", "contract_dl_kbps"):
        number(c["policy"][key], key)
    number(c["generation"]["mixed_probability"], "mixed_probability", high=1)
    number(c["generation"]["delta_over"], "delta_over")
    number(c["generation"]["delta_severe"], "delta_severe")
    lo, hi = c["generation"]["non_key_current_multiplier"]
    if not 1 <= lo <= hi:
        raise ValueError("INVALID_CURRENT_QOS_MULTIPLIER")
    if (
        c["generation"]["stream_phase"] == "initial"
        and c["population"]["business_probs"].get("meeting", 0)
        and not c["businesses"]["meeting"]["initial_enabled"]
    ):
        raise ValueError("MEETING_INITIAL_BUFFER_UNAVAILABLE")
    for name, table in c["ranges"].items():
        for quality, (lo, hi) in table.items():
            number(lo, name)
            number(hi, name)
            if lo > hi or (name in ("loss", "stall") and hi > 1):
                raise ValueError("INVALID_KQI_RANGE")
    for name, b in c["businesses"].items():
        if b["quality_model"] not in ("general", "call", "game", "bitrate"):
            raise ValueError("UNKNOWN_QUALITY_MODEL")
        if set(b["media_directions"]) - {"ul", "dl"}:
            raise ValueError("INVALID_MEDIA_DIRECTIONS")
        for key in ("quota_ul_kbps", "quota_dl_kbps", "weight_factor"):
            number(b[key], key)
        if (
            b["mos_type"]
            and not b["media_directions"]
            and (b["quota_ul_kbps"] <= 0 or b["quota_dl_kbps"] <= 0)
        ):
            raise ValueError("POSITIVE_GAME_QUOTAS_REQUIRED")
    for business, table in c["business_basic_kbps"].items():
        if business not in c["businesses"]:
            raise ValueError("UNKNOWN_BUSINESS_BASIC_FLOOR: " + business)
        for d in ("ul", "dl"):
            number(table.get(d, 0.0), f"business_basic_kbps.{business}.{d}")
    for app_id, app in c["applications"].items():
        if app["business"] not in c["businesses"] or app["bandwidth_direction"] not in ("ul", "dl"):
            raise ValueError("INVALID_APPLICATION_RULE: " + app_id)
        directional_floors = app.get("bandwidth_min_kbps_by_direction", {})
        if set(directional_floors) - {"ul", "dl"}:
            raise ValueError("INVALID_APPLICATION_BANDWIDTH_DIRECTION: " + app_id)
        for direction, floor in directional_floors.items():
            number(floor, f"{app_id}.bandwidth_min_kbps_by_direction.{direction}")
    for key in (
        "bitrate_scale",
        "resolution_scale",
        "score_span",
        "avg_qoe_per_mos",
        "dynamic_decay_divisor",
    ):
        if c["models"][key] <= 0:
            raise ValueError("POSITIVE_MODEL_SCALE_REQUIRED")
    return c


def load_config(path=None, overrides=None):
    config = json.loads(
        files("configs").joinpath("default.json").read_text(encoding="utf-8")
    )
    if path:
        merge(config, json.loads(Path(path).read_text(encoding="utf-8")))
    if overrides:
        merge(config, overrides)
    return validate(config)


def model_hash(c):
    return digest(
        {
            "models": c["models"],
            "businesses": c["businesses"],
            "quantum": c["solver"]["bandwidth_quantum_kbps"],
            "session": c["policy"]["session_aggregation"],
            "evaluation_packages": c["policy"]["evaluation_packages"],
            "extensions": c.get("extensions", {}),
            "kqi_response": c["kqi_response"],
            "business_basic_kbps": c["business_basic_kbps"],
            "normal_request_mos_cap": c["policy"].get("normal_request_mos_cap"),
        }
    )
