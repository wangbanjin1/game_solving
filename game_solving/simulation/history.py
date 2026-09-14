"""Seeded past bandwidth snapshots, constrained before MOS observations are generated."""

import math
from dataclasses import asdict, replace

from game_solving.domain.entities import Bandwidth
from game_solving.evaluation.validation import check_actions, total
from game_solving.optimization.feasibility import minimum_action
from game_solving.optimization.history import time_weighted_mos
from .population import rng_for


class HistoryGenerator:
    def __init__(self, config, policy):
        self.c = config
        self.policy = policy

    def generate(self, scene, scene_index, budget):
        cfg = self.c["generation"]["history"]
        if not cfg["enabled"]:
            return scene, None
        quantum = self.c["solver"]["bandwidth_quantum_kbps"]
        window = self.c["utility"]["history_window_seconds"]
        floors = []
        chosen = []
        for user in scene.users:
            budget.consume(kind="history_initialization")
            floor, reason = minimum_action(user, "hard", self.policy, budget)
            current = self.policy.make_action(user, user.current, budget)
            if floor is None or current is None:
                raise ValueError("HISTORY_REQUIRES_FEASIBLE_CURRENT: " + str(reason))
            floors.append(floor.bandwidth)
            chosen.append(current)
        if check_actions(scene, chosen, self.policy, budget):
            raise ValueError("HISTORY_REQUIRES_FEASIBLE_CURRENT")

        observations = {u.user_id: [] for u in scene.users}
        frames = []
        used = total(chosen)
        for period in range(cfg["periods"]):
            budget.consume(kind="history_period")
            rng = rng_for(self.c["seed"], scene_index, "history", period)
            order = list(range(len(scene.users)))
            rng.shuffle(order)
            for i in order:
                budget.consume(kind="history_proposal")
                user = scene.users[i]
                old = chosen[i]
                rates = asdict(old.bandwidth)
                directions = [d for d in ("ul", "dl") if d in user.streams]
                if not user.bitrate_adaptation:
                    directions = []
                rng.shuffle(directions)
                for direction in directions:
                    stream = user.streams[direction]
                    lower = getattr(floors[i], direction)
                    upper = min(
                        stream.max_kbps,
                        getattr(scene.available, direction)
                        - getattr(used, direction)
                        + getattr(old.bandwidth, direction),
                    )
                    if user.cap is not None:
                        upper = min(upper, getattr(user.cap, direction))
                    if user.total_cap_kbps is not None:
                        other = "dl" if direction == "ul" else "ul"
                        upper = min(upper, user.total_cap_kbps - rates[other])
                    low_tick = math.ceil(lower / quantum)
                    high_tick = math.floor(upper / quantum)
                    if low_tick > high_tick:
                        continue
                    wanted = rates[direction] * rng.uniform(*cfg["bandwidth_multiplier"])
                    tick = min(high_tick, max(low_tick, math.floor(wanted / quantum)))
                    rates[direction] = tick * quantum
                action = self.policy.make_action(user, Bandwidth(**rates), budget)
                # A custom model or a rounding boundary may reject a proposal;
                # keeping the preceding verified action preserves feasibility.
                if action is not None:
                    used = used + action.bandwidth - old.bandwidth
                    chosen[i] = action
            if check_actions(scene, chosen, self.policy, budget):
                raise AssertionError("GENERATED_HISTORY_INVALID")
            start = (period - cfg["periods"]) * cfg["interval_seconds"]
            end = (period + 1 - cfg["periods"]) * cfg["interval_seconds"]
            records = []
            for user, action in zip(scene.users, chosen):
                budget.consume(kind="history_observation")
                records.append({
                    "user_id": user.user_id,
                    "bandwidth": asdict(action.bandwidth),
                    "mos": action.mos,
                    "direction_mos": action.direction_mos,
                })
                if action.mos is not None:
                    observations[user.user_id].append({
                        "start": start, "end": end, "mos": action.mos,
                        "model_hash": scene.model_hash,
                    })
            frames.append({"start": start, "end": end, "users": records})

        enriched = []
        for user in scene.users:
            budget.consume(len(observations[user.user_id]) + 1, kind="history_aggregation")
            mean = time_weighted_mos(observations[user.user_id], 0.0, window, scene.model_hash)
            enriched.append(replace(user, history_mos=mean))
        audit = {
            "scene_id": scene.scene_id,
            "source": "synthetic",
            "mode": "bandwidth_random_walk_fixed_environment",
            "model_hash": scene.model_hash,
            "now": 0.0,
            "window_seconds": window,
            "frames": frames,
        }
        return replace(scene, users=tuple(enriched)), audit
