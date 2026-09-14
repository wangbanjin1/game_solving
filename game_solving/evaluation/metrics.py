"""Separate synthetic facts, algorithm outcomes and evidence-qualified WGR."""

from game_solving.evaluation.validation import total


def evaluate(scene, result, reference, config, policy):
    labels = {
        "scene_id": scene.scene_id,
        "model_hash": scene.model_hash,
        "source": "synthetic",
        "evaluation_environment": "matched_model",
        "certificates": result.certificates,
        "solution_status": result.solution_status,
        "run_status": result.run_status,
        "stop_reason": result.stop_reason,
    }
    metric = {
        "scene_id": scene.scene_id,
        "WGR_exact": None,
        "WGR_reason": "reference_not_exact",
        "N_improved": 0,
        "N_worsened": 0,
        "N_unchanged": 0,
        "newly_met": 0,
        "lost_met": 0,
        "weighted_mos_current": 0.0,
        "weighted_mos_algorithm": 0.0,
    }
    if not result.decisions:
        return labels, {**metric, "WGR_reason": "algorithm_has_no_feasible_solution"}
    current = []
    count = met = 0
    for user, a in zip(scene.users, result.decisions):
        old = policy.make_action(user, user.current, anchor=True)
        current.append(old)
        if a.mos is None:
            continue
        count += 1
        met += a.target_met
        difference = a.mos - user.observed_mos
        eps = config["metrics"]["epsilon_mos_change"]
        metric[
            (
                "N_improved"
                if difference > eps
                else "N_worsened" if difference < -eps else "N_unchanged"
            )
        ] += 1
        weight = policy.weight(user) * config["policy"]["weight_reference"]
        metric["weighted_mos_current"] += weight * user.observed_mos
        metric["weighted_mos_algorithm"] += weight * a.mos
        if old:
            metric["newly_met"] += int(not old.target_met and a.target_met)
            metric["lost_met"] += int(old.target_met and not a.target_met)
    metric["weighted_mos_gain"] = (
        metric["weighted_mos_algorithm"] - metric["weighted_mos_current"]
    )
    metric["net_met"] = metric["newly_met"] - metric["lost_met"]
    metric["target_met_users"] = met
    metric["key_users"] = count
    labels["target_status"] = (
        "NOT_APPLICABLE"
        if not count
        else "ALL_MET" if met == count else "PARTIAL_MET" if met else "NONE_MET"
    )
    metric["allocated_ul_kbps"] = total(result.decisions).ul
    metric["allocated_dl_kbps"] = total(result.decisions).dl
    metric["H_algorithm"] = sum(a.h for a in result.decisions)
    metric["guarantee_vector"] = policy.violation_vector(scene.users, result.decisions)
    if reference.get("status") == "exact_discrete":
        denominator = reference["U_reference"] - metric["weighted_mos_current"]
        if not all(current) or (
            result.mode == "NORMAL" and not all(a.basic_met for a in current)
        ):
            metric["WGR_reason"] = "invalid_baseline"
        elif not total(current).fits(
            scene.available, config["solver"]["epsilon_bandwidth_kbps"]
        ):
            metric["WGR_reason"] = "invalid_baseline_capacity"
        elif denominator <= config["metrics"]["denominator_epsilon"]:
            metric["WGR_reason"] = "no_positive_denominator"
        else:
            metric["WGR_exact"] = metric["weighted_mos_gain"] / denominator
            metric["WGR_reason"] = None
            if metric["WGR_exact"] > 1 + config["solver"]["epsilon_mos"]:
                raise AssertionError("WGR_COMPARABILITY_FAILURE")
    return labels, metric
