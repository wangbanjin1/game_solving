"""Independent finite-universe DFS; does not call the heuristic's pruning or coordinator."""

import itertools
import math
from game_solving.domain.entities import Bandwidth
from game_solving.optimization.budget import Budget, BudgetExceeded
from game_solving.optimization.feasibility import minimum_action
from game_solving.infrastructure.config import digest


def evaluate_reference(scene, result, config, policy):
    cfg = config["reference"]
    budget = Budget(cfg["max_nodes"], cfg["time_budget_ms"])
    if not cfg["enabled"]:
        return {"status": "not_computed", "reason": "disabled"}
    pools = []
    best_score = None
    best_h = None
    best_rank = None
    witness = None
    complete = False
    try:
        for i, u in enumerate(scene.users):
            q = policy.quota(u)
            rates = {"ul": {q.ul}, "dl": {q.dl}}
            actions = {}
            for requirement in ("hard", "basic", "target"):
                a, _ = minimum_action(u, requirement, policy, budget)
                if a:
                    actions[a.action_id] = a
            current = policy.make_action(u, u.current, budget, True)
            if current:
                actions[current.action_id] = current
            if result.decisions:
                actions[result.decisions[i].action_id] = result.decisions[i]
            if not config["businesses"][u.business]["mos_type"]:
                rates = {
                    "ul": {max(q.ul, u.contract.ul)},
                    "dl": {max(q.dl, u.contract.dl)},
                }
            for d, s in u.streams.items():
                if d == "session":
                    continue
                if not u.bitrate_adaptation:
                    rates[d] = {getattr(u.current, d)}
                    continue
                quantum = config["solver"]["bandwidth_quantum_kbps"]
                lower = (
                    math.ceil(max(s.min_kbps, getattr(u.contract, d)) / quantum)
                    * quantum
                )
                upper = math.floor(s.max_kbps / quantum) * quantum
                values = {lower, upper, getattr(u.current, d)}
                for target in sorted(
                    set(
                        config["solver"]["mos_grid"]
                        + cfg["mos_grid"]
                        + [
                            u.target,
                            u.baseline,
                            *u.direction_targets.values(),
                            *u.direction_baselines.values(),
                        ]
                    )
                ):
                    inv = policy.model.inverse(u.business, target, s, budget)
                    if inv.feasible:
                        values.add(max(lower, inv.bandwidth_kbps))
                rates[d] = {x for x in values if lower <= x <= upper}
            for ul, dl in itertools.product(sorted(rates["ul"]), sorted(rates["dl"])):
                budget.consume(kind="reference_candidate")
                a = policy.make_action(u, Bandwidth(ul, dl), budget)
                if a:
                    actions.setdefault(a.action_id, a)
            pool = [
                a for a in actions.values() if result.mode != "NORMAL" or a.basic_met
            ]
            pools.append(sorted(pool, key=lambda a: a.action_id))
        if not all(pools):
            return {
                "status": "infeasible",
                "proof_type": "empty_finite_candidate_set",
                "steps": budget.used,
            }
        n = len(pools)
        suffix = [Bandwidth() for _ in range(n + 1)]
        for i in range(n - 1, -1, -1):
            suffix[i] = suffix[i + 1] + Bandwidth(
                min(a.bandwidth.ul for a in pools[i]),
                min(a.bandwidth.dl for a in pools[i]),
            )
        # Explicit stack permits up to max_users without Python recursion limits.
        stack = [(0, Bandwidth(), tuple())]
        while stack:
            budget.consume(kind="reference_node")
            depth, used, chosen = stack.pop()
            if depth == n:
                score = sum(
                    policy.weight(u) * config["policy"]["weight_reference"] * a.mos
                    for u, a in zip(scene.users, chosen)
                    if a.mos is not None
                )
                h = sum(a.h for a in chosen)
                rank = policy.rank(scene.users, chosen, result.mode)
                if best_score is None or score > best_score:
                    best_score = score
                    witness = [a.action_id for a in chosen]
                if best_rank is None or rank < best_rank:
                    best_rank = rank
                    best_h = h
                continue
            for a in reversed(pools[depth]):
                budget.consume(kind="reference_branch")
                allocated = used + a.bandwidth
                if (allocated + suffix[depth + 1]).fits(
                    scene.available, config["solver"]["epsilon_bandwidth_kbps"]
                ):
                    stack.append((depth + 1, allocated, chosen + (a,)))
        complete = True
    except BudgetExceeded as exc:
        reason = exc.reason
    return {
        "status": (
            "exact_discrete"
            if complete and best_score is not None
            else (
                "infeasible"
                if complete
                else "best_known" if best_score is not None else "not_computed"
            )
        ),
        "reason": None if complete else reason,
        "U_reference": best_score,
        "H_reference": best_h,
        "rank_reference": best_rank,
        "action_ids": witness,
        "steps": budget.used,
        "elapsed_ms": budget.elapsed_ms,
        "candidate_universe_hash": digest(
            [[a.action_id for a in pool] for pool in pools]
        ),
        "objective": "weighted_session_mos",
        "policy_objective": (
            "basic_lexicographic_then_H" if result.mode != "NORMAL" else "H"
        ),
        "constraints_mode": result.mode,
        "complete": complete,
    }
