"""Human-readable characteristics of a generated scene; no model evaluation."""

import logging
from collections import Counter

logger = logging.getLogger(__name__)


def log_scene_summary(scene, audit):
    if not logger.isEnabledFor(logging.DEBUG):
        return
    users = scene.users
    def distribution(field):
        return ", ".join(f"{name}={count}" for name, count in sorted(Counter(getattr(u, field) for u in users).items()))

    logger.debug("场景特点 %s：人数=%d；业务[%s]；套餐[%s]", scene.scene_id, len(users), distribution("business"), distribution("package"))
    scores = [u.observed_mos for u in users if u.observed_mos is not None]
    bands = Counter(u["realized_band"] for u in audit["users"] if u["realized_band"] is not None)
    if scores:
        logger.debug("体验 %s：MOS用户=%d，无MOS用户=%d；会话MOS均值=%.3f，范围=%.3f~%.3f；目标达标(含超额)=%d/%d；档位[超额=%d，达标=%d，未达标=%d，严重未达标=%d]", scene.scene_id, len(scores), len(users)-len(scores), sum(scores)/len(scores), min(scores), max(scores), bands["met"]+bands["over"], len(scores), bands["over"], bands["met"], bands["unmet"], bands["severe"])
    else:
        logger.debug("体验 %s：全部为无MOS业务，MOS统计不适用", scene.scene_id)
    for direction, label in (("ul", "上行"), ("dl", "下行")):
        used = sum(getattr(u.current, direction) for u in users)
        available = getattr(scene.available, direction)
        utilization = f"{100 * used / available:.1f}%" if available > 0 else "不适用(可用容量为0)"
        logger.debug("资源 %s：%s当前占用=%.3f Mbps，可用=%.3f Mbps，占用率=%s，余量=%.3f Mbps", scene.scene_id, label, used/1000, available/1000, utilization, (available-used)/1000)
