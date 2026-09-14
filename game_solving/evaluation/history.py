"""Recompute serialized past observations and the scalar supplied to the solver."""

from game_solving.domain.entities import Bandwidth
from game_solving.domain.validation import number
from game_solving.optimization.history import time_weighted_mos
from .validation import check_actions


def validate_history(scene, history, policy):
    if history["scene_id"] != scene.scene_id or history["model_hash"] != scene.model_hash:
        raise ValueError("HISTORY_SCENE_OR_MODEL_MISMATCH")
    if history["source"] != "synthetic" or history["mode"] != "bandwidth_random_walk_fixed_environment":
        raise ValueError("UNSUPPORTED_HISTORY_SOURCE")
    if history["now"] != 0.0 or history["window_seconds"] != policy.c["utility"]["history_window_seconds"]:
        raise ValueError("HISTORY_WINDOW_MISMATCH")
    cfg = policy.c["generation"]["history"]
    frames = history["frames"]
    if len(frames) != cfg["periods"]:
        raise ValueError("HISTORY_PERIOD_COUNT_MISMATCH")
    observations = {u.user_id: [] for u in scene.users}
    count = 0
    for index, frame in enumerate(frames):
        number(frame["start"], "history_start", low=-float("inf"))
        number(frame["end"], "history_end", low=-float("inf"))
        if (
            frame["start"] != (index - len(frames)) * cfg["interval_seconds"]
            or frame["end"] != (index + 1 - len(frames)) * cfg["interval_seconds"]
            or frame["start"] >= frame["end"]
        ):
            raise ValueError("INVALID_HISTORY_INTERVAL")
        if [r["user_id"] for r in frame["users"]] != [u.user_id for u in scene.users]:
            raise ValueError("HISTORY_USER_MISMATCH")
        actions = []
        for user, record in zip(scene.users, frame["users"]):
            bandwidth = Bandwidth(**record["bandwidth"])
            number(bandwidth.ul, "history_ul")
            number(bandwidth.dl, "history_dl")
            action = policy.make_action(user, bandwidth)
            if action is None or action.mos != record["mos"] or action.direction_mos != record["direction_mos"]:
                raise ValueError("HISTORY_MOS_MISMATCH")
            actions.append(action)
            if action.mos is not None:
                observations[user.user_id].append({
                    "start": frame["start"], "end": frame["end"],
                    "mos": action.mos, "model_hash": history["model_hash"],
                })
            count += 1
        if check_actions(scene, actions, policy):
            raise ValueError("HISTORY_CONSTRAINT_VIOLATION")
    for user in scene.users:
        mean = time_weighted_mos(
            observations[user.user_id], history["now"], history["window_seconds"], scene.model_hash
        )
        if mean != user.history_mos:
            raise ValueError("HISTORY_MEAN_MISMATCH")
    return count
