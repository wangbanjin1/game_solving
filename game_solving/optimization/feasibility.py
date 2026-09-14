"""Certificates only for fixed-media, independent directional rate domains."""

import math
from game_solving.domain.entities import Bandwidth
from game_solving.evaluation.validation import check_actions, total


def minimum_action(user, requirement, policy, budget=None):
    q = policy.quota(user)
    rates = {"ul": q.ul, "dl": q.dl}
    for d, s in user.streams.items():
        if d == "session":
            continue
        floor = max(s.min_kbps, getattr(user.contract, d))
        quantum = policy.c["solver"]["bandwidth_quantum_kbps"]
        floor = math.ceil(floor / quantum) * quantum
        if not user.bitrate_adaptation:
            floor = getattr(user.current, d)
        if requirement != "hard" or user.hard_mos or not user.allow_soft_degrade:
            kind = "basic" if requirement == "hard" else requirement
            target = (
                user.direction_baselines.get(d, user.baseline)
                if kind == "basic"
                else user.direction_targets.get(d, user.target)
            )
            inv = policy.model.inverse(user.business, target, s, budget)
            if not inv.feasible:
                return None, inv.reason
            if user.bitrate_adaptation:
                floor = max(floor, inv.bandwidth_kbps)
        rates[d] = floor
    # Non-key QoS may have contracts exceeding its minimum; fixed media feedback quotas may not.
    if not policy.c["businesses"][user.business]["mos_type"]:
        rates = {d: max(rates[d], getattr(user.contract, d)) for d in rates}
    action = policy.make_action(user, Bandwidth(**rates), budget)
    if action is None:
        return None, "HARD_DOMAIN_CONFLICT"
    if requirement == "basic" and not action.basic_met:
        return None, "BASIC_UNREACHABLE_IN_DOMAIN"
    if requirement == "target" and not action.target_met:
        return None, "TARGET_UNREACHABLE_IN_DOMAIN"
    return action, None


def certificate(scene, requirement, policy, budget=None):
    actions = []
    unreachable = []
    for user in scene.users:
        if budget:
            budget.consume(kind="feasibility")
        action, reason = minimum_action(user, requirement, policy, budget)
        if action is None:
            unreachable.append({"user_id": user.user_id, "reason": reason})
        else:
            actions.append(action)
    if unreachable:
        return {
            "status": "infeasible",
            "proof_type": "individual_fixed_domain",
            "unreachable": unreachable,
        }, None
    errors = check_actions(scene, actions, policy, budget)
    used = total(actions)
    proof = {
        "status": "infeasible" if errors else "feasible",
        "proof_type": "componentwise_minimum_fixed_domain",
        "required_ul_kbps": used.ul,
        "required_dl_kbps": used.dl,
        "gap_ul_kbps": max(0, used.ul - scene.available.ul),
        "gap_dl_kbps": max(0, used.dl - scene.available.dl),
        "violations": errors,
        "action_ids": [a.action_id for a in actions],
    }
    return proof, actions if not errors else None
