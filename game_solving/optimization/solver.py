"""Price iteration with certified initialization, shared budgets and feasible incumbents."""

from dataclasses import asdict
import math
import logging
from game_solving.domain.entities import SolveResult
from game_solving.domain.validation import validate_scene
from game_solving.infrastructure.config import model_hash
from game_solving.evaluation.validation import total, check_actions
from .budget import Budget, BudgetExceeded
from .feasibility import certificate
from .candidates import CandidateBuilder
from .coordinator import ResourceCoordinator


logger = logging.getLogger(__name__)


class Solver:
    def __init__(self, config, policy, candidate_provider=None, coordinator=None):
        self.c = config
        self.policy = policy
        self.builder = candidate_provider or CandidateBuilder(config, policy)
        self.coordinator = coordinator or ResourceCoordinator(config, policy)

    def solve(self, scene):
        c = self.c
        p = c["solver"]
        n = len(scene.users)
        # Per user: validator + action construction + at most two forward evaluations; global sum.
        reserve = 4 * n + 1
        budget = Budget(p["max_total_steps"], p["time_budget_ms"], reserve)
        best = []
        certs = {}
        trace = []
        mode = "BEST_EFFORT_SEARCH"
        stop = "MAX_ITERATIONS"
        best_iteration = 0
        previous = None
        seen = set()
        stable = 0
        pool_signature = None
        prices = list(p["lambda_initial"])
        try:
            budget.consume(n, kind="input_validation")
            validate_scene(scene, c)
            if scene.model_hash != model_hash(c):
                raise ValueError("MODEL_CONFIG_MISMATCH")
            logger.debug("输入 %s：人数=%d，可用上下行=(%.3f, %.3f) Mbps；最大轮数=%d，总工作量=%d，搜索时间=%.1f ms", scene.scene_id, n, scene.available.ul/1000, scene.available.dl/1000, p["max_iterations"], p["max_total_steps"], p["time_budget_ms"])
            if logger.isEnabledFor(logging.DEBUG):
                for u in scene.users:
                    logger.debug("输入用户 %s：业务=%s，套餐=%s，当前上下行=(%.3f, %.3f) Mbps，MOS=%s，基本保障=%.2f，目标=%.2f", u.user_id, u.business, u.package, u.current.ul/1000, u.current.dl/1000, u.observed_mos, u.baseline, u.target)
            anchors = [
                self.policy.make_action(u, u.current, budget, True) for u in scene.users
            ]
            if all(a is not None for a in anchors) and not check_actions(
                scene, anchors, self.policy, budget
            ):
                best = anchors
            certs["hard"], hard = certificate(scene, "hard", self.policy, budget)
            logger.debug("预检查 hard：%s", certs["hard"])
            if hard is None:
                stop = "PRECHECK"
                return self._finish(
                    scene,
                    [],
                    certs,
                    trace,
                    budget,
                    mode,
                    stop,
                    best_iteration,
                    "INFEASIBLE_HARD",
                )
            if not best or self.policy.rank(scene.users, hard, mode) < self.policy.rank(
                scene.users, best, mode
            ):
                best = hard
            certs["basic"], basic = certificate(scene, "basic", self.policy, budget)
            logger.debug("预检查 basic：%s", certs["basic"])
            if basic:
                mode = "NORMAL"
                if not all(a.basic_met for a in best) or sum(a.h for a in basic) > sum(
                    a.h for a in best
                ):
                    best = basic
            else:
                mode = "DEGRADED"
            certs["target"], target = certificate(scene, "target", self.policy, budget)
            logger.debug("预检查 target：%s", certs["target"])
            required = [
                [hard[i], basic[i] if basic else None, target[i] if target else None]
                for i in range(n)
            ]
            pools = [
                self.builder.build(u, budget, required[i])
                for i, u in enumerate(scene.users)
            ]
            if mode == "NORMAL":
                pools = [[a for a in pool if a.basic_met] for pool in pools]
            logger.debug("求解模式=%s，候选总数=%d，各用户候选数=%s，候选截断=%s", mode, sum(map(len, pools)), [len(pool) for pool in pools], getattr(self.builder, "truncated", False))
            for k in range(p["max_iterations"]):
                budget.consume(kind="iteration")
                scopes = [
                    self.builder.scope(u, pool, k)
                    for u, pool in zip(scene.users, pools)
                ]
                signature = tuple(tuple(a.action_id for a in pool) for pool in scopes)
                changed = signature != pool_signature
                if changed:
                    previous = None
                    seen = set()
                    stable = 0
                    pool_signature = signature
                raw = []
                for u, pool in zip(scene.users, scopes):
                    candidates = []
                    for a in pool:
                        budget.consume(kind="utility_comparison")
                        cost = (
                            prices[0] * a.bandwidth.ul + prices[1] * a.bandwidth.dl
                        ) / c["utility"]["bandwidth_reference_kbps"]
                        candidates.append(
                            (
                                a.h - cost,
                                a.anchor,
                                -a.bandwidth.ul - a.bandwidth.dl,
                                a.action_id,
                                a,
                            )
                        )
                    raw.append(max(candidates, key=lambda x: x[:4])[-1])
                raw_total = total(raw)
                logger.debug("第 %d 轮个人选择完成：原始需求上下行=(%.3f, %.3f) Mbps，开始资源协调", k+1, raw_total.ul/1000, raw_total.dl/1000)
                repaired, local = self.coordinator.coordinate(
                    scene, raw, scopes, budget, mode, prices
                )
                if repaired is None:
                    repaired = best
                    local = False
                if check_actions(scene, repaired, self.policy, budget):
                    raise AssertionError("COORDINATOR_RETURNED_INFEASIBLE")
                if self.policy.rank(scene.users, repaired, mode) < self.policy.rank(
                    scene.users, best, mode
                ):
                    best = repaired[:]
                    best_iteration = k + 1
                used = total(repaired)
                h = sum(a.h for a in repaired)
                vector = tuple(a.action_id for a in repaired)
                next_prices = [
                    max(
                        0,
                        prices[j]
                        + p["gamma0"]
                        / math.sqrt(k + 1)
                        * (getattr(raw_total, d) - getattr(scene.available, d))
                        / c["utility"]["bandwidth_reference_kbps"],
                    )
                    for j, d in enumerate(("ul", "dl"))
                ]
                metrics = {}
                if previous:
                    old, old_used, old_h, old_v = previous
                    metrics = {
                        "strategy_change": sum(a != b for a, b in zip(vector, old)) / n,
                        "resource_change": max(
                            abs(used.ul - old_used.ul) / max(scene.available.ul, 1),
                            abs(used.dl - old_used.dl) / max(scene.available.dl, 1),
                        ),
                        "utility_change": abs(h - old_h) / max(1, abs(old_h)),
                    }
                    stable = (
                        stable + 1
                        if metrics["strategy_change"] <= p["epsilon_strategy"]
                        and metrics["resource_change"] <= p["epsilon_resource"]
                        and metrics["utility_change"] <= p["epsilon_utility"]
                        and old_v == self.policy.violation_vector(scene.users, repaired)
                        and local
                        else 0
                    )
                trace.append(
                    {
                        "iteration": k + 1,
                        "raw": asdict(raw_total),
                        "allocated": asdict(used),
                        "H": h,
                        "V": self.policy.violation_vector(scene.users, repaired),
                        "prices": prices[:],
                        "next_prices": next_prices,
                        "steps": budget.used,
                        "scope_changed": changed,
                        **metrics,
                    }
                )
                logger.debug("第 %d 轮协调完成：分配上下行=(%.3f, %.3f) Mbps，余量=(%.3f, %.3f) Mbps；H=%.6f，保障向量=%s；价格=%s -> %s；稳定计数=%d，已用工作量=%d，变化指标=%s", k+1, used.ul/1000, used.dl/1000, (scene.available.ul-used.ul)/1000, (scene.available.dl-used.dl)/1000, h, self.policy.violation_vector(scene.users, repaired), prices, next_prices, stable, budget.used, metrics)
                prices = next_prices
                full_scope = all(len(s) == len(pool) for s, pool in zip(scopes, pools))
                if stable >= p["stable_rounds"] and full_scope:
                    checked, local_best = self.coordinator.coordinate(
                        scene, best, pools, budget, mode, prices
                    )
                    if checked and self.policy.rank(
                        scene.users, checked, mode
                    ) < self.policy.rank(scene.users, best, mode):
                        best = checked
                        stable = 0
                    elif local_best:
                        stop = "CONVERGED_LOCAL"
                        break
                if vector in seen and previous and vector != previous[0]:
                    stop = "STOPPED_CYCLE"
                    break
                seen.add(vector)
                previous = (
                    vector,
                    used,
                    h,
                    self.policy.violation_vector(scene.users, repaired),
                )
        except BudgetExceeded as exc:
            stop = exc.reason
            logger.debug("预算触发停止：%s；已完成轮数=%d，已用工作量=%d", stop, len(trace), budget.used)
        return self._finish(
            scene, best, certs, trace, budget, mode, stop, best_iteration
        )

    def _finish(
        self, scene, best, certs, trace, budget, mode, stop, best_iteration, status=None
    ):
        budget.finalizing = True
        errors = (
            check_actions(scene, best, self.policy, budget, recompute=True)
            if best
            else []
        )
        if errors:
            raise AssertionError("FINAL_VALIDATION_FAILED: " + ",".join(errors))
        status = (
            status
            or (
                "FEASIBLE_NORMAL"
                if all(a.basic_met for a in best)
                else "FEASIBLE_DEGRADED"
            )
            if best
            else (status or "NO_FEASIBLE_FOUND")
        )
        result = SolveResult(
            scene.scene_id,
            status,
            stop,
            mode,
            best,
            certs,
            trace,
            len(trace),
            budget.used,
            budget.elapsed_ms,
            best_iteration,
            getattr(self.builder, "truncated", False),
            errors,
        )
        result.run_status = "SUCCESS" if best and stop == "CONVERGED_LOCAL" else "FAILED"
        logger.debug("最终结果 %s：状态=%s，停止=%s，最佳方案记录轮次=%d，终检违规=%s", scene.scene_id, status, stop, best_iteration, errors)
        if logger.isEnabledFor(logging.DEBUG):
            for u, a in zip(scene.users, best):
                logger.debug("最终用户 %s：上下行Mbps=(%.3f, %.3f) -> (%.3f, %.3f)，会话MOS=%s -> %s，方向MOS=%s，基本保障=%s，目标达标=%s", u.user_id, u.current.ul/1000, u.current.dl/1000, a.bandwidth.ul/1000, a.bandwidth.dl/1000, u.observed_mos, a.mos, a.direction_mos, a.basic_met, a.target_met)
        return result
