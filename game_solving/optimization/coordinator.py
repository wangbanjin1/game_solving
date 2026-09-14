"""Bounded repair, slack upgrades and pair exchange under UL/DL constraints."""

from game_solving.evaluation.validation import total


class ResourceCoordinator:
    def __init__(self, config, policy):
        self.c = config
        self.policy = policy

    def coordinate(self, scene, raw, pools, budget, mode, prices):
        chosen = list(raw)
        p = self.c["solver"]
        tol = p["epsilon_bandwidth_kbps"]
        eps = p["epsilon_gain"]
        ref = self.c["utility"]["bandwidth_reference_kbps"]
        users = scene.users
        used = total(chosen)
        while not used.fits(scene.available, tol):
            excess = (
                max(0, used.ul - scene.available.ul),
                max(0, used.dl - scene.available.dl),
            )
            moves = []
            for i, (u, old, pool) in enumerate(zip(users, chosen, pools)):
                for a in pool:
                    budget.consume(kind="repair_evaluation")
                    release = old.bandwidth - a.bandwidth
                    if release.ul < -tol or release.dl < -tol:
                        continue
                    help_ = (
                        min(max(0, release.ul), excess[0])
                        + min(max(0, release.dl), excess[1])
                    ) / ref
                    if help_ <= 0:
                        continue
                    loss = (old.h - a.h) / help_
                    if mode == "NORMAL" and not a.basic_met:
                        continue
                    damages = int(not a.basic_met) - int(not old.basic_met)
                    gap = a.gap - old.gap
                    protection = self.policy.protection(u)
                    stage = self.policy.stage(u)
                    if damages <= 0 and gap <= eps:
                        key = (0, stage, -protection[0], -protection[1], loss)
                    else:
                        key = (
                            1,
                            -protection[0],
                            -protection[1],
                            stage,
                            damages,
                            gap,
                            loss,
                        )
                    moves.append((key, u.user_id, a.action_id, i, a))
            if not moves:
                return None, False
            _, _, _, i, a = min(moves, key=lambda x: x[:3])
            chosen[i] = a
            used = total(chosen)
        # Repeated single-user improvements; total resources updated in O(1).
        while True:
            best = None
            best_key = None
            for i, (u, old, pool) in enumerate(zip(users, chosen, pools)):
                for a in pool:
                    budget.consume(kind="upgrade_evaluation")
                    delta = a.bandwidth - old.bandwidth
                    if not (used + delta).fits(scene.available, tol):
                        continue
                    if mode == "NORMAL" and not a.basic_met:
                        continue
                    benefit = a.h - old.h
                    protection = self.policy.protection(u)
                    guarantee = (
                        int(not old.basic_met) - int(not a.basic_met),
                        old.gap - a.gap,
                    )
                    if mode != "NORMAL" and (
                        guarantee[0] < 0 or (guarantee[0] == 0 and guarantee[1] < -eps)
                    ):
                        continue
                    improves_guarantee = mode != "NORMAL" and (
                        guarantee[0] > 0 or guarantee[1] > eps
                    )
                    if not improves_guarantee and benefit <= eps:
                        continue
                    cost = (
                        max(prices[0], p["price_floor"]) * max(delta.ul, 0)
                        + max(prices[1], p["price_floor"]) * max(delta.dl, 0)
                    ) / ref
                    ratio = benefit / cost if cost > 0 else float("inf")
                    key = (
                        (
                            0,
                            protection,
                            -guarantee[0],
                            -guarantee[1],
                            -ratio,
                            u.user_id,
                            a.action_id,
                        )
                        if improves_guarantee
                        else (1, (0, 0), 0, 0, -ratio, u.user_id, a.action_id)
                    )
                    if best_key is None or key < best_key:
                        best_key = key
                        best = (i, a)
            if best is None:
                break
            i, a = best
            used = used + a.bandwidth - chosen[i].bandwidth
            chosen[i] = a
        # A bounded pair exchange may restore a higher guarantee or improve H.
        donors = []
        receivers = []
        for i, (u, old, pool) in enumerate(zip(users, chosen, pools)):
            for a in pool:
                budget.consume(kind="exchange_candidate")
                delta = a.bandwidth - old.bandwidth
                if delta.ul <= 0 and delta.dl <= 0 and (delta.ul < 0 or delta.dl < 0):
                    donors.append(
                        (
                            self.policy.stage(u),
                            tuple(-x for x in self.policy.protection(u)),
                            i,
                            a,
                        )
                    )
                if a.h > old.h + eps or (
                    mode != "NORMAL"
                    and (a.basic_met > old.basic_met or a.gap < old.gap - eps)
                ):
                    receivers.append((self.policy.protection(u), i, a))
        donors.sort(key=lambda x: (x[0], x[1], x[2], x[3].action_id))
        receivers.sort(key=lambda x: (x[0], x[1], x[2].action_id))
        evaluations = 0
        best = None
        best_rank = self.policy.rank(users, chosen, mode)
        for _, _, i, down in donors:
            for _, j, up in receivers:
                if i == j:
                    continue
                if evaluations >= p["exchange_pairs_per_iteration"]:
                    break
                budget.consume(kind="exchange_evaluation")
                evaluations += 1
                trial = chosen[:]
                trial[i] = down
                trial[j] = up
                if mode == "NORMAL" and not all(a.basic_met for a in trial):
                    continue
                if not total(trial).fits(scene.available, tol):
                    continue
                rank = self.policy.rank(users, trial, mode)
                if rank < best_rank:
                    best_rank = rank
                    best = trial
            if evaluations >= p["exchange_pairs_per_iteration"]:
                break
        return (best, False) if best is not None else (chosen, True)
