"""Independent result reconstruction rather than trusting action flags or totals."""

from game_solving.domain.entities import Bandwidth


def total(actions):
    return Bandwidth(
        sum(a.bandwidth.ul for a in actions), sum(a.bandwidth.dl for a in actions)
    )


def check_actions(scene, actions, policy, budget=None, recompute=False):
    errors = []
    if len(actions) != len(scene.users):
        return ["MISSING_ACTIONS"]
    for u, a in zip(scene.users, actions):
        if budget:
            budget.consume(kind="validation")
        if a.user_id != u.user_id:
            errors.append("USER_ID_MISMATCH")
        errors.extend(
            u.user_id + ":" + e
            for e in policy.hard_errors(u, a.bandwidth, a.direction_mos)
        )
        if recompute:
            actual = policy.make_action(u, a.bandwidth, budget, anchor=a.anchor)
            if actual is None:
                errors.append("INVALID_RECOMPUTED_ACTION")
            elif actual != a:
                errors.append("PREDICTED_ACTION_MISMATCH")
    if budget:
        budget.consume(kind="validation")
    if not total(actions).fits(
        scene.available, policy.c["solver"]["epsilon_bandwidth_kbps"]
    ):
        errors.append("CELL_CAPACITY_EXCEEDED")
    return errors
