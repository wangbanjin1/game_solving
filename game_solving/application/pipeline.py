"""Use cases for generate/solve/run, reports and independently reloaded validation."""

import logging
import platform
import hashlib
from dataclasses import asdict
from pathlib import Path
from game_solving.domain.entities import scene_from_dict
from game_solving.domain.validation import validate_scene
from game_solving.infrastructure.config import digest, model_hash
from game_solving.infrastructure.json_store import JsonStore, read_jsonl
from game_solving.evaluation.validation import check_actions
from game_solving.evaluation.reference import evaluate_reference
from game_solving.evaluation.metrics import evaluate
from game_solving.evaluation.history import validate_history
from .services import Services
from game_solving.evaluation.comparison import compare, render

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, config, services=None):
        self.c = config
        self.services = services or Services(config)

    def execute(self, command, output, input_path=None):
        if command == "solve" and input_path is None:
            raise ValueError("SOLVE_REQUIRES_INPUT")
        logger.info("任务开始：%s，输出目录=%s", command, output)
        store = JsonStore(output)
        c = self.c
        manifest = {
            "complete": False,
            "schema_version": "2.0",
            "source": "synthetic",
            "config_hash": digest(c),
            "model_hash": model_hash(c),
            "seed": c["seed"],
            "python": platform.python_version(),
            "source_hash": digest(
                {
                    str(
                        p.relative_to(Path(__file__).resolve().parents[1])
                    ): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(Path(__file__).resolve().parents[1].rglob("*.py"))
                }
            ),
            "input_hash": digest(read_jsonl(input_path)) if input_path else None,
        }
        store.write("manifest.json", manifest)
        store.write("resolved_config.json", c)
        if command in ("run", "generate"):
            scenes, audits, report = self.services.generator().generate()
            store.write("solver_inputs.jsonl", scenes, True)
            store.write(
                "scenes.jsonl",
                [{"snapshot": asdict(s), "private": a} for s, a in zip(scenes, audits)],
                True,
            )
            store.write("generation_report.json", report)
            if c["generation"]["history"]["enabled"]:
                store.write("history.jsonl", [a["history"] for a in audits], True)
            store.write(
                "source_catalog.json",
                {
                    "ranges": c["ranges"],
                    "businesses": c["businesses"],
                    "proxy": "identity_demo",
                    "calibrated": False,
                },
            )
            scenes = [
                scene_from_dict(row)
                for row in read_jsonl(store.path / "solver_inputs.jsonl")
            ]
            logger.info("生成数据已写出，开始重读校验")
            validated = 0
            history_validated = 0
            if c["generation"]["history"]["enabled"]:
                histories = read_jsonl(store.path / "history.jsonl")
                if len(histories) != len(scenes):
                    raise AssertionError("HISTORY_SCENE_COUNT_MISMATCH")
                for scene, history in zip(scenes, histories):
                    history_validated += validate_history(scene, history, self.services.policy)
            for scene in scenes:
                validate_scene(scene, c)
                actions = [
                    self.services.policy.make_action(u, u.current, anchor=True)
                    for u in scene.users
                ]
                if not all(actions) or check_actions(
                    scene, actions, self.services.policy, recompute=True
                ):
                    raise AssertionError("GENERATED_SCENE_INVALID")
                for u, a in zip(scene.users, actions):
                    if a.mos != u.observed_mos:
                        raise AssertionError("GENERATED_MOS_MISMATCH")
                validated += len(scene.users)
            store.write(
                "validation_report.json",
                {
                    "valid": True,
                    "roles_validated": validated,
                    "serialized_reload": True,
                    "complete_generation": report["failed"] == 0,
                    "history_observations_validated": history_validated,
                },
            )
            logger.info("生成数据校验通过：%d 人", validated)
            if report["failed"] and not c["allow_partial"]:
                logger.error(
                    "生成未完整成功，停止后续流程；详见 generation_report.json"
                )
                manifest.update(complete=True, outcome="GENERATION_FAILED")
                store.write("manifest.json", manifest)
                return 2, report
        else:
            scenes = [scene_from_dict(row) for row in read_jsonl(input_path)]
            if not scenes:
                raise ValueError("EMPTY_INPUT")
        results = []
        labels = []
        metrics = []
        references = []
        comparisons = []
        if command in ("run", "solve"):
            logger.info("开始求解：共 %d 个场景", len(scenes))
            for index, scene in enumerate(scenes, 1):
                logger.info(
                    "求解场景 %d/%d：%s，人数=%d",
                    index,
                    len(scenes),
                    scene.scene_id,
                    len(scene.users),
                )
                result = self.services.solver().solve(scene)
                logger.info(
                    "求解完成 %d/%d：%s，停止=%s，轮数=%d，工作量=%d，耗时=%.1f ms",
                    index,
                    len(scenes),
                    "成功" if result.run_status == "SUCCESS" else "失败",
                    result.stop_reason,
                    result.iterations,
                    result.total_steps,
                    result.elapsed_ms,
                )
                if c["reference"]["enabled"]:
                    logger.debug("开始独立参考计算：%s", scene.scene_id)
                reference = evaluate_reference(scene, result, c, self.services.policy)
                if c["reference"]["enabled"]:
                    logger.debug(
                        "独立参考完成：%s，状态=%s", scene.scene_id, reference["status"]
                    )
                label, metric = evaluate(
                    scene, result, reference, c, self.services.policy
                )
                comparison = compare(scene, result, self.services.policy)
                comparisons.append(comparison)
                logger.info("\n%s", render(comparison))
                results.append(result)
                labels.append(label)
                metrics.append(metric)
                references.append({"scene_id": scene.scene_id, **reference})
            store.write("comparison.jsonl", comparisons, True)
            report_path = store.path / "comparison.md"
            report_tmp = store.path / "comparison.md.tmp"
            report_tmp.write_text("# 求解前后对比\n\n" + "\n".join(render(item) for item in comparisons), encoding="utf-8")
            report_tmp.replace(report_path)
            store.write("solve_results.jsonl", results, True)
            store.write("run_labels.jsonl", labels, True)
            store.write("reference_results.jsonl", references, True)
            store.write("metrics.jsonl", metrics, True)
            store.write(
                "static_labels.jsonl",
                [
                    {
                        "scene_id": s.scene_id,
                        "current_hard_feasible": (
                            not check_actions(
                                s,
                                [
                                    self.services.policy.make_action(
                                        u, u.current, anchor=True
                                    )
                                    for u in s.users
                                ],
                                self.services.policy,
                            )
                            if all(
                                self.services.policy.make_action(u, u.current)
                                for u in s.users
                            )
                            else False
                        ),
                        "evidence": r.certificates,
                    }
                    for s, r in zip(scenes, results)
                ],
                True,
            )
        summary = {
            "scenes": len(scenes),
            "successful_scenes": sum(r.run_status == "SUCCESS" for r in results),
            "failed_scenes": sum(r.run_status == "FAILED" for r in results),
            "feasible_scenes": sum(bool(r.decisions) for r in results),
            "statuses": {
                name: sum(r.solution_status == name for r in results)
                for name in sorted({r.solution_status for r in results})
            },
            "stops": {
                name: sum(r.stop_reason == name for r in results)
                for name in sorted({r.stop_reason for r in results})
            },
            "max_iterations": max((r.iterations for r in results), default=0),
            "max_steps": max((r.total_steps for r in results), default=0),
            "max_solver_ms": max((r.elapsed_ms for r in results), default=0),
            "weighted_mos_gain": sum(m.get("weighted_mos_gain", 0) for m in metrics),
        }
        store.write("summary.json", summary)
        code = (
            0
            if command == "generate" or summary["successful_scenes"] == len(scenes)
            else 3
        )
        manifest.update(
            complete=True, outcome="COMPLETE" if code == 0 else "SOLVE_FAILED"
        )
        store.write("manifest.json", manifest)
        logger.info(
            "任务完成：%s，场景数=%d，退出码=%d，结果目录=%s",
            command,
            len(scenes),
            code,
            output,
        )
        return code, summary
