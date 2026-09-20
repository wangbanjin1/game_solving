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

    def group_key(self, u):
        """Stable role key used after the initial per-user alignment round."""
        width = self.c["solver"]["initial_mos_band_width"]
        band = None if u.observed_mos is None else int((u.observed_mos - 1.0) / width)
        return (u.package, u.tolerance, u.position, u.business, u.app_id, band)

    def _predicted_kqi(self, u, b, direction_mos):
        result = {}
        for direction, stream in u.streams.items():
            rate = stream.bitrate_kbps if direction == "session" else getattr(b, direction)
            predicted = self._action_stream(u, direction, stream, rate)
            result[direction] = {
                # App rules use avgQoe on 20-100; the solver uses MOS on 1-5.
                "avg_qoe": direction_mos.get(direction, 0.0)
                * self.c["models"]["avg_qoe_per_mos"],
                "service_delay_ms": predicted.rtt_ms,
                # The input model has stall ratio but no duration.  Keep the
                # conversion explicit and auditable instead of relabeling it.
                "stalling_duration_seconds_proxy": predicted.stall_ratio * self.c["models"]["stall_window_seconds"],
                "stall_ratio": predicted.stall_ratio,
                "loss_ratio": predicted.loss_ratio,
                "buffer_ms": predicted.buffer_ms,
                "jitter_ms": predicted.jitter_ms,
                "bitrate_kbps": rate,
                "resolution": (
                    predicted.resolution
                    if not hasattr(self.model, "resolution_available")
                    or self.model.resolution_available(u.business)
                    else None
                ),
                "width": predicted.width or None,
                "height": predicted.height or None,
            }
        return result

    def _action_stream(self, u, direction, stream, rate):
        if direction == "session":
            return replace(stream, bitrate_kbps=rate)
        if hasattr(self.model, "project_stream"):
            return self.model.project_stream(u.business, stream, rate)
        response = self.c["kqi_response"]
        if not response["enabled"] or direction == "session" or abs(rate - getattr(u.current, direction)) <= self.c["solver"]["epsilon_bandwidth_kbps"]:
            return replace(stream, bitrate_kbps=rate)
        base_rate = max(getattr(u.current, direction), 1.0)
        ratio = max(rate / base_rate, 1e-9)
        cap = response["maximum_degradation_ratio"]
        def adjusted(value, elasticity, floor):
            factor = min(cap, max(floor, ratio ** (-elasticity)))
            return value * factor
        resolution, width, height = stream.resolution, stream.width, stream.height
        media = self.c["businesses"][u.business]["media"]
        if response["resolution_adaptation"] and media:
            eligible = [m for m in media if m["min_kbps"] <= rate]
            selected = max(eligible or media[:1], key=lambda m: (m["resolution"], m["min_kbps"]))
            resolution, width, height = selected["resolution"], selected["width"], selected["height"]
        return replace(
            stream,
            bitrate_kbps=rate,
            resolution=resolution,
            width=width,
            height=height,
            min_kbps=min(m["min_kbps"] for m in media) if media else stream.min_kbps,
            max_kbps=max(m["max_kbps"] for m in media) if media else stream.max_kbps,
            rtt_ms=adjusted(stream.rtt_ms, response["rtt_elasticity"], response["minimum_rtt_ratio"]),
            loss_ratio=min(1.0, adjusted(stream.loss_ratio, response["loss_elasticity"], response["minimum_loss_ratio"])),
            stall_ratio=min(1.0, adjusted(stream.stall_ratio, response["stall_elasticity"], response["minimum_stall_ratio"])),
        )

    def quality_guarantee(self, u, b, direction_mos, predicted_kqi):
        if u.package != "vip" or not u.app_id:
            return True, ()
        rule = self.c["applications"].get(u.app_id)
        if not rule:
            return True, ()
        errors = []
        avg_qoe = min((x["avg_qoe"] for x in predicted_kqi.values()), default=100.0)
        if avg_qoe < rule["avg_qoe_min"]:
            errors.append("AVG_QOE")
        if rule.get("service_delay_max_ms") is not None:
            delay = max((x["service_delay_ms"] for x in predicted_kqi.values()), default=0.0)
            if delay >= rule["service_delay_max_ms"]:
                errors.append("SERVICE_DELAY")
        if rule.get("stalling_duration_max_seconds") is not None:
            stall = max((x["stalling_duration_seconds_proxy"] for x in predicted_kqi.values()), default=0.0)
            if stall > rule["stalling_duration_max_seconds"]:
                errors.append("STALLING_DURATION")
        directional_floors = rule.get("bandwidth_min_kbps_by_direction")
        if directional_floors:
            for direction, floor in directional_floors.items():
                if getattr(b, direction) < floor:
                    errors.append("BANDWIDTH_" + direction.upper())
        else:
            direction = rule["bandwidth_direction"]
            if getattr(b, direction) < rule["bandwidth_min_kbps"]:
                errors.append("BANDWIDTH_" + direction.upper())
        return not errors, tuple(errors)

    def decision_score(self, u, action):
        if self.c["policy"]["objective"] != "vip_guarantee":
            return action.h
        if u.package == "vip":
            return (self.c["utility"]["vip_guarantee_bonus"] if action.quality_guarantee_met else 0.0) + action.h
        return action.h * self.c["utility"]["normal_residual_factor"]

    def hard_errors(self, u, b, direction_mos, anchor=False):
        tol = self.c["solver"]["epsilon_bandwidth_kbps"]
        eps = self.c["solver"]["epsilon_mos"]
        errors = []
        for d in ("ul", "dl"):
            rate = getattr(b, d)
            contract = getattr(u.contract, d)
            if rate < contract - tol:
                errors.append("CONTRACT_" + d)
            if not anchor:
                basic_floor = self.c["business_basic_kbps"].get(u.business, {}).get(d, 0.0)
                if basic_floor and rate < basic_floor - tol:
                    errors.append("BASIC_FLOOR_" + d)
            if d in u.streams:
                s = u.streams[d]
                media = self.c["businesses"][u.business]["media"]
                lower = min((m["min_kbps"] for m in media), default=s.min_kbps) if self.c["kqi_response"]["enabled"] else s.min_kbps
                upper = max((m["max_kbps"] for m in media), default=s.max_kbps) if self.c["kqi_response"]["enabled"] else s.max_kbps
                if rate < lower - tol or rate > upper + tol:
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
        if not anchor:
            cap = self.c["policy"].get("normal_request_mos_cap")
            if (
                cap is not None
                and u.package not in self.c["policy"]["evaluation_packages"]
                and direction_mos
                and min(direction_mos.values()) > cap + eps
            ):
                errors.append("NORMAL_REQUEST_CAP")
        return errors

    def make_action(self, u, b, budget=None, anchor=False):
        if budget:
            budget.consume(kind="candidate")
        direction = {}
        for d, s in u.streams.items():
            rate = s.bitrate_kbps if d == "session" else getattr(b, d)
            media = self.c["businesses"][u.business]["media"]
            lower = min((m["min_kbps"] for m in media), default=s.min_kbps) if self.c["kqi_response"]["enabled"] else s.min_kbps
            upper = max((m["max_kbps"] for m in media), default=s.max_kbps) if self.c["kqi_response"]["enabled"] else s.max_kbps
            if hasattr(self.model, "rate_bounds"):
                lookup_lower, lookup_upper = self.model.rate_bounds(u.business, s.phase)
                lower, upper = max(lower, lookup_lower), min(upper, lookup_upper)
            if not lower <= rate <= upper:
                return None
            predicted_stream = self._action_stream(u, d, s, rate)
            direction[d] = self.model.forward(
                u.business, predicted_stream, budget
            )
        if self.hard_errors(u, b, direction, anchor):
            return None
        mos = min(direction.values()) if direction else None
        predicted_kqi = self._predicted_kqi(u, b, direction)
        quality_met, quality_errors = self.quality_guarantee(u, b, direction, predicted_kqi)
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
        evaluated = u.package in self.c["policy"]["evaluation_packages"]
        basic = not evaluated or all(
            m + eps >= u.direction_baselines.get(d, u.baseline)
            for d, m in direction.items()
        )
        target = not evaluated or all(
            m + eps >= u.direction_targets.get(d, u.target)
            for d, m in direction.items()
        )
        gap = 0.0 if not evaluated else max(
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
            predicted_kqi,
            quality_met,
            quality_errors,
            evaluated,
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
        if self.c["policy"]["objective"] == "vip_guarantee":
            vip = [(u, a) for u, a in zip(users, actions) if u.package == "vip"]
            return (
                sum(not a.quality_guarantee_met for _, a in vip),
                sum(a.gap for _, a in vip),
                -sum(a.h for _, a in vip),
                -h,
            )
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
