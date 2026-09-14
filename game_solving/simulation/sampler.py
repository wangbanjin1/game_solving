"""Conditional MOS sampling: freeze environment, invert rate, forward-check actual band."""

import math
from dataclasses import replace
from game_solving.domain.entities import User, Stream, Bandwidth


def band(mos, target, c):
    if mos is None:
        return None
    if mos < target - c["generation"]["delta_severe"]:
        return "severe"
    if mos < target:
        return "unmet"
    if mos < target + c["generation"]["delta_over"]:
        return "met"
    return "over"


def interval(target, requested, c):
    lo, hi = c["models"]["minimum_mos"], c["models"]["maximum_mos"]
    if requested == "severe":
        hi = min(
            hi, math.nextafter(target - c["generation"]["delta_severe"], -math.inf)
        )
    if requested == "unmet":
        lo = max(lo, target - c["generation"]["delta_severe"])
        hi = min(hi, math.nextafter(target, -math.inf))
    if requested == "met":
        lo = max(lo, target)
        hi = min(hi, math.nextafter(target + c["generation"]["delta_over"], -math.inf))
    if requested == "over":
        lo = max(lo, target + c["generation"]["delta_over"])
    return lo, hi


class ConditionalSampler:
    def __init__(self, config, model, policy):
        self.c = config
        self.model = model
        self.policy = policy

    def pick_metric(self, name, state, rng):
        if rng.random() < self.c["generation"]["mixed_probability"]:
            state = rng.choice(list(self.c["generation"]["quality_probs"]))
        lo, hi = self.c["ranges"][name][state]
        if state != "good":
            lo = math.nextafter(lo, hi)
        return rng.uniform(lo, hi)

    def environment(self, business, rng):
        c = self.c
        b = c["businesses"][business]
        state = rng.choices(
            list(c["generation"]["quality_probs"]),
            weights=list(c["generation"]["quality_probs"].values()),
        )[0]
        media = (
            rng.choice(b["media"])
            if b["media"]
            else {
                "min_kbps": b["quota_ul_kbps"],
                "max_kbps": b["quota_ul_kbps"],
                "resolution": 0,
                "width": 0,
                "height": 0,
            }
        )
        phase = c["generation"]["stream_phase"]
        if phase == "initial" and business == "meeting" and not b["initial_enabled"]:
            raise ValueError("MEETING_INITIAL_BUFFER_UNAVAILABLE")
        return Stream(
            media["min_kbps"],
            media["resolution"],
            media["width"],
            media["height"],
            self.pick_metric(b["rtt_range"], state, rng),
            self.pick_metric("loss", state, rng),
            self.pick_metric("stall", state, rng),
            media["min_kbps"],
            media["max_kbps"],
            (
                self.pick_metric("buffer", state, rng)
                if phase == "initial" and b["initial_enabled"]
                else None
            ),
            phase,
            self.pick_metric("jitter", state, rng) if business == "game" else None,
        )

    def propose(self, person, requested, rng, budget):
        c = self.c
        p = c["policy"]
        business = person["business"]
        b = c["businesses"][business]
        target = p["targets"][person["package"]]
        baseline = p["baselines"][person["package"]]
        streams = {}
        rates = {"ul": b["quota_ul_kbps"], "dl": b["quota_dl_kbps"]}
        direction_mos = {}
        proposal_targets = {}
        if b["mos_type"]:
            directions = list(b["media_directions"]) or ["session"]
            rng.shuffle(directions)
            for d in directions:
                s = self.environment(business, rng)
                if d == "session":
                    streams[d] = s
                    direction_mos[d] = self.model.forward(business, s, budget)
                    continue
                contract = p["contract_" + d + "_kbps"]
                s = replace(
                    s,
                    min_kbps=max(s.min_kbps, contract),
                    bitrate_kbps=max(s.min_kbps, contract),
                )
                if s.min_kbps > s.max_kbps:
                    return None, "CONTRACT_EXCEEDS_MEDIA_MAX"
                lower = self.model.forward(business, s, budget)
                upper = self.model.forward(
                    business, replace(s, bitrate_kbps=s.max_kbps), budget
                )
                low, high = interval(target, requested, c)
                if direction_mos:
                    low = max(direction_mos.values())
                    high = upper
                low = max(low, lower)
                high = min(high, upper)
                if low > high:
                    return None, "MOS_BAND_UNREACHABLE_OR_LOW_ACCEPTANCE"
                wanted = rng.uniform(low, high)
                inverse = self.model.inverse(business, wanted, s, budget)
                if not inverse.feasible:
                    return None, inverse.reason
                rates[d] = inverse.bandwidth_kbps
                streams[d] = replace(s, bitrate_kbps=inverse.bandwidth_kbps)
                direction_mos[d] = inverse.mos
                proposal_targets[d] = wanted
            actual = min(direction_mos.values())
            realized = band(actual, target, c)
            if requested is not None and realized != requested:
                return None, "QUANTIZED_BAND_MISMATCH"
        else:
            rates = {
                d: max(rate, p["contract_" + d + "_kbps"]) for d, rate in rates.items()
            }
            rates["dl"] *= rng.uniform(*c["generation"]["non_key_current_multiplier"])
            rates["dl"] = (
                math.ceil(rates["dl"] / c["solver"]["bandwidth_quantum_kbps"])
                * c["solver"]["bandwidth_quantum_kbps"]
            )
            actual = None
            realized = None
        user = User(
            **person,
            current=Bandwidth(**rates),
            streams=streams,
            observed_mos=actual,
            target=target,
            baseline=baseline,
            contract=Bandwidth(p["contract_ul_kbps"], p["contract_dl_kbps"]),
            bitrate_adaptation=p["bitrate_adaptation"],
            hard_mos=p["hard_mos"],
            allow_soft_degrade=p["allow_soft_degrade"],
        )
        action = self.policy.make_action(user, user.current, budget, True)
        if action is None:
            return None, "CURRENT_HARD_CONSTRAINT_CONFLICT"
        audit = {
            "user_id": user.user_id,
            "requested_band": requested,
            "realized_band": realized,
            "quota_status": "met",
            "proposal_targets": proposal_targets,
            "direction_mos": direction_mos,
            "position_true": user.position,
            "tolerance_true": user.tolerance,
            "w_eval": self.policy.weight(user) * p["weight_reference"],
            "range_source": b["range_source"],
            "rtt_mapping": "identity_demo",
            "resolution_source": "vertical_pixels_assumed",
        }
        return (user, audit), None
