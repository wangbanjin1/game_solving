"""Only valid prior observations contribute to a frozen history value."""


def time_weighted_mos(observations, now, window_seconds, model_hash):
    total = duration = 0.0
    for record in observations:
        if record["model_hash"] != model_hash:
            continue
        start = max(now - window_seconds, record["start"])
        end = min(now, record["end"])
        if end > start:
            total += (end - start) * record["mos"]
            duration += end - start
    return total / duration if duration else None
