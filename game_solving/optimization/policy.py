"""User utility, hard constraints and explicit normal/degraded ranking."""

from dataclasses import replace
from game_solving.domain.entities import Bandwidth, Action
from game_solving.infrastructure.config import digest


class Policy:
    def __init__(self, config, model):
        self.c = config
        self.model = model

    def weight(self, u):
        p = self.c["policy"]
        b = self.c["businesses"][u.business]
        if p["weight_mode"] == "ordered":
            # Mixed-radix order: a primary dimension outweighs every lower
            # dimension's combined contribution to this user's weight.
            priorities = sorted({v["priority"] for v in self.c["businesses"].values()})
            categories = {"business": priorities, **{d: p[d + "_order"] for d in ("package", "position", "tolerance")}}
            values = {"business": b["priority"], "package": u.package, "position": u.position, "tolerance": u.tolerance}
            ordinal = 0
            for dimension in p["weight_dimensions"]:
                order = categories[dimension]
                ordinal = ordinal * len(order) + len(order) - 1 - order.index(values[dimension])
            return (ordinal + 1) / p["weight_reference"]
        group = "realtime" if b["mos_type"] else u.business
        return (
            p["weights"][u.package][group]
            * b["weight_factor"]
            * p["position_factors"][u.position]
            * p["tolerance_factors"][u.tolerance]
            / p["weight_reference"]
        )

    def protection(self, u):
        dimensions = {"business": self.c["businesses"][u.business]["priority"], "package": self.c["policy"]["package_order"].index(u.package)}
        return tuple(dimensions[d] for d in self.c["policy"]["priority_order"])

    def stage(self, u):
        return (
            0
            if not self.c["businesses"][u.business]["mos_type"]
            else 1 if self.c["policy"]["profiles"][u.profile] == "Non-GBR" else 2
        )

    def quota(self, u):
        b = self.c["businesses"][u.business]
        return Bandwidth(b["quota_ul_kbps"], b["quota_dl_kbps"])

    def hard_errors(self, u, b, direction_mos):
        tol = self.c["solver"]["epsilon_bandwidth_kbps"]
        eps = self.c["solver"]["epsilon_mos"]
        errors = []
        for d in ("ul", "dl"):
            rate = getattr(b, d)
            contract = getattr(u.contract, d)
            if rate < contract - tol:
                errors.append("CONTRACT_" + d)
            if d in u.streams:
                s = u.streams[d]
                if rate < s.min_kbps - tol or rate > s.max_kbps + tol:
                    errors.append("MEDIA_BOUND_" + d)
                if not u.bitrate_adaptation and abs(rate - getattr(u.current, d)) > tol:
                    errors.append("ADAPTATION_DISABLED_" + d)
            else:
                quota = getattr(self.quota(u), d)
                if self.c["businesses"][u.business]["mos_type"]:
                    if abs(rate - quota) > tol:
                        errors.append("FIXED_QUOTA_" + d)
                elif rate < quota - tol:
                    errors.append("QOS_FLOOR_" + d)
            if u.cap and rate > getattr(u.cap, d) + tol:
                errors.append("USER_CAP_" + d)
        if u.total_cap_kbps is not None and b.ul + b.dl > u.total_cap_kbps + tol:
            errors.append("USER_TOTAL_CAP")
        if u.hard_mos or not u.allow_soft_degrade:
            if any(
                m + eps < u.direction_baselines.get(d, u.baseline)
                for d, m in direction_mos.items()
            ):
                errors.append("HARD_MOS")
        return errors

    def make_action(self, u, b, budget=None, anchor=False):
        if budget:
            budget.consume(kind="candidate")
        direction = {}
        for d, s in u.streams.items():
            rate = s.bitrate_kbps if d == "session" else getattr(b, d)
            if not s.min_kbps <= rate <= s.max_kbps:
                return None
            direction[d] = self.model.forward(
                u.business, replace(s, bitrate_kbps=rate), budget
            )
        if self.hard_errors(u, b, direction):
            return None
        mos = min(direction.values()) if direction else None
        p = self.c["models"]
        span = p["score_span"]
        lo = p["minimum_mos"]
        eps = self.c["solver"]["epsilon_mos"]
        if mos is not None:
            experience = (mos - lo) / span
        else:
            q = self.quota(u)
            experience = min(
                [1.0]
                + [
                    getattr(b, d) / getattr(q, d)
                    for d in ("ul", "dl")
                    if getattr(q, d) > 0
                ]
            )
        basic = all(
            m + eps >= u.direction_baselines.get(d, u.baseline)
            for d, m in direction.items()
        )
        target = all(
            m + eps >= u.direction_targets.get(d, u.target)
            for d, m in direction.items()
        )
        gap = max(
            [0.0]
            + [
                max(0, u.direction_baselines.get(d, u.baseline) - m) / span
                for d, m in direction.items()
            ]
        )
        utility = self.c["utility"]
        target_session = (
            min(u.direction_targets.values()) if u.direction_targets else u.target
        )
        debt = (
            0
            if mos is None or u.history_mos is None
            else min(
                utility["debt_cap"], max(0, (target_session - u.history_mos) / span)
            )
        )
        previous = (
            (u.observed_mos - lo) / span if u.observed_mos is not None else experience
        )
        stability = utility["beta_change"] * abs(experience - previous)
        h = (self.weight(u) + utility["eta_fair"] * debt) * experience - stability
        ident = digest({"profile": u.profile, "ul": float(b.ul), "dl": float(b.dl)})[
            :20
        ]
        return Action(
            u.user_id,
            ident,
            b,
            direction,
            mos,
            experience,
            h,
            stability,
            basic,
            target,
            gap,
            anchor,
        )

    def violation_vector(self, users, actions):
        layers = sorted({self.protection(u) for u in users})
        result = []
        for layer in layers:
            group = [a for u, a in zip(users, actions) if self.protection(u) == layer]
            result.extend(
                (sum(not a.basic_met for a in group), sum(a.gap for a in group))
            )
        return tuple(result)

    def rank(self, users, actions, mode):
        h = sum(a.h for a in actions)
        if self.c["policy"]["objective"] == "total_utility":
            return (-h,)
        return (
            (*self.violation_vector(users, actions), -h) if mode != "NORMAL" else (-h,)
        )

    def components(self, user, action):
        target = min(user.direction_targets.values()) if user.direction_targets else user.target
        debt = 0.0 if action.mos is None or user.history_mos is None else min(self.c["utility"]["debt_cap"], max(0, (target - user.history_mos) / self.c["models"]["score_span"]))
        return {"weight": self.weight(user), "debt": debt,
                "experience_benefit": self.weight(user) * action.experience,
                "fairness_compensation": self.c["utility"]["eta_fair"] * debt * action.experience,
                "stability_cost": action.stability_cost, "H": action.h}
