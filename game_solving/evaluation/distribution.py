"""Initial distributions and target witnesses, independent of solver decisions."""
import csv
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from game_solving.optimization.feasibility import minimum_action
from game_solving.evaluation.validation import total, check_actions


def statistics(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return {"count": 0}
    def quantile(q):
        point = (len(values) - 1) * q
        index = int(point)
        return values[index] + (values[min(index + 1, len(values) - 1)] - values[index]) * (point - index)
    return {"count": len(values), "min": values[0], "p10": quantile(.1), "median": quantile(.5), "p90": quantile(.9), "max": values[-1], "mean": sum(values) / len(values)}


def describe_scene(scene, policy):
    groups = {}
    current = [policy.make_action(u, u.current, anchor=True) for u in scene.users]
    targets = [minimum_action(u, "target", policy)[0] for u in scene.users]
    for name, selected in [("all", list(range(len(scene.users))))] + [
        (package, [i for i, u in enumerate(scene.users) if u.package == package])
        for package in policy.c["policy"]["package_order"]
    ]:
        users = [scene.users[i] for i in selected]
        mos_users = [i for i in selected if scene.users[i].observed_mos is not None]
        evaluated_users = [i for i in mos_users if scene.users[i].package in policy.c["policy"]["evaluation_packages"]]
        groups[name] = {"users": len(users), "mos_users": len(mos_users),
            "business_counts": dict(Counter(u.business for u in users)),
            "app_counts": dict(Counter(u.app_id for u in users if u.app_id)),
            "qoe_counts": dict(Counter(u.qoe_category for u in users if u.qoe_category)),
            "target_evaluated_users": len(evaluated_users),
            "target_met": sum(current[i].target_met for i in evaluated_users) if evaluated_users else None,
            "target_unmet": sum(not current[i].target_met for i in evaluated_users) if evaluated_users else None,
            "individually_unreachable": sum(targets[i] is None for i in evaluated_users) if evaluated_users else None,
            "mos": statistics([u.observed_mos for u in users]),
            "history_mos": statistics([u.history_mos for u in users]),
            "kqi": {field: statistics([getattr(stream, field) for u in users for stream in u.streams.values()]) for field in ("rtt_ms", "loss_ratio", "stall_ratio", "buffer_ms", "jitter_ms", "bitrate_kbps")},
            "by_business": {business: {
                "users": sum(u.business == business for u in users),
                "mos": statistics([u.observed_mos for u in users if u.business == business]),
                "target_met": sum(current[i].target_met for i in evaluated_users if scene.users[i].business == business) if evaluated_users else None,
                "target_unmet": sum(not current[i].target_met for i in evaluated_users if scene.users[i].business == business) if evaluated_users else None,
            } for business in sorted({u.business for u in users})}}
    used = total(current)
    all_targets = all(a is not None for a in targets)
    target_feasible = all_targets and not check_actions(scene, targets, policy)
    vip = groups.get("vip", {})
    vip_users = vip.get("users", 0)
    vip_unmet = vip.get("target_unmet") or 0
    return {"scene_id": scene.scene_id, "groups": groups,
        "total_users": len(scene.users), "vip_users": vip_users,
        "vip_ratio": vip_users / len(scene.users) if scene.users else None,
        "vip_target_unmet": vip_unmet,
        "vip_target_unmet_ratio": vip_unmet / vip_users if vip_users else None,
        "solver_group_counts": dict(Counter(str(policy.group_key(u)) for u in scene.users)),
        "capacity": asdict(scene.capacity), "available": asdict(scene.available), "current_total": asdict(used),
        "headroom_ratio": {d: getattr(scene.available, d) / getattr(used, d) - 1 if getattr(used, d) else None for d in ("ul", "dl")},
        "all_targets_feasible": bool(target_feasible),
        "target_required": asdict(total(targets)) if all_targets else None,
        "target_witness": [asdict(a) for a in targets] if target_feasible else None,
        "control_scope": "active_kqi_response_model; quantized_legal_bandwidth_domain"}


def write_initial_distribution_csv(records, output_path):
    """Write the cross-scene initial-state statistics as a reviewable table."""
    target = Path(output_path)
    fields = [
        "scene_id", "total_users", "total_user_factor", "vip_users",
        "requested_vip_ratio", "vip_ratio", "vip_ratio_offset",
        "requested_vip_unmet_ratio", "vip_target_unmet",
        "vip_target_unmet_ratio", "solver_group_count",
    ]
    with target.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for record in records:
            parameters = record.get("scenario_parameters", {})
            writer.writerow({
                "scene_id": record["scene_id"],
                "total_users": record.get("total_users"),
                "total_user_factor": parameters.get("total_user_factor"),
                "vip_users": record.get("vip_users"),
                "requested_vip_ratio": parameters.get(
                    "requested_vip_ratio"
                ),
                "vip_ratio": record.get("vip_ratio"),
                "vip_ratio_offset": (
                    round(parameters["vip_ratio_offset"], 10)
                    if parameters.get("vip_ratio_offset") is not None
                    else None
                ),
                "requested_vip_unmet_ratio": parameters.get(
                    "requested_vip_unmet_ratio"
                ),
                "vip_target_unmet": record.get("vip_target_unmet"),
                "vip_target_unmet_ratio": record.get(
                    "vip_target_unmet_ratio"
                ),
                "solver_group_count": len(record.get("solver_group_counts", {})),
            })
    return target.resolve()
