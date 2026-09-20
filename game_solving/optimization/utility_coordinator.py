"""Feasible net-utility repair and bounded one/two-donor exchanges."""
import heapq
import itertools
from dataclasses import asdict
from game_solving.evaluation.validation import total


def coordinate_utility(owner, scene, raw, pools, budget, prices):
    cfg = owner.c["solver"]
    tol, eps = cfg["epsilon_bandwidth_kbps"], cfg["epsilon_gain"]
    chosen = list(raw)
    used = total(chosen)
    owner.events = []
    owner.exchange_checks = 0
    owner.exchange_truncated = False

    def apply(changes, reason):
        nonlocal used
        changes = tuple(changes)
        gain = sum(owner.policy.decision_score(scene.users[i], action) - owner.policy.decision_score(scene.users[i], chosen[i]) for i, action in changes)
        if cfg["trace_users"]:
            owner.events.append({"reason": reason, "net_benefit": gain, "changes": [
                {"user_id": scene.users[i].user_id, "before": asdict(chosen[i]), "after": asdict(action)}
                for i, action in changes]})
        for i, action in changes:
            used = used + action.bandwidth - chosen[i].bandwidth
            chosen[i] = action

    # Feasibility first; spend the smallest utility loss per useful released unit.
    while not used.fits(scene.available, tol):
        best = None
        excess = {d: max(0, getattr(used, d) - getattr(scene.available, d)) for d in ("ul", "dl")}
        for i, pool in enumerate(pools):
            old = chosen[i]
            for action in pool:
                budget.consume(kind="repair_evaluation")
                release = old.bandwidth - action.bandwidth
                if release.ul < -tol or release.dl < -tol:
                    continue
                useful = sum(min(excess[d], max(0, getattr(release, d))) for d in excess)
                if useful <= tol:
                    continue
                key = ((owner.policy.decision_score(scene.users[i], old) - owner.policy.decision_score(scene.users[i], action)) / useful, i, action.action_id)
                if best is None or key < best[0]:
                    best = key, i, action
        if best is None:
            return None, False
        apply([(best[1], best[2])], "capacity_repair")

    # Strict improvements only. After each exchange, reconsider slack upgrades.
    while True:
        best = None
        donors, receivers = [], []
        for i, pool in enumerate(pools):
            old = chosen[i]
            for action in pool:
                budget.consume(kind="upgrade_evaluation")
                delta = action.bandwidth - old.bandwidth
                gain = owner.policy.decision_score(scene.users[i], action) - owner.policy.decision_score(scene.users[i], old)
                if gain > eps:
                    receivers.append((gain, i, action))
                    if (used + delta).fits(scene.available, tol):
                        key = (-gain, i, action.action_id)
                        if best is None or key < best[0]:
                            best = key, i, action
                if delta.ul <= tol and delta.dl <= tol and (delta.ul < -tol or delta.dl < -tol):
                    donors.append((owner.policy.decision_score(scene.users[i], old) - owner.policy.decision_score(scene.users[i], action), i, action))
        if best is not None:
            apply([(best[1], best[2])], "slack_upgrade")
            continue
        limit = cfg["exchange_shortlist"]
        # Bound sorting as well as pair checks. Tie-breaking is deterministic.
        if len(donors) > limit or len(receivers) > limit:
            owner.exchange_truncated = True
        donors = heapq.nsmallest(limit, donors, key=lambda x: (x[0], x[1], x[2].action_id))
        receivers = heapq.nsmallest(limit, receivers, key=lambda x: (-x[0], x[1], x[2].action_id))
        best_changes, best_gain = None, eps
        exhausted = False
        for count in range(1, cfg["exchange_max_donors"] + 1):
            for donor_group in itertools.combinations(donors, count):
                for gain, receiver, up in receivers:
                    if owner.exchange_checks >= cfg["exchange_pairs_per_iteration"]:
                        exhausted = True
                        break
                    budget.consume(kind="exchange_evaluation")
                    owner.exchange_checks += 1
                    indices = [x[1] for x in donor_group] + [receiver]
                    if len(set(indices)) != len(indices):
                        continue
                    net = gain - sum(x[0] for x in donor_group)
                    if net <= best_gain:
                        continue
                    changes = [(i, a) for _, i, a in donor_group] + [(receiver, up)]
                    trial_used = used
                    for i, action in changes:
                        trial_used = trial_used + action.bandwidth - chosen[i].bandwidth
                    if trial_used.fits(scene.available, tol):
                        best_changes, best_gain = changes, net
                if exhausted:
                    break
            if exhausted:
                break
        owner.exchange_truncated |= exhausted
        if best_changes is None:
            # Local only in the evaluated neighborhood, not an exhaustive proof.
            return chosen, True
        apply(best_changes, "net_utility_exchange")
