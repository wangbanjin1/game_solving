"""Independent directional feasibility certificates for the active KQI model."""

import math
from game_solving.domain.entities import Bandwidth
from game_solving.evaluation.validation import check_actions, total


def minimum_action(user, requirement, policy, budget=None):
    q = policy.quota(user)
    rates = {"ul": q.ul, "dl": q.dl}
    for d, s in user.streams.items():
        if d == "session":
            continue
        quantum = policy.c["solver"]["bandwidth_quantum_kbps"]
        media = policy.c["businesses"][user.business]["media"]
        response_enabled = policy.c["kqi_response"]["enabled"]
        model_min = min((m["min_kbps"] for m in media), default=s.min_kbps) if response_enabled else s.min_kbps
        model_max = max((m["max_kbps"] for m in media), default=s.max_kbps) if response_enabled else s.max_kbps
        if hasattr(policy.model, "rate_bounds"):
            lookup_min, lookup_max = policy.model.rate_bounds(user.business, s.phase)
            model_min, model_max = max(model_min, lookup_min), min(model_max, lookup_max)
        basic_floor = policy.c["business_basic_kbps"].get(user.business, {}).get(d, 0.0)
        floor = max(model_min, getattr(user.contract, d), basic_floor)
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
            if user.bitrate_adaptation:
                if response_enabled:
                    # Bandwidth changes the media level and network KQIs. Search
                    # the quantized legal domain instead of applying the old
                    # fixed-environment inverse to the initial stream.
                    lo_step = math.ceil(floor / quantum)
                    hi_step = math.floor(model_max / quantum)

                    def reaches(step):
                        rate = step * quantum
                        predicted = policy._action_stream(user, d, s, rate)
                        mos = policy.model.forward(user.business, predicted, budget)
                        return mos + policy.c["solver"]["epsilon_mos"] >= target

                    if hi_step < lo_step or not reaches(hi_step):
                        return None, "TARGET_UNREACHABLE_IN_DYNAMIC_KQI_DOMAIN"
                    while lo_step < hi_step:
                        middle = (lo_step + hi_step) // 2
                        if reaches(middle):
                            hi_step = middle
                        else:
                            lo_step = middle + 1
                    floor = lo_step * quantum
                else:
                    inv = policy.model.inverse(user.business, target, s, budget)
                    if not inv.feasible:
                        return None, inv.reason
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
