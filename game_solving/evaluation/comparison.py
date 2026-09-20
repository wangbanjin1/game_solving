"""Before/after reports; a retained feasible allocation is distinct from convergence."""


def compare(scene, result, policy):
    before = [policy.make_action(u, u.current, anchor=True) for u in scene.users]
    after = result.decisions
    users = scene.users
    keys = [i for i, u in enumerate(users) if u.observed_mos is not None]
    vip_keys = [i for i in keys if users[i].package in policy.c["policy"]["evaluation_packages"]]

    def stats(actions, current=False):
        if not current and not actions:
            return None
        scores = [users[i].observed_mos if current else actions[i].mos for i in keys]
        valid = all(actions)
        return {
            "VIP基本保障不足人数": sum(not actions[i].basic_met for i in vip_keys) if valid else None,
            "VIP目标达标人数": sum(actions[i].target_met for i in vip_keys) if valid else None,
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

    def package_change(package):
        indexes = [i for i, user in enumerate(users) if user.package == package]
        mos_indexes = [i for i in indexes if users[i].observed_mos is not None]
        mos_changes = (
            [after[i].mos - users[i].observed_mos for i in mos_indexes]
            if after else []
        )

        def average_delta(field, scale=1.0):
            values = []
            if after:
                for i in indexes:
                    if before[i] is None or after[i] is None:
                        continue
                    for direction, initial_kqi in before[i].predicted_kqi.items():
                        final_kqi = after[i].predicted_kqi.get(direction, {})
                        initial = initial_kqi.get(field)
                        final = final_kqi.get(field)
                        if initial is not None and final is not None:
                            values.append((final - initial) * scale)
            return sum(values) / len(values) if values else None

        initial_mos = [users[i].observed_mos for i in mos_indexes]
        final_mos = [after[i].mos for i in mos_indexes] if after else []
        initial_ul = sum(users[i].current.ul for i in indexes) / 1000
        initial_dl = sum(users[i].current.dl for i in indexes) / 1000
        final_ul = sum(after[i].bandwidth.ul for i in indexes) / 1000 if after else None
        final_dl = sum(after[i].bandwidth.dl for i in indexes) / 1000 if after else None
        evaluated = package in policy.c["policy"]["evaluation_packages"]
        if evaluated:
            eval_indexes = [i for i in indexes if users[i].observed_mos is not None]
            basic_unmet_before = sum(not before[i].basic_met for i in eval_indexes if before[i] is not None)
            basic_unmet_after = sum(not after[i].basic_met for i in eval_indexes if after[i] is not None) if after else None
            target_met_before = sum(before[i].target_met for i in eval_indexes if before[i] is not None)
            target_met_after = sum(after[i].target_met for i in eval_indexes if after[i] is not None) if after else None
        else:
            basic_unmet_before = basic_unmet_after = None
            target_met_before = target_met_after = None
        weight_ref = policy.c["policy"]["weight_reference"]
        weighted_before = sum(policy.weight(users[i]) * weight_ref * users[i].observed_mos for i in mos_indexes) if mos_indexes else None
        weighted_after = sum(policy.weight(users[i]) * weight_ref * after[i].mos for i in mos_indexes) if after and mos_indexes else None
        return {
            "package": package,
            "label": "VIP" if package == "vip" else "普通",
            "users": len(indexes),
            "mos_users": len(mos_indexes),
            "mos_before": sum(initial_mos) / len(initial_mos) if initial_mos else None,
            "mos_after": sum(final_mos) / len(final_mos) if final_mos else None,
            "mos_change": sum(mos_changes) / len(mos_changes) if mos_changes else None,
            "improved": sum(x > eps for x in mos_changes) if after else None,
            "worsened": sum(x < -eps for x in mos_changes) if after else None,
            "unchanged": sum(abs(x) <= eps for x in mos_changes) if after else None,
            "ul_before_mbps": initial_ul,
            "ul_after_mbps": final_ul,
            "ul_change_mbps": final_ul - initial_ul if final_ul is not None else None,
            "dl_before_mbps": initial_dl,
            "dl_after_mbps": final_dl,
            "dl_change_mbps": final_dl - initial_dl if final_dl is not None else None,
            "delay_change_ms": average_delta("service_delay_ms"),
            "loss_change_pct_point": average_delta("loss_ratio", 100.0),
            "stall_change_pct_point": average_delta("stall_ratio", 100.0),
            "buffer_change_ms": average_delta("buffer_ms"),
            "jitter_change_ms": average_delta("jitter_ms"),
            "resolution_change": average_delta("resolution"),
            "basic_unmet_before": basic_unmet_before,
            "basic_unmet_after": basic_unmet_after,
            "basic_unmet_change": (basic_unmet_after - basic_unmet_before) if (basic_unmet_before is not None and basic_unmet_after is not None) else None,
            "target_met_before": target_met_before,
            "target_met_after": target_met_after,
            "target_met_change": (target_met_after - target_met_before) if (target_met_before is not None and target_met_after is not None) else None,
            "weighted_mos_before": weighted_before,
            "weighted_mos_after": weighted_after,
            "weighted_mos_change": (weighted_after - weighted_before) if (weighted_before is not None and weighted_after is not None) else None,
        }

    return {"scene_id": scene.scene_id, "run_status": result.run_status,
            "stop_reason": result.stop_reason, "has_feasible_solution": bool(after),
            "mos_users": len(keys), "rows": rows,
            "improved": sum(x>eps for x in changes) if after else None,
            "worsened": sum(x < -eps for x in changes) if after else None,
            "unchanged": sum(abs(x)<=eps for x in changes) if after else None,
            "max_mos_drop": max([0]+[-x for x in changes]) if after and keys else None,
            "package_changes": [package_change(package) for package in ("vip", "normal")]}


def render(report):
    def fmt(value):
        if value is None:
            return "—"
        return str(value) if isinstance(value, int) else f"{value:.3f}"

    lines = [f"### {report['scene_id']}：{'成功' if report['run_status']=='SUCCESS' else '失败'}（{report['stop_reason']}）", "",
             "失败时下表为保留的可行方案，不表示已收敛。" if report['run_status']=='FAILED' and report['has_feasible_solution'] else "MOS变化统计全部MOS用户；达标和质差保障只评价VIP；无可行方案时求解后列为空。",
             "", "| 指标 | 求解前 | 求解后 | 变化（后−前） |", "| --- | ---: | ---: | ---: |"]
    for row in report['rows']:
        lines.append(f"| {row['metric']} | {fmt(row['before'])} | {fmt(row['after'])} | {fmt(row['change'])} |")
    lines += ["", f"MOS评价人数：{report['mos_users']}；改善：{fmt(report['improved'])}；恶化：{fmt(report['worsened'])}；不变：{fmt(report['unchanged'])}；最大MOS降幅：{fmt(report['max_mos_drop'])}。", ""]
    if report.get("package_changes"):
        lines += ["按套餐汇总：", "", "| 套餐 | 人数 | MOS前 | MOS后 | MOS变化 | 改善/恶化/不变 | UL变化Mbps | DL变化Mbps | 时延变化ms | 丢包率变化百分点 | 卡顿率变化百分点 | 分辨率变化 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for row in report["package_changes"]:
            movement = f"{fmt(row['improved'])}/{fmt(row['worsened'])}/{fmt(row['unchanged'])}"
            lines.append(f"| {row['label']} | {row['users']} | {fmt(row['mos_before'])} | {fmt(row['mos_after'])} | {fmt(row['mos_change'])} | {movement} | {fmt(row['ul_change_mbps'])} | {fmt(row['dl_change_mbps'])} | {fmt(row['delay_change_ms'])} | {fmt(row['loss_change_pct_point'])} | {fmt(row['stall_change_pct_point'])} | {fmt(row['resolution_change'])} |")
        lines.append("")
    return "\n".join(lines)
