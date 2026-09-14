"""Units and structural checks shared across application boundaries."""

import math


def number(value, name, low=0.0, high=math.inf):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not low <= value <= high
    ):
        raise ValueError(f"INVALID_VALUE: {name}={value!r}")
    return value


def validate_stream(s):
    for key in (
        "bitrate_kbps",
        "resolution",
        "width",
        "height",
        "rtt_ms",
        "min_kbps",
        "max_kbps",
    ):
        number(getattr(s, key), key)
    for key in ("loss_ratio", "stall_ratio"):
        number(getattr(s, key), key, high=1)
    for key in ("buffer_ms", "jitter_ms"):
        if getattr(s, key) is not None:
            number(getattr(s, key), key)
    if (
        s.min_kbps <= 0
        or s.min_kbps > s.max_kbps
        or not s.min_kbps <= s.bitrate_kbps <= s.max_kbps
    ):
        raise ValueError("INVALID_MEDIA_INTERVAL")
    if s.phase not in ("steady", "initial"):
        raise ValueError("INVALID_PHASE")


def validate_scene(scene, config):
    if scene.schema_version != "2.0" or not scene.users:
        raise ValueError("INVALID_SCENE")
    if len(scene.users) > config["population"]["max_users"]:
        raise ValueError("MAX_USERS_EXCEEDED")
    for b in (scene.capacity, scene.unmanaged, scene.reserve, scene.available):
        number(b.ul, "ul_kbps")
        number(b.dl, "dl_kbps")
    seen = set()
    for u in scene.users:
        if u.user_id in seen:
            raise ValueError("DUPLICATE_USER")
        seen.add(u.user_id)
        if (
            u.business not in config["businesses"]
            or u.profile not in config["policy"]["profiles"]
        ):
            raise ValueError("UNKNOWN_BUSINESS_OR_PROFILE")
        if (
            u.package not in config["policy"]["weights"]
            or u.position not in config["policy"]["position_factors"]
            or u.tolerance not in config["policy"]["tolerance_factors"]
        ):
            raise ValueError("UNKNOWN_USER_POLICY")
        for b in (u.current, u.contract):
            number(b.ul, "user_ul")
            number(b.dl, "user_dl")
        if u.cap is not None:
            number(u.cap.ul, "user_cap_ul")
            number(u.cap.dl, "user_cap_dl")
        if u.total_cap_kbps is not None:
            number(u.total_cap_kbps, "user_total_cap")
        number(u.target, "target", 1, 5)
        number(u.baseline, "baseline", 1, u.target)
        for d, t in u.direction_targets.items():
            if d not in ("ul", "dl", "session"):
                raise ValueError("INVALID_TARGET_DIRECTION")
            number(t, "direction_target", 1, 5)
        for d, t in u.direction_baselines.items():
            if d not in ("ul", "dl", "session"):
                raise ValueError("INVALID_BASELINE_DIRECTION")
            number(t, "direction_baseline", 1, u.direction_targets.get(d, u.target))
        for m in (u.observed_mos, u.history_mos):
            if m is not None:
                number(m, "mos", 1, 5)
        if config["businesses"][u.business]["mos_type"] and u.observed_mos is None:
            raise ValueError("MISSING_OBSERVED_MOS")
        expected = set(config["businesses"][u.business]["media_directions"])
        if u.business == "game":
            expected = {"session"}
        if set(u.streams) != expected:
            raise ValueError("INVALID_STREAM_DIRECTIONS")
        for s in u.streams.values():
            validate_stream(s)
