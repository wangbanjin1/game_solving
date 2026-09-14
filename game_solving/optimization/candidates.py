"""Finite directional candidates, mandatory anchors/floors and safe two-resource pruning."""

import itertools
import math
from game_solving.domain.entities import Bandwidth


class CandidateBuilder:
    def __init__(self, config, policy):
        self.c = config
        self.policy = policy
        self.truncated = False

    def build(self, user, budget, required=()):
        c = self.c
        model = self.policy.model
        mandatory = {a.action_id: a for a in required if a is not None}
        anchor = self.policy.make_action(user, user.current, budget, True)
        if anchor:
            mandatory[anchor.action_id] = anchor
        q = self.policy.quota(user)
        rates = {"ul": [q.ul], "dl": [q.dl]}
        if not c["businesses"][user.business]["mos_type"]:
            rates = {
                "ul": [max(q.ul, user.contract.ul)],
                "dl": [max(q.dl, user.contract.dl)],
            }
        for d, s in user.streams.items():
            if d == "session":
                continue
            if not user.bitrate_adaptation:
                rates[d] = [getattr(user.current, d)]
                continue
            quantum = c["solver"]["bandwidth_quantum_kbps"]
            lo = (
                math.ceil(max(s.min_kbps, getattr(user.contract, d)) / quantum)
                * quantum
            )
            hi = math.floor(s.max_kbps / quantum) * quantum
            values = {lo, hi, getattr(user.current, d)}
            targets = set(
                c["solver"]["mos_grid"]
                + [
                    user.direction_targets.get(d, user.target),
                    user.direction_baselines.get(d, user.baseline),
                ]
            )
            for target in sorted(targets):
                inverse = model.inverse(user.business, target, s, budget)
                if inverse.feasible:
                    values.add(max(lo, inverse.bandwidth_kbps))
            rates[d] = sorted(x for x in values if lo <= x <= hi)
        actions = dict(mandatory)
        count = 0
        for ul, dl in itertools.product(rates["ul"], rates["dl"]):
            budget.consume(kind="candidate_combination")
            count += 1
            if count > c["solver"]["raw_candidates_per_user"]:
                self.truncated = True
                break
            action = self.policy.make_action(user, Bandwidth(ul, dl), budget)
            if action:
                actions.setdefault(action.action_id, action)
        values = sorted(
            actions.values(),
            key=lambda a: (a.bandwidth.ul, a.bandwidth.dl, a.action_id),
        )
        kept = []
        for a in values:
            if a.action_id in mandatory:
                kept.append(a)
                continue
            dominated = False
            for b in values:
                budget.consume(kind="pareto_comparison")
                if b.action_id == a.action_id:
                    continue
                if b.basic_met != a.basic_met or b.target_met != a.target_met:
                    continue
                if (
                    b.bandwidth.fits(a.bandwidth)
                    and b.stability_cost <= a.stability_cost
                    and b.gap <= a.gap
                    and all(b.direction_mos[d] >= m for d, m in a.direction_mos.items())
                    and b.experience >= a.experience
                    and (b.bandwidth != a.bandwidth or b.experience > a.experience)
                ):
                    dominated = True
                    break
            if not dominated:
                kept.append(a)
        limit = c["solver"]["kept_candidates_per_user"]
        if len(mandatory) > limit:
            raise ValueError("CANDIDATE_LIMIT_BELOW_REQUIRED_ACTIONS")
        if len(kept) > limit:
            self.truncated = True
            preserved = dict(mandatory)
            for a in sorted(
                kept, key=lambda a: (not a.basic_met, a.gap, -a.h, a.action_id)
            ):
                if len(preserved) >= limit:
                    break
                preserved.setdefault(a.action_id, a)
            kept = list(preserved.values())
        return sorted(kept, key=lambda a: (a.bandwidth.ul, a.bandwidth.dl, a.action_id))

    def scope(self, user, pool, round_index):
        # One graph layer per iteration; baseline repair candidates are always available.
        edges = self.c["solver"]["level_edges"]
        level = lambda m: sum(m >= x for x in edges[1:-1]) if m is not None else 0
        center = level(user.observed_mos)
        nearest = sorted(
            pool,
            key=lambda a: (
                abs(a.bandwidth.ul - user.current.ul)
                + abs(a.bandwidth.dl - user.current.dl),
                a.action_id,
            ),
        )
        allowed = [a for a in nearest if abs(level(a.mos) - center) <= round_index]
        for a in pool:
            if a.anchor and a not in allowed:
                allowed.append(a)
        basic = [a for a in pool if a.basic_met]
        if basic:
            floor = min(
                basic, key=lambda a: (a.bandwidth.ul + a.bandwidth.dl, a.action_id)
            )
            if floor not in allowed:
                allowed.append(floor)
        return allowed or nearest[:1]
