"""Before/after reports; a retained feasible allocation is distinct from convergence."""


def compare(scene, result, policy):
    before = [policy.make_action(u, u.current, anchor=True) for u in scene.users]
    after = result.decisions
    users = scene.users
    keys = [i for i, u in enumerate(users) if u.observed_mos is not None]

    def stats(actions, current=False):
        if not current and not actions:
            return None
        scores = [users[i].observed_mos if current else actions[i].mos for i in keys]
        valid = all(actions)
        return {
            "基本保障不足人数": sum(not actions[i].basic_met for i in keys) if valid else None,
            "目标达标人数": sum(actions[i].target_met for i in keys) if valid else None,
            "会话MOS均值": sum(scores) / len(scores) if scores else None,
            "加权MOS总和": sum(policy.weight(users[i]) * policy.c["policy"]["weight_reference"] * m for i, m in zip(keys, scores)) if scores else None,
            "上行占用Mbps": sum(u.current.ul for u in users)/1000 if current else sum(a.bandwidth.ul for a in actions)/1000,
            "下行占用Mbps": sum(u.current.dl for u in users)/1000 if current else sum(a.bandwidth.dl for a in actions)/1000,
        }

    initial = stats(before, True)
    final = stats(after)
    rows = [{"metric": name, "before": value,
             "after": final[name] if final else None,
             "change": final[name]-value if final and final[name] is not None and value is not None else None}
            for name, value in initial.items()]
    changes = [after[i].mos-users[i].observed_mos for i in keys] if after else []
    eps = policy.c["metrics"]["epsilon_mos_change"]
    return {"scene_id": scene.scene_id, "run_status": result.run_status,
            "stop_reason": result.stop_reason, "has_feasible_solution": bool(after),
            "mos_users": len(keys), "rows": rows,
            "improved": sum(x>eps for x in changes) if after else None,
            "worsened": sum(x < -eps for x in changes) if after else None,
            "unchanged": sum(abs(x)<=eps for x in changes) if after else None,
            "max_mos_drop": max([0]+[-x for x in changes]) if after and keys else None}


def render(report):
    def fmt(value):
        if value is None:
            return "—"
        return str(value) if isinstance(value, int) else f"{value:.3f}"

    lines = [f"### {report['scene_id']}：{'成功' if report['run_status']=='SUCCESS' else '失败'}（{report['stop_reason']}）", "",
             "失败时下表为保留的可行方案，不表示已收敛。" if report['run_status']=='FAILED' and report['has_feasible_solution'] else "MOS及保障人数仅统计MOS评价用户；无可行方案时求解后列为空。",
             "", "| 指标 | 求解前 | 求解后 | 变化（后−前） |", "| --- | ---: | ---: | ---: |"]
    for row in report['rows']:
        lines.append(f"| {row['metric']} | {fmt(row['before'])} | {fmt(row['after'])} | {fmt(row['change'])} |")
    lines += ["", f"MOS评价人数：{report['mos_users']}；改善：{fmt(report['improved'])}；恶化：{fmt(report['worsened'])}；不变：{fmt(report['unchanged'])}；最大MOS降幅：{fmt(report['max_mos_drop'])}。", ""]
    return "\n".join(lines)
