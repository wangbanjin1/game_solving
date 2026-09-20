"""Generation orchestration, explicit failed scenes and private audit separation."""

import logging
import copy
from dataclasses import replace, asdict
from collections import defaultdict, Counter
from game_solving.domain.entities import Scene, Bandwidth
from game_solving.infrastructure.config import model_hash
from game_solving.optimization.budget import Budget, BudgetExceeded
from .summary import log_scene_summary
from .population import generate_people, rng_for, allocate
from .sampler import ConditionalSampler, interval
from .assembler import assemble, AssemblyFailure
from .history import HistoryGenerator

logger = logging.getLogger(__name__)


class DatasetGenerator:
    def __init__(self, config, model, policy):
        self.c = config
        self.sampler = ConditionalSampler(config, model, policy)
        self.history = HistoryGenerator(config, policy)

    def generate(self):
        c = self.c
        logger.info(
            "开始生成：场景数=%d，每场景人数=%d",
            c["num_scenes"],
            c["population"]["users_per_cell"],
        )
        scenes = []
        audits = []
        failures = []
        rejects = Counter()
        cell = c["cell"]
        capacity = Bandwidth(cell["capacity_ul_kbps"], cell["capacity_dl_kbps"])
        unmanaged = Bandwidth(cell["unmanaged_ul_kbps"], cell["unmanaged_dl_kbps"])
        reserve = Bandwidth(cell["reserve_ul_kbps"], cell["reserve_dl_kbps"])
        for index in range(c["num_scenes"]):
            logger.info("生成场景 %d/%d", index + 1, c["num_scenes"])
            people = generate_people(c, index)
            groups = defaultdict(list)
            requests = {}
            for person in people:
                if c["businesses"][person["business"]]["mos_type"] and person["package"] in c["policy"]["evaluation_packages"]:
                    groups[(person["package"], person["business"])].append(person)
            for key, group in groups.items():
                quotas = (
                    allocate(
                        len(group),
                        c["generation"]["compliance_probs_by_package"].get(key[0], c["generation"]["compliance_probs"]),
                        rng_for(c["seed"], index, "quota", *key),
                    )
                    if c["generation"]["quota_mode"] == "specified"
                    else [None] * len(group)
                )
                requests.update({p["user_id"]: q for p, q in zip(group, quotas)})
            reason = "NO_STATE"
            scene_budget = Budget(
                c["generation"]["max_model_evaluations"], float("inf")
            )
            for attempt in range(c["generation"]["scene_max_attempts"]):
                try:
                    pools = []
                    for person in people:
                        pool = []
                        requested = requests.get(person["user_id"])
                        rng = rng_for(
                            c["seed"], index, attempt, "kqi", person["user_id"]
                        )
                        empty_band = False
                        if requested:
                            lo, hi = interval(
                                c["policy"]["targets"][person["package"]], requested, c
                            )
                            if person["business"] == "game":
                                hi = min(hi, c["models"]["game_quality"])
                            if lo > hi:
                                empty_band = True
                                if c["generation"]["strict_quotas"]:
                                    raise AssemblyFailure("EMPTY_REQUESTED_MOS_BAND")
                        for tries in range(
                            0 if empty_band else c["generation"]["role_max_attempts"]
                        ):
                            scene_budget.consume(kind="proposal")
                            result, error = self.sampler.propose(
                                person, requested, rng, scene_budget
                            )
                            if result:
                                result[1]["proposal_attempts"] = tries + 1
                                pool.append(result)
                                if len(pool) >= c["generation"]["state_pool_size"]:
                                    break
                            else:
                                rejects[error] += 1
                        if not pool and not c["generation"]["strict_quotas"]:
                            for _ in range(c["generation"]["role_max_attempts"]):
                                scene_budget.consume(kind="proposal")
                                result, error = self.sampler.propose(
                                    person, None, rng, scene_budget
                                )
                                if result:
                                    result[1].update(
                                        requested_band=requested, quota_status="gap"
                                    )
                                    pool.append(result)
                                    if len(pool) >= c["generation"]["state_pool_size"]:
                                        break
                        if not pool:
                            raise AssemblyFailure(
                                "MOS_BAND_UNREACHABLE_OR_LOW_ACCEPTANCE:"
                                + person["user_id"]
                            )
                        pools.append(pool)
                    if c["generation"]["capacity_mode"] == "current_headroom":
                        selected, nodes = [pool[0] for pool in pools], 0
                        used = Bandwidth(sum(x[0].current.ul for x in selected), sum(x[0].current.dl for x in selected))
                        ratio = min(c["generation"]["headroom_ratios"])
                        capacity = Bandwidth(used.ul * (1 + ratio), used.dl * (1 + ratio)) + unmanaged + reserve
                    else:
                        selected, nodes = assemble(
                            pools, capacity - unmanaged - reserve,
                            c["generation"]["assembly_max_nodes"], scene_budget,
                        )
                    sid = f"scene_{index:06d}"
                    scene = Scene(
                        sid,
                        model_hash(c),
                        capacity,
                        tuple(x[0] for x in selected),
                        unmanaged,
                        reserve,
                    )
                    scene, history = self.history.generate(scene, index, scene_budget)
                    split = rng_for(c["seed"], index, "split").choices(
                        list(c["generation"]["split_probs"]),
                        weights=list(c["generation"]["split_probs"].values()),
                    )[0]
                    scenes.append(scene)
                    audits.append(
                        {
                            "scene_id": sid,
                            "split": split,
                            "users": [x[1] for x in selected],
                            "assembly_nodes": nodes,
                            "generation_steps": scene_budget.used,
                            "generation_attempt": attempt + 1,
                            **({"history": history} if history is not None else {}),
                        }
                    )
                    log_scene_summary(scene, audits[-1])
                    break
                except (AssemblyFailure, BudgetExceeded) as exc:
                    reason = str(exc)
                    logger.debug(
                        "场景 %d，第 %d 次尝试未完成：%s",
                        index + 1,
                        attempt + 1,
                        reason,
                    )
                    if isinstance(exc, BudgetExceeded):
                        break
            else:
                failures.append(
                    {
                        "scene_id": f"scene_{index:06d}",
                        "reason": reason,
                        "evidence": "finite_search_or_config",
                        "steps": scene_budget.used,
                    }
                )
                logger.warning("场景 %d 生成失败：%s", index + 1, reason)
                continue
            if len(scenes) == 0 or scenes[-1].scene_id != f"scene_{index:06d}":
                failures.append(
                    {
                        "scene_id": f"scene_{index:06d}",
                        "reason": reason,
                        "evidence": "search_budget",
                        "steps": scene_budget.used,
                    }
                )
                logger.warning("场景 %d 生成失败：%s", index + 1, reason)
        if c["generation"]["capacity_mode"] == "current_headroom":
            variants, variant_audits = [], []
            for scene, audit in zip(scenes, audits):
                used = Bandwidth(sum(u.current.ul for u in scene.users), sum(u.current.dl for u in scene.users))
                for index, ratio in enumerate(c["generation"]["headroom_ratios"]):
                    sid = scene.scene_id + f"_headroom_{index:02d}"
                    capacity = Bandwidth(used.ul * (1 + ratio), used.dl * (1 + ratio)) + scene.unmanaged + scene.reserve
                    variants.append(replace(scene, scene_id=sid, capacity=capacity))
                    cloned = copy.deepcopy(audit)
                    cloned.update(scene_id=sid, base_scene_id=scene.scene_id, headroom_ratio=ratio, current_total=asdict(used))
                    if "history" in cloned:
                        cloned["history"]["scene_id"] = sid
                    variant_audits.append(cloned)
            scenes, audits = variants, variant_audits
            failures = [dict(failure, scene_id=failure["scene_id"] + f"_headroom_{i:02d}") for failure in failures for i in range(len(c["generation"]["headroom_ratios"]))]
        logger.info("生成完成：成功=%d，失败=%d", len(scenes), len(failures))
        report = {
            "throughput_reference": copy.deepcopy(c["generation"]["throughput_reference"]),
            "throughput_reference_role": "sampling_scale_only_not_cell_capacity_or_per_user_demand",
            "high_load_sampling": copy.deepcopy(c["generation"]["high_load_sampling"]),
            "high_load_samples": sum(u.get("high_load_sample", False) for a in audits for u in a["users"]),
            "requested": c["num_scenes"] * (len(c["generation"]["headroom_ratios"]) if c["generation"]["capacity_mode"] == "current_headroom" else 1),
            "successful": len(scenes),
            "failed": len(failures),
            "failures": failures,
            "rejection_counts": dict(rejects),
            "quota_mode": c["generation"]["quota_mode"],
            "quota_gaps": sum(
                u["quota_status"] == "gap" for a in audits for u in a["users"]
            ),
            "realized_bands": dict(
                Counter(
                    u["realized_band"]
                    for a in audits
                    for u in a["users"]
                    if u["realized_band"] is not None
                )
            ),
        }
        return scenes, audits, report
