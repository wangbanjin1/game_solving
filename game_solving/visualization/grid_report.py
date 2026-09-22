"""Decoupled data storage, per-population HTML reports, cross-scenario grid comparison,
and per-scenario solving process visualization matching qoe_pureH_cap40_report_20260920.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_grid_data(input_dir: Path) -> Dict[str, Any]:
    """Parse experiment outputs and build structured, lightweight curve & summary data."""
    results_raw = _load_jsonl(input_dir / "solve_results.jsonl")
    inputs_raw = _load_jsonl(input_dir / "solver_inputs.jsonl")
    dist_raw = _load_jsonl(input_dir / "initial_distribution.jsonl")
    comp_raw = _load_jsonl(input_dir / "comparison.jsonl")
    config = _load_json(input_dir / "resolved_config.json")
    summary = _load_json(input_dir / "summary.json")

    results_by_id = {r["scene_id"]: r for r in results_raw}
    inputs_by_id = {s["scene_id"]: s for s in inputs_raw}
    dist_by_id = {d["scene_id"]: d for d in dist_raw}
    comp_by_id = {c["scene_id"]: c for c in comp_raw}

    scenes_data = []

    for sid, res in results_by_id.items():
        inp = inputs_by_id.get(sid, {})
        dist = dist_by_id.get(sid, {})
        comp = comp_by_id.get(sid, {})

        params = dist.get("scenario_parameters") or {}
        users = inp.get("users", [])
        vip_users = [u for u in users if u.get("package") == "vip"]

        total_users = params.get("total_users") or len(users)
        requested_vip_ratio = params.get("requested_vip_ratio")
        if requested_vip_ratio is None and users:
            requested_vip_ratio = len(vip_users) / len(users)

        actual_vip_ratio = len(vip_users) / len(users) if users else 0.0
        requested_unmet_ratio = params.get("requested_vip_unmet_ratio", 0.0)

        # Initial MOS breakdown for VIP users
        initial_unmet_vips = [u for u in vip_users if (u.get("observed_mos") or 0.0) < 4.0]
        initial_severe_vips = [u for u in initial_unmet_vips if (u.get("observed_mos") or 0.0) < 3.5]
        initial_mild_vips = [u for u in initial_unmet_vips if (u.get("observed_mos") or 0.0) >= 3.5]
        initial_met_vips = [u for u in vip_users if (u.get("observed_mos") or 0.0) >= 4.0]

        actual_initial_unmet_ratio = len(initial_unmet_vips) / len(vip_users) if vip_users else 0.0

        # Headroom
        headroom_ratio = dist.get("headroom_ratio", {}).get("dl")
        if headroom_ratio is None:
            if "_headroom_" in sid:
                try:
                    headroom_idx = int(sid.split("_headroom_")[-1])
                    ratios = config.get("generation", {}).get("headroom_ratios", [0.0])
                    headroom_ratio = ratios[headroom_idx] if headroom_idx < len(ratios) else 0.0
                except (ValueError, IndexError):
                    headroom_ratio = 0.0
            else:
                headroom_ratio = 0.0

        trace = res.get("trace", [])
        final_decisions = res.get("decisions", [])
        final_vip_decisions = [
            d for d in final_decisions
            if any(u["user_id"] == d.get("user_id") and u.get("package") == "vip" for u in users)
        ]
        final_vip_met_count = sum(bool(d.get("quality_guarantee_met")) for d in final_vip_decisions)
        final_vip_met_ratio = (
            final_vip_met_count / len(vip_users) if vip_users else 0.0
        )
        final_vip_unmet_count = len(vip_users) - final_vip_met_count

        initial_h = trace[0].get("H", 0.0) if trace else 0.0
        final_h = trace[-1].get("H", 0.0) if trace else 0.0
        delta_h = final_h - initial_h

        # Extract iteration curves
        iterations_curve = []
        vip_met_ratio_curve = []
        vip_unmet_count_curve = []
        h_curve = []
        price_ul_curve = []
        price_dl_curve = []
        alloc_ul_mbps_curve = []
        alloc_dl_mbps_curve = []

        for row in trace:
            it = row.get("iteration", 0)
            iterations_curve.append(it)
            v_met = row.get("vip_quality_met", 0)
            v_total = row.get("vip_quality_total", len(vip_users))
            ratio = (v_met / v_total * 100.0) if v_total else 100.0
            vip_met_ratio_curve.append(round(ratio, 2))
            vip_unmet_count_curve.append(v_total - v_met)
            h_curve.append(round(row.get("H", 0.0), 3))
            prices = row.get("prices", [0.0, 0.0])
            price_ul_curve.append(round(prices[0], 4) if len(prices) > 0 else 0.0)
            price_dl_curve.append(round(prices[1], 4) if len(prices) > 1 else 0.0)
            alloc = row.get("allocated", {})
            alloc_ul_mbps_curve.append(round(alloc.get("ul", 0.0) / 1000.0, 3))
            alloc_dl_mbps_curve.append(round(alloc.get("dl", 0.0) / 1000.0, 3))

        scene_entry = {
            "scene_id": sid,
            "total_users": total_users,
            "vip_ratio": round(requested_vip_ratio, 4),
            "vip_ratio_percent": round(requested_vip_ratio * 100, 1),
            "actual_vip_ratio_percent": round(actual_vip_ratio * 100, 1),
            "vip_users": len(vip_users),
            "requested_vip_unmet_ratio": round(requested_unmet_ratio, 4),
            "requested_unmet_percent": round(requested_unmet_ratio * 100, 1),
            "actual_initial_unmet_percent": round(actual_initial_unmet_ratio * 100, 1),
            "initial_unmet_count": len(initial_unmet_vips),
            "initial_severe_count": len(initial_severe_vips),
            "initial_mild_unmet_count": len(initial_mild_vips),
            "initial_met_count": len(initial_met_vips),
            "headroom_ratio": round(headroom_ratio, 4) if headroom_ratio is not None else 0.0,
            "headroom_percent": round(headroom_ratio * 100, 1) if headroom_ratio is not None else 0.0,
            "status": res.get("run_status", "UNKNOWN"),
            "solution_status": res.get("solution_status", "UNKNOWN"),
            "stop_reason": res.get("stop_reason", "UNKNOWN"),
            "iterations": res.get("iterations", len(trace)),
            "total_steps": res.get("total_steps", 0),
            "elapsed_ms": round(res.get("elapsed_ms", 0.0), 1),
            "final_vip_met_count": final_vip_met_count,
            "final_vip_met_ratio": round(final_vip_met_ratio, 4),
            "final_vip_met_percent": round(final_vip_met_ratio * 100, 1),
            "final_vip_unmet_count": final_vip_unmet_count,
            "initial_H": round(initial_h, 3),
            "final_H": round(final_h, 3),
            "delta_H": round(delta_h, 3),
            "curves": {
                "iteration": iterations_curve,
                "vip_met_percent": vip_met_ratio_curve,
                "vip_unmet_count": vip_unmet_count_curve,
                "H": h_curve,
                "price_ul": price_ul_curve,
                "price_dl": price_dl_curve,
                "alloc_ul_mbps": alloc_ul_mbps_curve,
                "alloc_dl_mbps": alloc_dl_mbps_curve,
            },
        }
        scenes_data.append(scene_entry)

    scenes_data.sort(
        key=lambda s: (
            s["total_users"],
            s["vip_ratio"],
            s["requested_vip_unmet_ratio"],
            s["headroom_ratio"],
        )
    )

    groups_by_users: Dict[int, List[Dict[str, Any]]] = {}
    for sc in scenes_data:
        groups_by_users.setdefault(sc["total_users"], []).append(sc)

    return {
        "summary": summary,
        "config": config,
        "total_scenes": len(scenes_data),
        "user_groups": sorted(groups_by_users.keys()),
        "groups": groups_by_users,
        "all_scenes": scenes_data,
    }


def export_scene_detail_payloads(input_dir: Path, output_dir: Path) -> int:
    """Generate per-scenario lightweight decoupled .js files in data/scenes/."""
    scenes_dir = output_dir / "data" / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)

    results_raw = _load_jsonl(input_dir / "solve_results.jsonl")
    if not results_raw:
        return 0

    results = {r["scene_id"]: r for r in results_raw}
    comparisons = {c["scene_id"]: c for c in _load_jsonl(input_dir / "comparison.jsonl")}
    references = {r["scene_id"]: r for r in _load_jsonl(input_dir / "reference_results.jsonl")}
    distributions = {d["scene_id"]: d for d in _load_jsonl(input_dir / "initial_distribution.jsonl")}
    inputs = {i["scene_id"]: i for i in _load_jsonl(input_dir / "solver_inputs.jsonl")}

    config_path = input_dir / "resolved_config.json"
    config = _load_json(config_path)
    cell = config.get("cell", {})

    trace_by_scene: Dict[str, List[Dict[str, Any]]] = {}
    trace_path = input_dir / "iteration_trace.jsonl"
    if trace_path.exists():
        with trace_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    trace_by_scene.setdefault(r["scene_id"], []).append(r)

    traj_by_scene: Dict[str, List[Dict[str, Any]]] = {}
    traj_path = input_dir / "user_trajectories.jsonl"
    if traj_path.exists():
        with traj_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    traj_by_scene.setdefault(r["scene_id"], []).append(r)

    count = 0
    for sid, result in results.items():
        snapshot = inputs.get(sid, {})
        package_by_user = {u["user_id"]: u.get("package") for u in snapshot.get("users", [])}

        def policy_summary(decisions):
            vip = [a for a in decisions if package_by_user.get(a.get("user_id")) == "vip"]
            return {
                "vip_total": len(vip),
                "vip_quality_met": sum(bool(a.get("quality_guarantee_met")) for a in vip),
                "vip_target_met": sum(bool(a.get("target_met")) for a in vip),
                "vip_gap": sum(float(a.get("gap", 0.0)) for a in vip),
                "vip_H": sum(float(a.get("h", 0.0)) for a in vip),
            }

        capacity = {}
        resource_budget = {}
        for d in ("ul", "dl"):
            if "capacity" in snapshot:
                capacity[d] = (snapshot["capacity"][d] - snapshot.get("unmanaged", {}).get(d, 0) - snapshot.get("reserve", {}).get(d, 0)) / 1000
            elif "capacity_" + d + "_kbps" in cell:
                capacity[d] = (cell["capacity_" + d + "_kbps"] - cell.get("unmanaged_" + d + "_kbps", 0) - cell.get("reserve_" + d + "_kbps", 0)) / 1000
            else:
                capacity[d] = None
            initial = sum(u["current"][d] for u in snapshot["users"]) if "users" in snapshot else None
            total_limit = snapshot.get("capacity", {}).get(d, cell.get("capacity_" + d + "_kbps"))
            unmanaged = snapshot.get("unmanaged", {}).get(d, cell.get("unmanaged_" + d + "_kbps", 0))
            reserve = snapshot.get("reserve", {}).get(d, cell.get("reserve_" + d + "_kbps", 0))
            available = capacity[d] * 1000 if capacity[d] is not None else None
            margin = available - initial if available is not None and initial is not None else None
            resource_budget[d] = dict(initial_kbps=initial, total_limit_kbps=total_limit,
                                      unmanaged_kbps=unmanaged, reserve_kbps=reserve,
                                      available_kbps=available, initial_margin_kbps=margin,
                                      headroom_percent=100 * margin / initial if initial and margin is not None else None)

        scene_data = {
            "id": sid,
            "status": result.get("run_status") or ("SUCCESS" if result.get("decisions") and result.get("stop_reason") == "CONVERGED_LOCAL" else "FAILED"),
            "solution": result.get("solution_status"),
            "stop": result.get("stop_reason"),
            "iterations": result.get("iterations", 0),
            "steps": result.get("total_steps", 0),
            "ms": result.get("elapsed_ms", 0),
            "feasible": bool(result.get("decisions")),
            "trace": result.get("trace", []),
            "comparison": comparisons.get(sid),
            "capacity": capacity,
            "reference": references.get(sid),
            "distribution": distributions.get(sid),
            "resource_budget": resource_budget,
            "capacity_mode": config.get("generation", {}).get("capacity_mode"),
            "users": snapshot.get("users", []),
            "decisions": result.get("decisions", []),
            "terminal_decisions": result.get("terminal_decisions", []),
            "detail_trace": trace_by_scene.get(sid, []),
            "user_trajectories": traj_by_scene.get(sid, []),
            "H_returned": sum(a["h"] for a in result.get("decisions", [])),
            "H_terminal": sum(a["h"] for a in result.get("terminal_decisions", [])) if result.get("terminal_decisions") else None,
            "returned_policy": policy_summary(result.get("decisions", [])),
            "terminal_policy": policy_summary(result.get("terminal_decisions", [])),
            "policy_objective": config.get("policy", {}).get("objective"),
            "model_name": config.get("models", {}).get("name"),
            "lookup_directory": config.get("models", {}).get("lookup_directory"),
            "avg_qoe_per_mos": config.get("models", {}).get("avg_qoe_per_mos", 20.0),
            "returned_matches_terminal": result.get("returned_matches_terminal"),
            "output_selection": result.get("output_selection"),
        }

        js_content = f"window.SCENE_DATA = window.SCENE_DATA || {{}};\nwindow.SCENE_DATA[{json.dumps(sid)}] = {json.dumps(scene_data, ensure_ascii=False)};\n"
        (scenes_dir / f"{sid}.js").write_text(js_content, encoding="utf-8")
        count += 1

    return count


def _render_svg_script() -> str:
    """Return reusable JavaScript chart rendering and interactive filtering logic for Tab 1."""
    return """
// Color palette for high distinction
const PALETTE = [
  '#0284c7', '#ea580c', '#10b981', '#8b5cf6', '#f43f5e',
  '#eab308', '#06b6d4', '#4f46e5', '#d97706', '#059669',
  '#db2777', '#2563eb', '#65a30d', '#7c3aed', '#dc2626',
  '#0d9488', '#9333ea', '#c026d3', '#b45309', '#475569', '#14b8a6'
];

let activeMetric = 'vip_met_percent';
let highlightedSceneId = null;

const METRIC_META = {
  vip_met_percent: { label: 'VIP 达标率', unit: '%', minZero: true, maxVal: 100 },
  vip_unmet_count: { label: 'VIP 未达标人数', unit: '人', minZero: true },
  H: { label: '系统总效用 H', unit: '', minZero: false },
  price_ul: { label: '上行影子价格 λ_UL', unit: '', minZero: true },
  price_dl: { label: '下行影子价格 λ_DL', unit: '', minZero: true },
  alloc_dl_mbps: { label: '下行分配带宽', unit: 'Mbps', minZero: true },
  alloc_ul_mbps: { label: '上行分配带宽', unit: 'Mbps', minZero: true }
};

function getFilteredScenes(scenes) {
  const vipFilter = document.getElementById('vip-filter').value;
  const unmetFilter = document.getElementById('unmet-filter').value;
  const headroomFilter = document.getElementById('headroom-filter')?.value || 'ALL';

  return scenes.filter(s => {
    if (vipFilter !== 'ALL' && String(s.vip_ratio_percent) !== vipFilter) return false;
    if (unmetFilter !== 'ALL' && String(s.requested_unmet_percent) !== unmetFilter) return false;
    if (headroomFilter !== 'ALL' && String(s.headroom_percent) !== headroomFilter) return false;
    return true;
  });
}

function renderMultiCurveChart(svgId, scenes, metricKey, options = {}) {
  const svg = document.getElementById(svgId);
  if (!svg) return;
  svg.innerHTML = '';

  const meta = METRIC_META[metricKey] || { label: metricKey, unit: '', minZero: false };
  const W = options.width || 800;
  const H = options.height || 360;
  const padding = { top: 25, right: 30, bottom: 45, left: 60 };

  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);

  if (!scenes || scenes.length === 0) {
    const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    text.setAttribute('x', W / 2);
    text.setAttribute('y', H / 2);
    text.setAttribute('text-anchor', 'middle');
    text.setAttribute('fill', '#94a3b8');
    text.textContent = '没有符合当前筛选条件的场景';
    svg.appendChild(text);
    return;
  }

  let maxIt = 1;
  let allVals = [];
  scenes.forEach(s => {
    const itArr = s.curves.iteration || [];
    const valArr = s.curves[metricKey] || [];
    if (itArr.length > 0) maxIt = Math.max(maxIt, itArr[itArr.length - 1]);
    valArr.forEach(v => { if (Number.isFinite(v)) allVals.push(v); });
  });

  if (allVals.length === 0) allVals = [0];

  let minVal = meta.minZero ? Math.min(0, Math.min(...allVals)) : Math.min(...allVals);
  let maxVal = Math.max(...allVals);
  if (meta.maxVal !== undefined) maxVal = Math.max(maxVal, meta.maxVal);
  if (minVal === maxVal) { minVal -= 1; maxVal += 1; }
  const span = maxVal - minVal;
  minVal = meta.minZero ? 0 : minVal - span * 0.05;
  maxVal = maxVal + span * 0.08;

  const xScale = (it) => padding.left + ((it - 1) / Math.max(1, maxIt - 1)) * (W - padding.left - padding.right);
  const yScale = (v) => H - padding.bottom - ((v - minVal) / (maxVal - minVal)) * (H - padding.top - padding.bottom);

  const yTicks = 5;
  for (let i = 0; i <= yTicks; i++) {
    const tickVal = minVal + (i / yTicks) * (maxVal - minVal);
    const y = yScale(tickVal);
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', padding.left);
    line.setAttribute('y1', y);
    line.setAttribute('x2', W - padding.right);
    line.setAttribute('y2', y);
    line.setAttribute('stroke', '#e2e8f0');
    line.setAttribute('stroke-dasharray', '3 3');
    svg.appendChild(line);

    const txt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    txt.setAttribute('x', padding.left - 8);
    txt.setAttribute('y', y + 4);
    txt.setAttribute('text-anchor', 'end');
    txt.setAttribute('font-size', '11');
    txt.setAttribute('fill', '#64748b');
    txt.textContent = tickVal.toFixed(meta.unit === '%' || tickVal > 10 ? 1 : 2) + meta.unit;
    svg.appendChild(txt);
  }

  const xStep = Math.max(1, Math.ceil(maxIt / 10));
  for (let it = 1; it <= maxIt; it += xStep) {
    const x = xScale(it);
    const txt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    txt.setAttribute('x', x);
    txt.setAttribute('y', H - padding.bottom + 18);
    txt.setAttribute('text-anchor', 'middle');
    txt.setAttribute('font-size', '11');
    txt.setAttribute('fill', '#64748b');
    txt.textContent = '轮 ' + it;
    svg.appendChild(txt);
  }
  if ((maxIt - 1) % xStep !== 0) {
    const x = xScale(maxIt);
    const txt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    txt.setAttribute('x', x);
    txt.setAttribute('y', H - padding.bottom + 18);
    txt.setAttribute('text-anchor', 'middle');
    txt.setAttribute('font-size', '11');
    txt.setAttribute('fill', '#64748b');
    txt.textContent = '轮 ' + maxIt;
    svg.appendChild(txt);
  }

  const xTitle = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  xTitle.setAttribute('x', W / 2);
  xTitle.setAttribute('y', H - 6);
  xTitle.setAttribute('text-anchor', 'middle');
  xTitle.setAttribute('font-size', '12');
  xTitle.setAttribute('fill', '#475569');
  xTitle.textContent = '迭代步长 (Iteration)';
  svg.appendChild(xTitle);

  const tooltip = document.getElementById('chart-tooltip');

  scenes.forEach((s, idx) => {
    const itArr = s.curves.iteration || [];
    const valArr = s.curves[metricKey] || [];
    if (itArr.length === 0 || valArr.length === 0) return;

    const color = PALETTE[idx % PALETTE.length];
    const isHighlighted = highlightedSceneId === s.scene_id;
    const isDimmed = highlightedSceneId && !isHighlighted;

    const points = [];
    for (let i = 0; i < itArr.length; i++) {
      const it = itArr[i];
      const val = valArr[i];
      if (Number.isFinite(val)) {
        points.push(`${xScale(it).toFixed(1)},${yScale(val).toFixed(1)}`);
      }
    }

    if (points.length === 0) return;

    const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    poly.setAttribute('points', points.join(' '));
    poly.setAttribute('fill', 'none');
    poly.setAttribute('stroke', color);
    poly.setAttribute('stroke-width', isHighlighted ? '3.5' : (options.compact ? '1.5' : '2'));
    poly.setAttribute('stroke-opacity', isDimmed ? '0.15' : (isHighlighted ? '1.0' : '0.85'));
    poly.style.cursor = 'pointer';
    poly.dataset.sceneId = s.scene_id;

    poly.addEventListener('mouseenter', (evt) => {
      highlightScene(s.scene_id);
      if (tooltip) {
        const lastVal = valArr[valArr.length - 1];
        tooltip.innerHTML = `
          <strong>${s.scene_id}</strong><br/>
          VIP占比: <strong>${s.vip_ratio_percent}%</strong> | 初始未达标: <strong>${s.requested_unmet_percent}%</strong><br/>
          余量: <strong>${s.headroom_percent}%</strong> | 收敛轮数: <strong>${s.iterations}</strong><br/>
          当前指标 [${meta.label}]: <strong>${lastVal}${meta.unit}</strong>
        `;
        tooltip.style.display = 'block';
        tooltip.style.left = (evt.pageX + 15) + 'px';
        tooltip.style.top = (evt.pageY - 20) + 'px';
      }
    });

    poly.addEventListener('mousemove', (evt) => {
      if (tooltip && tooltip.style.display === 'block') {
        tooltip.style.left = (evt.pageX + 15) + 'px';
        tooltip.style.top = (evt.pageY - 20) + 'px';
      }
    });

    poly.addEventListener('mouseleave', () => {
      if (tooltip) tooltip.style.display = 'none';
      highlightScene(null);
    });

    svg.appendChild(poly);

    const lastIt = itArr[itArr.length - 1];
    const lastVal = valArr[valArr.length - 1];
    if (Number.isFinite(lastVal) && !options.compact) {
      const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      circle.setAttribute('cx', xScale(lastIt));
      circle.setAttribute('cy', yScale(lastVal));
      circle.setAttribute('r', isHighlighted ? '5' : '3');
      circle.setAttribute('fill', color);
      circle.setAttribute('stroke', '#ffffff');
      circle.setAttribute('stroke-width', '1.5');
      circle.setAttribute('opacity', isDimmed ? '0.2' : '1');
      svg.appendChild(circle);
    }
  });
}

function highlightScene(sceneId) {
  highlightedSceneId = sceneId;
  document.querySelectorAll('polyline[data-scene-id]').forEach(el => {
    const isTarget = el.dataset.sceneId === sceneId;
    if (!sceneId) {
      el.setAttribute('stroke-opacity', '0.85');
      el.setAttribute('stroke-width', '2');
    } else if (isTarget) {
      el.setAttribute('stroke-opacity', '1.0');
      el.setAttribute('stroke-width', '4');
      el.parentNode.appendChild(el);
    } else {
      el.setAttribute('stroke-opacity', '0.15');
      el.setAttribute('stroke-width', '1.5');
    }
  });

  document.querySelectorAll('tr[data-scene-id]').forEach(tr => {
    if (tr.dataset.sceneId === sceneId) {
      tr.classList.add('selected-row');
    } else {
      tr.classList.remove('selected-row');
    }
  });
}
"""


def _render_single_scene_script() -> str:
    """Return reusable JavaScript for the per-scenario detailed solving process view."""
    return """
let currentLoadedScene = null;
const fmt = x => x === null || x === undefined ? '—' : typeof x === 'number' ? Number(x.toFixed(3)).toLocaleString('zh-CN') : String(x);
const num = x => x == null ? '—' : typeof x === 'number' ? String(Number(x.toFixed(6))) : String(x);
const yes = x => x == null ? '—' : x ? '是' : '否';
const bw = a => a && a.bandwidth ? num(a.bandwidth.ul) + ' / ' + num(a.bandwidth.dl) : '—';
const byUser = xs => new Map((xs || []).map(x => [x.user_id, x]));

function singleTable(headers, rows) {
  const t = document.createElement('table'), h = document.createElement('thead'), tr = document.createElement('tr');
  headers.forEach(x => {
    const th = document.createElement('th');
    th.textContent = x;
    tr.appendChild(th);
  });
  h.appendChild(tr);
  t.appendChild(h);
  const b = document.createElement('tbody');
  rows.forEach(values => {
    const r = document.createElement('tr');
    values.forEach(v => {
      const td = document.createElement('td');
      td.textContent = fmt(v);
      r.appendChild(td);
    });
    b.appendChild(r);
  });
  t.appendChild(b);
  return t;
}

const colors = ['#167d98', '#ee9744', '#8b99ad', '#e11d48', '#10b981'];

function singleChart(id, trace, series, includeZero = true) {
  const box = document.getElementById(id);
  if (!box) return;
  box.replaceChildren();
  if (!trace || !trace.length) {
    const emp = document.createElement('div');
    emp.textContent = '没有已完成轮次；请查看停止原因。';
    emp.style.padding = '40px 10px';
    emp.style.textAlign = 'center';
    emp.style.color = '#71869a';
    box.appendChild(emp);
    return;
  }
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 600 275');
  svg.setAttribute('role', 'img');
  svg.style.width = '100%';
  svg.style.height = '100%';

  function shape(tag, attrs, text) {
    const e = document.createElementNS(ns, tag);
    Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
    if (text !== undefined) e.textContent = text;
    svg.appendChild(e);
    return e;
  }

  const values = series.flatMap(s => s.values).filter(Number.isFinite);
  if (!values.length) {
    const emp = document.createElement('div');
    emp.textContent = '无可用数值';
    emp.style.padding = '40px 10px';
    emp.style.textAlign = 'center';
    emp.style.color = '#71869a';
    box.appendChild(emp);
    return;
  }
  let lo = includeZero ? Math.min(0, ...values) : Math.min(...values);
  let hi = includeZero ? Math.max(0, ...values) : Math.max(...values);
  if (hi === lo) {
    const pad = Math.max(Math.abs(lo) * 0.08, 0.5);
    lo -= pad;
    hi += pad;
  } else {
    const span = hi - lo;
    if (!includeZero || lo !== 0) lo -= span * 0.08;
    hi += span * 0.08;
  }
  const left = 64, right = 580, top = 18, bottom = 230;
  const x = i => trace.length === 1 ? (left + right) / 2 : left + i * (right - left) / (trace.length - 1);
  const y = v => bottom - (v - lo) / (hi - lo) * (bottom - top);

  for (let i = 0; i <= 4; i++) {
    let v = lo + (hi - lo) * i / 4;
    shape('line', { x1: left, y1: y(v), x2: right, y2: y(v), stroke: '#e6edf3' });
    shape('text', { x: left - 8, y: y(v) + 4, 'text-anchor': 'end', fill: '#6b8092', 'font-size': 11 }, fmt(v));
  }
  trace.forEach((t, i) => {
    if (i === 0 || i === trace.length - 1 || i % Math.max(1, Math.ceil(trace.length / 8)) === 0) {
      shape('text', { x: x(i), y: 250, 'text-anchor': 'middle', fill: '#6b8092', 'font-size': 11 }, t.iteration);
    }
  });
  series.forEach((s, j) => {
    const points = s.values.map((v, i) => Number.isFinite(v) ? [x(i), y(v), i, v] : null);
    let segment = [];
    function flush() {
      if (segment.length) {
        shape('polyline', {
          points: segment.map(p => p[0] + ',' + p[1]).join(' '),
          fill: 'none',
          stroke: colors[j % colors.length],
          'stroke-width': 2,
          'stroke-dasharray': s.dashed ? '5 4' : ''
        });
        segment = [];
      }
    }
    points.forEach(p => { if (p) segment.push(p); else flush(); });
    flush();
    points.filter(Boolean).forEach(p => {
      const c = shape('circle', { cx: p[0], cy: p[1], r: 3, fill: colors[j % colors.length] });
      const title = document.createElementNS(ns, 'title');
      const itLabel = trace[p[2]] ? trace[p[2]].iteration : p[2];
      title.textContent = '轮 ' + itLabel + ' ' + s.name + ': ' + fmt(p[3]);
      c.appendChild(title);
    });
  });
  box.appendChild(svg);
  const legend = document.createElement('div');
  legend.className = 'legend';
  legend.style.display = 'flex';
  legend.style.gap = '16px';
  legend.style.fontSize = '12px';
  legend.style.flexWrap = 'wrap';
  legend.style.marginTop = '8px';
  legend.style.justifyContent = 'center';
  series.forEach((s, i) => {
    let item = document.createElement('span');
    let dot = document.createElement('i');
    dot.style.display = 'inline-block';
    dot.style.width = '9px';
    dot.style.height = '9px';
    dot.style.borderRadius = '50%';
    dot.style.marginRight = '5px';
    dot.style.background = colors[i % colors.length];
    item.appendChild(dot);
    item.appendChild(document.createTextNode(s.name));
    legend.appendChild(item);
  });
  box.appendChild(legend);
}

const kqiMetrics = {
  avg_qoe: { label: 'avgQoe', unit: '分' },
  bitrate_kbps: { label: '码率', unit: 'kbps' },
  resolution: { label: '分辨率', unit: '垂直像素' },
  service_delay_ms: { label: '时延', unit: 'ms' },
  loss_ratio: { label: '丢包率', unit: '比例' },
  stall_ratio: { label: '卡顿率', unit: '比例' },
  stalling_level: { label: '卡顿档位', unit: '档' },
  buffer_ms: { label: '首缓', unit: 'ms' },
  jitter_ms: { label: '抖动', unit: 'ms' }
};

const mean = xs => { const values = xs.filter(Number.isFinite); return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null; };
const trend = (before, after) => !Number.isFinite(before) || !Number.isFinite(after) ? 'NA' : after - before > 1e-9 ? 'UP' : before - after > 1e-9 ? 'DOWN' : 'SAME';
const trendText = x => ({ UP: '上升', DOWN: '下降', SAME: '不变', NA: '无数据' })[x] || '—';
const metricRounds = s => s.user_trajectories && s.user_trajectories.length ? s.user_trajectories : (s.detail_trace || []);
const metricAction = u => u ? (u.allocated || u) : null;

function userDirections(s, userId) {
  const u = (s.users || []).find(x => x.user_id === userId);
  const final = byUser(s.decisions).get(userId);
  const set = new Set(Object.keys(u?.streams || {}));
  for (const d of Object.keys(final?.predicted_kqi || {})) set.add(d);
  for (const t of metricRounds(s)) {
    const matched = (t.users || []).find(x => x.user_id === userId);
    for (const d of Object.keys(metricAction(matched)?.KQI || {})) set.add(d);
  }
  const selected = document.getElementById('user-kqi-direction').value;
  return selected === 'ALL' ? [...set] : set.has(selected) ? [selected] : [];
}

function initialKqi(s, u, d, key) {
  const first = (metricRounds(s)[0]?.users || []).find(x => x.user_id === u.user_id);
  const saved = first?.initial_KQI?.[d]?.[key];
  if (Number.isFinite(saved)) return saved;
  const current = first?.allocated?.KQI?.[d]?.[key];
  const delta = first?.kqi_change_from_initial?.[d]?.[key];
  if (Number.isFinite(current)) return current - (Number.isFinite(delta) ? delta : 0);
  const stream = u.streams?.[d];
  if (!stream) return null;
  const fields = { bitrate_kbps: 'bitrate_kbps', resolution: 'resolution', service_delay_ms: 'rtt_ms', loss_ratio: 'loss_ratio', stall_ratio: 'stall_ratio', buffer_ms: 'buffer_ms', jitter_ms: 'jitter_ms' };
  if (key === 'avg_qoe') return Number.isFinite(u.observed_mos) ? u.observed_mos * (s.avg_qoe_per_mos || 20) : null;
  return Number.isFinite(stream[fields[key]]) ? stream[fields[key]] : null;
}

function userChangeRecord(s, u) {
  const final = byUser(s.decisions).get(u.user_id);
  const metric = document.getElementById('user-kqi-metric').value;
  const directions = userDirections(s, u.user_id);
  const beforeKqi = mean(directions.map(d => initialKqi(s, u, d, metric)));
  const afterKqi = mean(directions.map(d => final?.predicted_kqi?.[d]?.[metric]));
  return {
    u, final,
    beforeMos: u.observed_mos,
    afterMos: final?.mos,
    beforeKqi, afterKqi,
    mosTrend: trend(u.observed_mos, final?.mos),
    kqiTrend: trend(beforeKqi, afterKqi)
  };
}

function renderUserAnalysis() {
  const s = currentLoadedScene;
  if (!s) return;
  const packageFilter = document.getElementById('user-package-filter').value;
  const mosFilter = document.getElementById('user-mos-trend').value;
  const kqiFilter = document.getElementById('user-kqi-trend').value;
  const records = (s.users || []).map(u => userChangeRecord(s, u));
  const visible = records.filter(r => 
    (packageFilter === 'ALL' || r.u.package === packageFilter) &&
    (mosFilter === 'ALL' || r.mosTrend === mosFilter) &&
    (kqiFilter === 'ALL' || r.kqiTrend === kqiFilter)
  );
  const userSelect = document.getElementById('trajectory-user');
  const selected = userSelect.value;
  userSelect.replaceChildren();
  for (const r of visible) {
    const o = document.createElement('option');
    o.textContent = r.u.user_id + ' · ' + (r.u.package === 'vip' ? 'VIP' : '普通') + ' · ' + r.u.app_id;
    o.value = r.u.user_id;
    userSelect.appendChild(o);
  }
  if (visible.some(r => r.u.user_id === selected)) {
    userSelect.value = selected;
  }
  document.getElementById('user-filter-summary').textContent = `筛选结果 ${visible.length} / ${records.length} 人；上升/下降按返回方案与初始值比较。`;
  const metricKey = document.getElementById('user-kqi-metric').value;
  const metric = kqiMetrics[metricKey] || { label: metricKey, unit: '' };
  const rows = visible.map(r => [
    r.u.user_id,
    r.u.package === 'vip' ? 'VIP' : '普通',
    r.u.business,
    r.u.app_id,
    r.beforeMos,
    r.afterMos,
    Number.isFinite(r.beforeMos) && Number.isFinite(r.afterMos) ? r.afterMos - r.beforeMos : null,
    trendText(r.mosTrend),
    r.beforeKqi,
    r.afterKqi,
    Number.isFinite(r.beforeKqi) && Number.isFinite(r.afterKqi) ? r.afterKqi - r.beforeKqi : null,
    trendText(r.kqiTrend)
  ]);
  const t = singleTable(['用户', '类型', '业务', 'App', '初始MOS', '返回MOS', 'ΔMOS', 'MOS方向', '初始' + metric.label, '返回' + metric.label, 'Δ' + metric.label, 'KQI方向'], rows);
  [...t.tBodies[0].rows].forEach((row, i) => {
    row.dataset.id = visible[i].u.user_id;
    row.style.cursor = 'pointer';
    if (visible[i].u.user_id === userSelect.value) row.style.background = '#e0f2fe';
    row.onclick = () => {
      userSelect.value = visible[i].u.user_id;
      renderUserAnalysis();
    };
  });
  const wrap = document.getElementById('user-filter-table');
  wrap.replaceChildren(t);
  if (!visible.length) {
    wrap.replaceChildren(document.createTextNode('没有符合当前筛选条件的用户。'));
  }
  renderUserTrajectory();
}

function renderUserTrajectory() {
  const s = currentLoadedScene;
  const userId = document.getElementById('trajectory-user').value;
  const u = (s?.users || []).find(x => x.user_id === userId);
  if (!s || !u) {
    document.getElementById('user-mos-chart').replaceChildren(document.createTextNode('请选择有数据的用户。'));
    document.getElementById('user-kqi-chart').replaceChildren(document.createTextNode('请选择有数据的用户。'));
    return;
  }
  const final = byUser(s.decisions).get(userId);
  const rounds = metricRounds(s);
  const axis = [{ iteration: '初始' }, ...rounds.map(t => ({ iteration: t.iteration })), { iteration: '返回' }];
  const roundUsers = rounds.map(t => (t.users || []).find(x => x.user_id === userId));

  document.getElementById('user-mos-title').textContent = userId + ' · MOS逐轮变化';
  singleChart('user-mos-chart', axis, [{
    name: 'MOS',
    values: [u.observed_mos, ...roundUsers.map(x => metricAction(x)?.mos), final?.mos]
  }], false);

  const metricKey = document.getElementById('user-kqi-metric').value;
  const metric = kqiMetrics[metricKey] || { label: metricKey, unit: '' };
  const directions = userDirections(s, userId);
  const series = directions.map(d => ({
    name: (d === 'ul' ? '上行 UL' : d === 'dl' ? '下行 DL' : '会话') + ' · ' + metric.label,
    values: [initialKqi(s, u, d, metricKey), ...roundUsers.map(x => metricAction(x)?.KQI?.[d]?.[metricKey]), final?.predicted_kqi?.[d]?.[metricKey]]
  }));
  document.getElementById('user-kqi-title').textContent = userId + ' · ' + metric.label + '逐轮变化（' + metric.unit + '）';
  singleChart('user-kqi-chart', axis, series, false);
}

function renderRound() {
  const s = currentLoadedScene;
  if (!s) return;
  const roundSelect = document.getElementById('round-select');
  const t = (s.detail_trace || []).find(t => String(t.iteration) === roundSelect.value);
  if (!t) {
    document.getElementById('round-state-notice').textContent = '本场景在初始状态已局部收敛或无额外逐轮明细记录。';
    ['round-users-table', 'candidate-actions-table', 'round-events-table', 'round-components-table', 'round-kqi-summary-table', 'round-kqi-table', 'round-groups-table'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.replaceChildren();
    });
    return;
  }
  const index = s.detail_trace.indexOf(t);
  const prior = byUser(index > 0 ? s.detail_trace[index - 1].users : []);
  const initial = byUser(s.users);
  const ref = byUser(s.reference?.policy_decisions);
  const previous = x => prior.get(x.user_id)?.allocated || { bandwidth: initial.get(x.user_id)?.current, mos: initial.get(x.user_id)?.observed_mos };

  document.getElementById('round-state-notice').textContent = 
    '决策方式 ' + (t.decision_mode === 'grouped_common_mos' ? '分组同 MOS 起跑线' : '逐用户初态对齐') +
    ' ｜ 分组数 ' + num(t.groups?.length) +
    ' ｜ VIP质差保障 ' + num(t.vip_quality_met) + ' / ' + num(t.vip_quality_total) +
    ' ｜ 本轮 H ' + num(t.H) + ' ｜ 历史最好 H ' + num(t.best_H) + ' (记录轮次 ' + num(t.best_iteration) + ')' +
    ' ｜ 需求 UL/DL ' + num(t.raw?.ul) + ' / ' + num(t.raw?.dl) +
    ' ｜ 分配 ' + num(t.allocated?.ul) + ' / ' + num(t.allocated?.dl) +
    ' ｜ 可用上限 UL/DL ' + num(s.resource_budget?.ul?.available_kbps) + ' / ' + num(s.resource_budget?.dl?.available_kbps) +
    ' ｜ 剩余容量 ' + num(s.capacity?.ul == null ? null : s.capacity.ul * 1000 - t.allocated?.ul) + ' / ' + num(s.capacity?.dl == null ? null : s.capacity.dl * 1000 - t.allocated?.dl) +
    ' ｜ 影子价格 ' + (t.prices || []).map(num).join(' / ') + ' → ' + (t.next_prices || []).map(num).join(' / ') +
    ' ｜ 策略变化比例 ' + num(t.strategy_change) + ' ｜ 稳定计数 ' + num(t.stable_count) + ' / ' + num(t.stable_required);

  const fill = (id, headers, rows) => {
    const el = document.getElementById(id);
    if (el) el.replaceChildren(singleTable(headers, rows.map(r => r.map(num))));
  };

  fill('round-users-table', ['用户', '套餐', '业务', 'App', '上轮上/下行', '本轮请求上/下行', '协调后上/下行', '上轮 MOS', '请求 MOS', '协调后 MOS', 'MOS 变化', 'VIP达标', 'VIP质差保障', '个人 H', '参考上/下行'],
    (t.users || []).map(u => [
      u.user_id, u.package, u.business, u.app_id,
      bw(previous(u)), bw(u.requested), bw(u.allocated),
      previous(u).mos, u.requested?.mos, u.allocated?.mos,
      u.allocated?.mos != null && previous(u).mos != null ? u.allocated.mos - previous(u).mos : null,
      u.package === 'vip' ? yes(u.allocated?.target_met) : '不评价',
      u.package === 'vip' ? yes(u.allocated?.quality_guarantee_met) : '不评价',
      u.allocated?.H,
      bw(ref.get(u.user_id))
    ])
  );

  const kqi = [], records = [];
  for (const u of t.users || []) {
    for (const [d, current] of Object.entries(u.allocated?.KQI || {})) {
      const delta = u.kqi_change_from_initial?.[d] || {}, initVal = {};
      for (const field of ['avg_qoe', 'bitrate_kbps', 'resolution', 'service_delay_ms', 'loss_ratio', 'stall_ratio', 'stalling_level']) {
        initVal[field] = current[field] == null ? null : current[field] - (delta[field] || 0);
      }
      records.push({ u, d, current, delta, initial: initVal });
      kqi.push([
        u.user_id, u.package === 'vip' ? 'VIP' : '普通', u.app_id, d,
        initVal.avg_qoe, current.avg_qoe, delta.avg_qoe,
        initVal.bitrate_kbps, current.bitrate_kbps, delta.bitrate_kbps,
        initVal.resolution, current.resolution, delta.resolution,
        initVal.service_delay_ms, current.service_delay_ms, delta.service_delay_ms,
        initVal.loss_ratio, current.loss_ratio, delta.loss_ratio,
        initVal.stall_ratio, current.stall_ratio, delta.stall_ratio,
        (u.allocated?.quality_violations || []).join(',')
      ]);
    }
  }

  const avg = (xs, f) => xs.length ? xs.reduce((n, x) => n + (f(x) || 0), 0) / xs.length : null;
  const metrics = [['avgQoe', 'avg_qoe'], ['码率 kbps', 'bitrate_kbps'], ['分辨率（垂直像素）', 'resolution'], ['时延 ms', 'service_delay_ms'], ['丢包率', 'loss_ratio'], ['卡顿率', 'stall_ratio'], ['卡顿档位', 'stalling_level']];
  fill('round-kqi-summary-table', ['指标', '初始均值', '本轮均值', '平均变化'], metrics.map(([name, key]) => [name, avg(records, x => x.initial[key]), avg(records, x => x.current[key]), avg(records, x => x.delta[key])]));

  fill('round-kqi-table', ['用户', '套餐', 'App', '方向', '初始 avgQoe', '当前 avgQoe', 'ΔavgQoe', '初始码率', '当前码率', 'Δ码率', '初始分辨率', '当前分辨率', 'Δ分辨率', '初始时延', '当前时延', 'Δ时延', '初始丢包率', '当前丢包率', 'Δ丢包率', '初始卡顿率', '当前卡顿率', 'Δ卡顿率', '质差原因'], kqi);

  if (t.decision_mode === 'grouped_common_mos') {
    fill('round-groups-table', ['套餐', '容忍度', '位置', '业务', 'App', '初始MOS档', '组内人数', '共同MOS线'], (t.groups || []).map(g => [...g.group, g.users, g.selected_mos_line]));
  } else {
    document.getElementById('round-groups-table').replaceChildren(document.createTextNode('第 1 轮按用户初始状态分别对齐；从第 2 轮开始显示六维分组。'));
  }

  const candidateSelect = document.getElementById('candidate-user-select');
  const selectedUser = candidateSelect.value;
  candidateSelect.replaceChildren();
  for (const u of t.users || []) {
    const o = document.createElement('option');
    o.textContent = u.user_id + ' (' + u.package + ')';
    o.value = u.user_id;
    candidateSelect.appendChild(o);
  }
  if ((t.users || []).some(u => u.user_id === selectedUser)) {
    candidateSelect.value = selectedUser;
  }
  renderCandidates();

  const reasons = { capacity_repair: '超容量修复', slack_upgrade: '利用余量升级', positive_utility_upgrade: '利用余量升级', net_utility_exchange: '释放资源并升级其他用户' };
  const events = [];
  for (const [i, e] of (t.coordination_events || []).entries()) {
    for (const c of e.changes || []) {
      events.push([i + 1, reasons[e.reason] || e.reason, c.user_id, bw(c.before), bw(c.after), c.before?.mos, c.after?.mos, c.after?.h - c.before?.h, e.net_benefit]);
    }
  }
  fill('round-events-table', ['步骤', '原因', '用户', '调整前上/下行', '调整后上/下行', '调整前 MOS', '调整后 MOS', '个人 ΔH', '本步骤总 ΔH'], events);
  if (!events.length) {
    document.getElementById('round-events-table').replaceChildren(document.createTextNode('本轮无资源协调事件。'));
  }

  fill('round-components-table', ['用户', '权重', '体验收益', '历史欠账', '公平补偿', '稳定性成本', 'H', '当前分配影子成本'], (t.users || []).map(u => [u.user_id, u.utility?.weight, u.utility?.experience_benefit, u.utility?.debt, u.utility?.fairness_compensation, u.utility?.stability_cost, u.utility?.H, u.shadow_cost]));
}

function renderCandidates() {
  const s = currentLoadedScene;
  const roundSelect = document.getElementById('round-select');
  const t = (s.detail_trace || []).find(t => String(t.iteration) === roundSelect.value);
  const userId = document.getElementById('candidate-user-select').value;
  const u = t?.users?.find(u => u.user_id === userId);
  const candidates = [...(u?.candidates || [])].sort((a, b) => b.selection_score - a.selection_score || Number(b.anchor) - Number(a.anchor) || (a.bandwidth.ul + a.bandwidth.dl) - (b.bandwidth.ul + b.bandwidth.dl) || (a.action_id < b.action_id ? 1 : a.action_id > b.action_id ? -1 : 0));

  const el = document.getElementById('candidate-actions-table');
  if (!candidates.length) {
    el.replaceChildren(document.createTextNode('此轮次未保存候选动作详情。'));
    return;
  }
  el.replaceChildren(singleTable(['动作 ID', '上行 / 下行', 'MOS', 'H', '影子成本', '选择评分 H−成本', '个人选中', '协调后采用'], candidates.map(a => [a.action_id, bw(a), a.mos, a.h, a.shadow_cost, a.selection_score, yes(a.action_id === u?.requested?.action_id), yes(a.action_id === u?.allocated?.action_id)])));
}

function showDetailScene(s) {
  currentLoadedScene = s;
  document.getElementById('scene-select').value = s.id;
  document.getElementById('detail-status-badge').textContent = s.status === 'SUCCESS' ? '求解成功' : '未收敛 / 终止';
  document.getElementById('detail-status-badge').className = 'badge ' + (s.status === 'SUCCESS' ? 'badge-success' : 'badge-fail');
  document.getElementById('detail-meta').textContent = 
    '停止原因：' + s.stop + ' ｜ ' + s.solution + ' ｜ ' + s.iterations + ' 轮 / ' + fmt(s.steps) + ' 工作量 / ' + fmt(s.ms) + ' ms';

  const c = s.comparison;
  const compBox = document.getElementById('detail-comparison-table');
  if (c) {
    const pc = c.package_changes || [];
    const pkg = p => pc.find(r => r.package === p);
    const vip = pkg('vip');
    const nrm = pkg('normal');
    const move = r => r ? fmt(r.improved) + '/' + fmt(r.worsened) + '/' + fmt(r.unchanged) : '—';
    const rows = [
      ['人数', vip?.users, '—', '—', nrm?.users, '—', '—'],
      ['基本保障不足人数', vip?.basic_unmet_before, vip?.basic_unmet_after, vip?.basic_unmet_change, '—', '—', '—'],
      ['目标达标人数', vip?.target_met_before, vip?.target_met_after, vip?.target_met_change, '—', '—', '—'],
      ['会话MOS均值', vip?.mos_before, vip?.mos_after, vip?.mos_change, nrm?.mos_before, nrm?.mos_after, nrm?.mos_change],
      ['加权MOS总和', vip?.weighted_mos_before, vip?.weighted_mos_after, vip?.weighted_mos_change, nrm?.weighted_mos_before, nrm?.weighted_mos_after, nrm?.weighted_mos_change],
      ['上行占用Mbps', vip?.ul_before_mbps, vip?.ul_after_mbps, vip?.ul_change_mbps, nrm?.ul_before_mbps, nrm?.ul_after_mbps, nrm?.ul_change_mbps],
      ['下行占用Mbps', vip?.dl_before_mbps, vip?.dl_after_mbps, vip?.dl_change_mbps, nrm?.dl_before_mbps, nrm?.dl_after_mbps, nrm?.dl_change_mbps],
      ['改善/恶化/不变', '—', '—', move(vip), '—', '—', move(nrm)],
      ['时延变化ms', '—', '—', vip?.delay_change_ms, '—', '—', nrm?.delay_change_ms],
      ['丢包率变化百分点', '—', '—', vip?.loss_change_pct_point, '—', '—', nrm?.loss_change_pct_point],
      ['卡顿率变化百分点', '—', '—', vip?.stall_change_pct_point, '—', '—', nrm?.stall_change_pct_point],
      ['首缓变化ms', '—', '—', vip?.buffer_change_ms, '—', '—', nrm?.buffer_change_ms],
      ['抖动变化ms', '—', '—', vip?.jitter_change_ms, '—', '—', nrm?.jitter_change_ms],
      ['分辨率变化', '—', '—', vip?.resolution_change, '—', '—', nrm?.resolution_change]
    ];
    compBox.replaceChildren(singleTable(['指标', 'VIP 前', 'VIP 后', 'VIP 变化', '普通 前', '普通 后', '普通 变化'], rows));
    document.getElementById('detail-people-summary').textContent = 
      'MOS评价人数 ' + fmt(c.mos_users) + ' · 改善 ' + fmt(c.improved) + ' · 恶化 ' + fmt(c.worsened) + ' · 不变 ' + fmt(c.unchanged) + ' · 最大MOS降幅 ' + fmt(c.max_mos_drop);
  } else {
    compBox.replaceChildren(document.createTextNode('无前后对比数据'));
  }

  const rp = s.returned_policy || {}, tp = s.terminal_policy || {}, rf = s.reference || {};
  const rv = (rf.policy_decisions || []).filter(a => (s.users || []).find(u => u.user_id === a.user_id)?.package === 'vip');
  const rvTotal = (s.users || []).filter(u => u.package === 'vip').length;
  document.getElementById('detail-reference-table').replaceChildren(singleTable(
    ['方案', '总 H', 'VIP质差达标', 'VIP目标达标', 'VIP目标缺口', 'VIP H'],
    [
      ['策略返回', s.H_returned, fmt(rp.vip_quality_met) + '/' + fmt(rp.vip_total), fmt(rp.vip_target_met) + '/' + fmt(rp.vip_total), rp.vip_gap, rp.vip_H],
      ['停止时终态', s.H_terminal, fmt(tp.vip_quality_met) + '/' + fmt(tp.vip_total), fmt(tp.vip_target_met) + '/' + fmt(tp.vip_total), tp.vip_gap, tp.vip_H],
      ['参考答案（暴搜）', rf.H_reference, fmt(rv.filter(a => a.quality_guarantee_met).length) + '/' + rvTotal, fmt(rv.filter(a => a.target_met).length) + '/' + rvTotal, '—', '—']
    ]
  ));

  const distBox = document.getElementById('detail-distribution-table');
  if (s.distribution && s.distribution.groups) {
    distBox.replaceChildren(singleTable(
      ['套餐', '人数', 'MOS人数', 'VIP评价人数', 'VIP初始达标', 'VIP初始未达标', 'VIP查表域内个人目标不可达'],
      Object.entries(s.distribution.groups).map(([name, g]) => [name, g.users, g.mos_users, g.target_evaluated_users, g.target_met, g.target_unmet, g.individually_unreachable])
    ));
  } else {
    distBox.replaceChildren(document.createTextNode('未记录初始分布。'));
  }

  const b = s.resource_budget || {};
  document.getElementById('detail-capacity-note').textContent = 
    s.id + ' ｜ 余量比例 UL ' + num(b.ul?.headroom_percent) + '% / DL ' + num(b.dl?.headroom_percent) + '%。';
  document.getElementById('detail-capacity-table').replaceChildren(singleTable(
    ['方向', '初始用户占用', '初始额外余量', '余量比例', '小区总上限', '非受控占用', '预留', '求解可用上限'],
    ['ul', 'dl'].map(d => {
      const dirBudget = b[d] || {};
      return [d === 'ul' ? '上行 UL' : '下行 DL', dirBudget.initial_kbps, dirBudget.initial_margin_kbps, num(dirBudget.headroom_percent) + '%', dirBudget.total_limit_kbps, dirBudget.unmanaged_kbps, dirBudget.reserve_kbps, dirBudget.available_kbps];
    })
  ));

  for (const d of ['ul', 'dl']) {
    let series = [
      { name: '协调前需求', values: (s.trace || []).map(t => t.raw?.[d] / 1000) },
      { name: '协调后分配', values: (s.trace || []).map(t => t.allocated?.[d] / 1000) }
    ];
    if (Number.isFinite(s.capacity?.[d])) {
      series.push({ name: '可用容量', values: (s.trace || []).map(() => s.capacity[d]), dashed: true });
    }
    singleChart('detail-chart-' + d, s.trace || [], series);
  }
  singleChart('detail-chart-utility', s.trace || [], [
    { name: '当轮 H', values: (s.trace || []).map(t => t.H) },
    { name: '历史策略最优方案的 H', values: (s.trace || []).map(t => t.best_H) },
    { name: '参考 H', values: (s.trace || []).map(() => s.reference?.H_reference), dashed: true }
  ]);
  singleChart('detail-chart-prices', s.trace || [], [
    { name: '上行影子价格 λ_UL', values: (s.trace || []).map(t => t.prices?.[0]) },
    { name: '下行影子价格 λ_DL', values: (s.trace || []).map(t => t.prices?.[1]) }
  ]);

  renderUserAnalysis();

  const roundSelect = document.getElementById('round-select');
  roundSelect.replaceChildren();
  for (const t of s.detail_trace || []) {
    const o = document.createElement('option');
    o.textContent = '第 ' + t.iteration + ' 轮';
    o.value = t.iteration;
    roundSelect.appendChild(o);
  }
  renderRound();
}

function loadAndShowScene(sceneId) {
  const loading = document.getElementById('scene-loading-indicator');
  const container = document.getElementById('scene-detail-container');
  if (window.SCENE_DATA && window.SCENE_DATA[sceneId]) {
    if (loading) loading.style.display = 'none';
    if (container) container.style.display = 'block';
    showDetailScene(window.SCENE_DATA[sceneId]);
    return;
  }
  if (loading) {
    loading.textContent = `⏳ 正在加载场景 ${sceneId} 的详细求解过程数据...`;
    loading.style.display = 'block';
  }
  if (container) container.style.display = 'none';
  const script = document.createElement('script');
  script.src = `data/scenes/${sceneId}.js`;
  script.onload = () => {
    if (loading) loading.style.display = 'none';
    if (container) container.style.display = 'block';
    if (window.SCENE_DATA && window.SCENE_DATA[sceneId]) {
      showDetailScene(window.SCENE_DATA[sceneId]);
    } else {
      if (loading) {
        loading.textContent = `⚠️ 未找到场景 ${sceneId} 的详细数据。`;
        loading.style.display = 'block';
      }
    }
  };
  script.onerror = () => {
    if (loading) {
      loading.textContent = `❌ 无法加载数据文件 data/scenes/${sceneId}.js`;
      loading.style.display = 'block';
    }
  };
  document.head.appendChild(script);
}
"""


def _generate_group_html(total_users: int, scenes: List[Dict[str, Any]], all_groups: List[int]) -> str:
    """Generate the stand-alone, decoupled HTML report for a specific total_users group."""
    vip_ratios = sorted({s["vip_ratio_percent"] for s in scenes})
    unmet_ratios = sorted({s["requested_unmet_percent"] for s in scenes})
    headroom_ratios = sorted({s["headroom_percent"] for s in scenes})

    has_multi_headroom = len(headroom_ratios) > 1

    nav_links_html = "".join(
        f'<a href="report_u{u}.html" class="nav-btn {"active" if u == total_users else ""}">{u} 人场景群</a>'
        for u in all_groups
    )

    scene_options_html = "".join(
        f'<option value="{s["scene_id"]}">{s["scene_id"]} · VIP {s["vip_ratio_percent"]}% · 未达标 {s["requested_unmet_percent"]}% ({s["status"]})</option>'
        for s in scenes
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{total_users} 人场景群 · 迭代收敛与求解过程深度分析</title>
  <style>
    :root {{
      --primary: #0284c7;
      --primary-dark: #0369a1;
      --bg: #f8fafc;
      --panel-bg: #ffffff;
      --line: #e2e8f0;
      --text: #0f172a;
      --muted: #64748b;
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
    }}
    header {{
      background: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #0369a1 100%);
      color: #fff;
      padding: 24px 32px;
      box-shadow: 0 4px 20px rgba(0,0,0,0.15);
    }}
    .header-top {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 16px;
    }}
    .title-group h1 {{
      font-size: 26px;
      font-weight: 700;
      letter-spacing: -0.5px;
    }}
    .title-group p {{
      color: #94a3b8;
      font-size: 14px;
      margin-top: 4px;
    }}
    .nav-group {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .nav-btn {{
      padding: 8px 16px;
      border-radius: 6px;
      background: rgba(255,255,255,0.1);
      color: #fff;
      text-decoration: none;
      font-size: 13px;
      font-weight: 500;
      transition: all 0.2s;
    }}
    .nav-btn:hover {{
      background: rgba(255,255,255,0.25);
    }}
    .nav-btn.active {{
      background: #38bdf8;
      color: #0f172a;
      font-weight: 600;
    }}
    .home-btn {{
      background: #0284c7;
    }}
    .container {{
      max-width: 1440px;
      margin: 0 auto;
      padding: 24px;
    }}

    /* Main View Switcher Tabs */
    .view-switcher {{
      display: flex;
      gap: 12px;
      margin-bottom: 24px;
      background: #e2e8f0;
      padding: 6px;
      border-radius: 10px;
      width: fit-content;
    }}
    .view-tab-btn {{
      padding: 10px 24px;
      border: none;
      border-radius: 8px;
      background: transparent;
      font-size: 15px;
      font-weight: 600;
      color: #475569;
      cursor: pointer;
      transition: all 0.2s;
    }}
    .view-tab-btn:hover {{
      color: #0f172a;
    }}
    .view-tab-btn.active {{
      background: #fff;
      color: #0284c7;
      box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    }}

    .stats-cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }}
    .card {{
      background: var(--panel-bg);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 16px 20px;
      box-shadow: 0 2px 8px rgba(0,0,0,0.03);
    }}
    .card-label {{
      font-size: 13px;
      color: var(--muted);
      font-weight: 500;
    }}
    .card-val {{
      font-size: 28px;
      font-weight: 700;
      color: var(--text);
      margin-top: 6px;
    }}
    .panel {{
      background: var(--panel-bg);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 20px 24px;
      margin-bottom: 24px;
      box-shadow: 0 2px 12px rgba(0,0,0,0.03);
    }}
    .panel-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
      margin-bottom: 18px;
      padding-bottom: 14px;
      border-bottom: 1px solid var(--line);
    }}
    .panel-title {{
      font-size: 18px;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .metric-tabs {{
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }}
    .tab-btn {{
      padding: 6px 14px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      font-size: 13px;
      cursor: pointer;
      font-weight: 500;
      transition: all 0.15s;
    }}
    .tab-btn:hover {{
      background: #f1f5f9;
    }}
    .tab-btn.active {{
      background: var(--primary);
      color: #fff;
      border-color: var(--primary);
    }}
    .filter-bar {{
      display: flex;
      align-items: center;
      gap: 16px;
      flex-wrap: wrap;
      background: #f1f5f9;
      padding: 12px 18px;
      border-radius: 8px;
      margin-bottom: 18px;
      font-size: 13px;
    }}
    .filter-item {{
      display: flex;
      align-items: center;
      gap: 6px;
    }}
    .filter-item label {{
      color: var(--muted);
      font-weight: 500;
    }}
    .filter-item select {{
      padding: 6px 10px;
      border: 1px solid #cbd5e1;
      border-radius: 6px;
      background: #fff;
      font-size: 13px;
    }}
    .chart-container {{
      position: relative;
      width: 100%;
      height: 380px;
      background: #fff;
    }}
    svg {{
      width: 100%;
      height: 100%;
      display: block;
    }}
    .faceted-grid {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 16px;
      margin-top: 24px;
    }}
    @media (max-width: 1080px) {{
      .faceted-grid {{ grid-template-columns: 1fr; }}
    }}
    .faceted-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fafafa;
    }}
    .faceted-card h4 {{
      font-size: 14px;
      color: var(--muted);
      margin-bottom: 8px;
      text-align: center;
    }}
    .faceted-svg-wrap {{
      height: 220px;
    }}
    .table-wrap {{
      overflow-x: auto;
      max-height: 480px;
      margin-top: 12px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    th, td {{
      padding: 10px 12px;
      text-align: right;
      border-bottom: 1px solid var(--line);
      white-space: nowrap;
    }}
    th:first-child, td:first-child {{ text-align: left; }}
    th {{
      background: #f8fafc;
      color: var(--muted);
      position: sticky;
      top: 0;
      z-index: 2;
      font-weight: 600;
      cursor: pointer;
      user-select: none;
    }}
    th:hover {{ color: var(--primary); }}
    tr[data-scene-id] {{ cursor: pointer; transition: background 0.15s; }}
    tr[data-scene-id]:hover {{ background: #f0f9ff; }}
    tr.selected-row {{ background: #e0f2fe !important; font-weight: 600; }}
    .badge {{
      display: inline-block;
      padding: 3px 10px;
      border-radius: 12px;
      font-size: 12px;
      font-weight: 600;
    }}
    .badge-success {{ background: #dcfce7; color: #15803d; }}
    .badge-fail {{ background: #fee2e2; color: #b91c1c; }}
    #chart-tooltip {{
      position: absolute;
      display: none;
      background: rgba(15, 23, 42, 0.92);
      color: #fff;
      padding: 10px 14px;
      border-radius: 6px;
      font-size: 12px;
      pointer-events: none;
      z-index: 100;
      box-shadow: 0 4px 14px rgba(0,0,0,0.25);
      line-height: 1.6;
    }}
    .notice {{
      background: #e0f2fe;
      color: #0369a1;
      padding: 12px 16px;
      border-radius: 8px;
      font-size: 13px;
      line-height: 1.6;
      margin-top: 14px;
    }}
  </style>
  <script src="data/curves_u{total_users}.js"></script>
</head>
<body>
  <header>
    <div class="header-top">
      <div class="title-group">
        <h1>{total_users} 人场景群 · 迭代收敛与求解过程深度分析</h1>
        <p>同人数下不同 VIP 占比 (15%/20%/25%) 与初始未达标比例 (5%~60%) 作为变量的多曲线迭代对比 + 逐场景求解全流程深入回放</p>
      </div>
      <div class="nav-group">
        <a href="index.html" class="nav-btn home-btn">📊 返回实验总览</a>
        {nav_links_html}
      </div>
    </div>
  </header>

  <main class="container">
    <!-- View Switcher -->
    <div class="view-switcher">
      <button class="view-tab-btn active" id="tab-btn-compare" onclick="switchTab('compare')">📈 多场景迭代对比与变量分析</button>
      <button class="view-tab-btn" id="tab-btn-detail" onclick="switchTab('detail')">🔬 单场景求解全流程深入分析</button>
    </div>

    <!-- TAB 1: MULTI-SCENARIO COMPARISON -->
    <div id="tab-compare-content">
      <!-- Stat Cards -->
      <section class="stats-cards" id="stats-cards"></section>

      <!-- Main Chart Panel -->
      <section class="panel">
        <div class="panel-header">
          <div class="panel-title">
            <span>📈 多场景随迭代步长 (Iteration) 收敛演化对比</span>
          </div>
          <div class="metric-tabs" id="metric-tabs">
            <button class="tab-btn active" data-metric="vip_met_percent">VIP 质差达标率 (%)</button>
            <button class="tab-btn" data-metric="vip_unmet_count">VIP 未达标人数</button>
            <button class="tab-btn" data-metric="H">系统总效用 H</button>
            <button class="tab-btn" data-metric="price_dl">下行影子价格 λ_DL</button>
            <button class="tab-btn" data-metric="price_ul">上行影子价格 λ_UL</button>
            <button class="tab-btn" data-metric="alloc_dl_mbps">下行分配带宽 (Mbps)</button>
          </div>
        </div>

        <!-- Filter Bar -->
        <div class="filter-bar">
          <div class="filter-item">
            <label>VIP 占比:</label>
            <select id="vip-filter">
              <option value="ALL">全部 (15%, 20%, 25%)</option>
              {''.join(f'<option value="{vr}">{vr}%</option>' for vr in vip_ratios)}
            </select>
          </div>
          <div class="filter-item">
            <label>初始未达标比例:</label>
            <select id="unmet-filter">
              <option value="ALL">全部 (5% ~ 60%)</option>
              {''.join(f'<option value="{ur}">{ur}%</option>' for ur in unmet_ratios)}
            </select>
          </div>
          {'<div class="filter-item"><label>余量 Headroom:</label><select id="headroom-filter"><option value="ALL">全部</option>' + ''.join(f'<option value="{hr}">{hr}%</option>' for hr in headroom_ratios) + '</select></div>' if has_multi_headroom else ''}
          <button class="tab-btn" id="reset-filter-btn" style="margin-left: auto;">重置筛选</button>
        </div>

        <!-- Main SVG Chart -->
        <div class="chart-container">
          <svg id="main-curve-svg"></svg>
          <div id="chart-tooltip"></div>
        </div>
        <p style="font-size: 12px; color: var(--muted); margin-top: 8px;">
          💡 提示：鼠标移动到曲线上可高亮并查看场景详细参数与当轮数值；点击右侧“🔍 求解过程”可直接跳转进入该场景的逐轮深入回放。
        </p>

        <!-- Faceted 3-Subplots (VIP 15%, 20%, 25%) -->
        <div class="faceted-grid">
          <div class="faceted-card">
            <h4>VIP 占比 15% 场景分面对比 (7条未达标曲线)</h4>
            <div class="faceted-svg-wrap"><svg id="facet-15-svg"></svg></div>
          </div>
          <div class="faceted-card">
            <h4>VIP 占比 20% 场景分面对比 (7条未达标曲线)</h4>
            <div class="faceted-svg-wrap"><svg id="facet-20-svg"></svg></div>
          </div>
          <div class="faceted-card">
            <h4>VIP 占比 25% 场景分面对比 (7条未达标曲线)</h4>
            <div class="faceted-svg-wrap"><svg id="facet-25-svg"></svg></div>
          </div>
        </div>
      </section>

      <!-- Detailed Scenario Comparison Table -->
      <section class="panel">
        <div class="panel-header">
          <div class="panel-title">
            <span>📋 {total_users} 人全部场景求解结果对比表</span>
          </div>
          <span id="table-count-label" style="font-size: 13px; color: var(--muted);"></span>
        </div>
        <div class="table-wrap">
          <table id="scenario-table">
            <thead>
              <tr>
                <th data-key="scene_id">场景 ID</th>
                <th data-key="vip_ratio_percent">VIP占比</th>
                <th data-key="vip_users">VIP人数</th>
                <th data-key="requested_unmet_percent">目标未达标%</th>
                <th data-key="initial_unmet_count">初始未达标(人)</th>
                <th data-key="initial_severe_count">深度未达标 [2.5~3.5)</th>
                <th data-key="initial_mild_unmet_count">轻度未达标 [3.5~4.0)</th>
                <th data-key="final_vip_met_count">最终达标(人)</th>
                <th data-key="final_vip_met_percent">最终达标率</th>
                <th data-key="initial_H">初始 H</th>
                <th data-key="final_H">最终 H</th>
                <th data-key="delta_H">ΔH 提升</th>
                <th data-key="iterations">收敛轮数</th>
                <th data-key="status">状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody id="scenario-tbody"></tbody>
          </table>
        </div>
      </section>
    </div>

    <!-- TAB 2: PER-SCENARIO SOLVING PROCESS DEEP-DIVE -->
    <div id="tab-detail-content" style="display: none;">
      <!-- Detail Toolbar -->
      <div class="toolbar" style="background: #fff; padding: 16px 20px; border-radius: 10px; border: 1px solid var(--line); margin-bottom: 20px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
        <label for="scene-select" style="font-weight: 600;">选择查看场景:</label>
        <select id="scene-select" style="padding: 8px 12px; border: 1px solid #cbd5e1; border-radius: 6px; min-width: 360px; font-weight: 500;">
          {scene_options_html}
        </select>
        <button id="prev-scene-btn" class="tab-btn">◀ 上一场景</button>
        <button id="next-scene-btn" class="tab-btn">下一场景 ▶</button>
        <span id="detail-status-badge" class="badge" style="padding: 6px 12px;"></span>
        <span id="detail-meta" style="color: var(--muted); font-size: 13px; margin-left: auto;"></span>
      </div>

      <div id="scene-loading-indicator" style="display: none; padding: 40px; text-align: center; color: var(--muted); font-size: 16px;">
        ⏳ 正在加载场景数据...
      </div>

      <div id="scene-detail-container">
        <!-- Panel 1: 求解前后对比 -->
        <section class="panel">
          <div class="panel-header">
            <div class="panel-title">
              <span id="detail-comparison-title">求解前后对比汇总</span>
            </div>
          </div>
          <div id="detail-comparison-table" class="table-wrap"></div>
          <p id="detail-people-summary" style="margin-top: 10px; font-size: 13px; color: var(--muted); font-weight: 500;"></p>
          <p style="font-size: 12px; color: var(--muted); margin-top: 6px;">
            指标按套餐分开统计；KQI变化为该套餐所有有效业务方向的平均“求解后−求解前”。丢包率和卡顿率使用百分点；基本保障不足与目标达标只评价 VIP。
          </p>
        </section>

        <!-- Panel 2: 效用与参考答案 & 初始套餐分布 -->
        <section class="panel">
          <div class="panel-header"><div class="panel-title"><span>🎯 效用与参考答案</span></div></div>
          <div id="detail-reference-table" class="table-wrap"></div>
          
          <div class="panel-header" style="margin-top: 24px;"><div class="panel-title"><span>👥 初始套餐分布</span></div></div>
          <div id="detail-distribution-table" class="table-wrap"></div>
        </section>

        <!-- Panel 3: 当前场景：余量与带宽上限 -->
        <section class="panel">
          <div class="panel-header"><div class="panel-title"><span>⚡ 当前场景：余量与带宽上限</span></div></div>
          <p id="detail-capacity-note" class="notice"></p>
          <div id="detail-capacity-table" class="table-wrap"></div>
          <p style="font-size: 12px; color: var(--muted); margin-top: 8px;">
            单位 kbps。求解可用上限 = 小区总上限 − 非受控占用 − 预留；余量比例 =（求解可用上限 − 初始用户占用）÷ 初始用户占用。上下行分别约束。
          </p>
        </section>

        <!-- Panel 4: 4 Single-Scene Iteration Curves -->
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 24px;">
          <section class="panel" style="margin-bottom: 0;">
            <div class="panel-header"><div class="panel-title"><span>📶 上行带宽 / Mbps (迭代收敛)</span></div></div>
            <div id="detail-chart-ul" style="height: 280px;"></div>
          </section>
          <section class="panel" style="margin-bottom: 0;">
            <div class="panel-header"><div class="panel-title"><span>📶 下行带宽 / Mbps (迭代收敛)</span></div></div>
            <div id="detail-chart-dl" style="height: 280px;"></div>
          </section>
          <section class="panel" style="margin-bottom: 0;">
            <div class="panel-header"><div class="panel-title"><span>📈 当轮协调方案收益 H (迭代收敛)</span></div></div>
            <div id="detail-chart-utility" style="height: 280px;"></div>
            <p style="font-size: 12px; color: var(--muted); margin-top: 4px;">同时显示当轮方案与历史最好方案。</p>
          </section>
          <section class="panel" style="margin-bottom: 0;">
            <div class="panel-header"><div class="panel-title"><span>💰 资源影子价格 (迭代收敛)</span></div></div>
            <div id="detail-chart-prices" style="height: 280px;"></div>
          </section>
        </div>

        <!-- Panel 5: 用户体验筛选与逐轮轨迹 -->
        <section class="panel">
          <div class="panel-header"><div class="panel-title"><span>👤 用户体验筛选与逐轮轨迹 (MOS & KQI)</span></div></div>
          <div class="analysis-toolbar" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; background: #f8fafc; padding: 14px; border-radius: 8px; margin-bottom: 14px;">
            <div>
              <label style="font-size: 12px; color: var(--muted); font-weight: 500;">用户类型</label>
              <select id="user-package-filter" style="width: 100%; padding: 6px; border: 1px solid #cbd5e1; border-radius: 6px; margin-top: 4px;">
                <option value="ALL">全部</option>
                <option value="vip">VIP</option>
                <option value="normal">普通用户</option>
              </select>
            </div>
            <div>
              <label style="font-size: 12px; color: var(--muted); font-weight: 500;">MOS变化</label>
              <select id="user-mos-trend" style="width: 100%; padding: 6px; border: 1px solid #cbd5e1; border-radius: 6px; margin-top: 4px;">
                <option value="ALL">全部</option>
                <option value="UP">上升</option>
                <option value="DOWN">下降</option>
                <option value="SAME">不变</option>
                <option value="NA">无MOS</option>
              </select>
            </div>
            <div>
              <label style="font-size: 12px; color: var(--muted); font-weight: 500;">KQI指标</label>
              <select id="user-kqi-metric" style="width: 100%; padding: 6px; border: 1px solid #cbd5e1; border-radius: 6px; margin-top: 4px;">
                <option value="avg_qoe">avgQoe</option>
                <option value="bitrate_kbps">码率</option>
                <option value="resolution">分辨率</option>
                <option value="service_delay_ms">时延</option>
                <option value="loss_ratio">丢包率</option>
                <option value="stall_ratio">卡顿率</option>
                <option value="stalling_level">卡顿档位</option>
                <option value="buffer_ms">首缓</option>
                <option value="jitter_ms">抖动</option>
              </select>
            </div>
            <div>
              <label style="font-size: 12px; color: var(--muted); font-weight: 500;">KQI方向</label>
              <select id="user-kqi-direction" style="width: 100%; padding: 6px; border: 1px solid #cbd5e1; border-radius: 6px; margin-top: 4px;">
                <option value="ALL">全部方向均值</option>
                <option value="ul">上行 UL</option>
                <option value="dl">下行 DL</option>
                <option value="session">会话</option>
              </select>
            </div>
            <div>
              <label style="font-size: 12px; color: var(--muted); font-weight: 500;">KQI变化</label>
              <select id="user-kqi-trend" style="width: 100%; padding: 6px; border: 1px solid #cbd5e1; border-radius: 6px; margin-top: 4px;">
                <option value="ALL">全部</option>
                <option value="UP">上升</option>
                <option value="DOWN">下降</option>
                <option value="SAME">不变</option>
                <option value="NA">无数据</option>
              </select>
            </div>
          </div>

          <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 12px;">
            <label for="trajectory-user" style="font-size: 13px; font-weight: 600;">选中用户:</label>
            <select id="trajectory-user" style="padding: 6px 12px; border: 1px solid #cbd5e1; border-radius: 6px; min-width: 260px;"></select>
            <span id="user-filter-summary" style="font-size: 13px; color: var(--muted);"></span>
          </div>

          <div id="user-filter-table" class="table-wrap" style="max-height: 280px;"></div>
          <p style="font-size: 12px; color: var(--muted); margin-top: 6px;">
            变化比较使用“算法返回方案 − 初始状态”。点击用户行可即时在下方查看该用户的逐轮 MOS 与 KQI 变化折线图。
          </p>

          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--line);">
            <div>
              <h4 id="user-mos-title" style="font-size: 15px; margin-bottom: 10px; color: #1e293b;">MOS 逐轮变化</h4>
              <div id="user-mos-chart" style="height: 250px;"></div>
            </div>
            <div>
              <h4 id="user-kqi-title" style="font-size: 15px; margin-bottom: 10px; color: #1e293b;">KQI 逐轮变化</h4>
              <div id="user-kqi-chart" style="height: 250px;"></div>
            </div>
          </div>
        </section>

        <!-- Panel 6: 逐轮策略与状态回放 -->
        <section class="panel">
          <div class="panel-header">
            <div class="panel-title"><span>🔄 逐轮策略与状态回放</span></div>
            <div style="display: flex; align-items: center; gap: 8px;">
              <button id="round-prev-btn" class="tab-btn">◀ 上一轮</button>
              <label for="round-select" style="font-size: 13px; font-weight: 600;">当前轮次:</label>
              <select id="round-select" style="padding: 6px 10px; border: 1px solid #cbd5e1; border-radius: 6px;"></select>
              <button id="round-next-btn" class="tab-btn">下一轮 ▶</button>
              <button id="download-evidence-btn" class="tab-btn" style="background: #0284c7; color: #fff; border-color: #0284c7;">📥 下载完整证据 JSON</button>
            </div>
          </div>

          <p id="round-state-notice" class="notice"></p>

          <h4 style="font-size: 15px; margin: 16px 0 8px;">📊 本轮 KQI 变化汇总</h4>
          <div id="round-kqi-summary-table" class="table-wrap"></div>

          <h4 style="font-size: 15px; margin: 20px 0 8px;">📋 KQI 明细（初始 → 当前）</h4>
          <div id="round-kqi-table" class="table-wrap" style="max-height: 320px;"></div>

          <h4 style="font-size: 15px; margin: 20px 0 8px;">👥 本轮六维分组决策</h4>
          <div id="round-groups-table" class="table-wrap"></div>

          <h4 style="font-size: 15px; margin: 20px 0 8px;">👤 逐用户策略（上轮 → 请求 → 协调后）</h4>
          <div id="round-users-table" class="table-wrap" style="max-height: 360px;"></div>

          <div style="margin-top: 20px; padding: 14px; background: #f8fafc; border: 1px solid var(--line); border-radius: 8px;">
            <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px;">
              <h4 style="font-size: 15px; margin: 0;">💡 用户动作决策解析 (为什么选择这个动作)</h4>
              <label for="candidate-user-select" style="font-size: 13px; margin-left: 10px;">查看用户:</label>
              <select id="candidate-user-select" style="padding: 4px 10px; border: 1px solid #cbd5e1; border-radius: 6px;"></select>
            </div>
            <p style="font-size: 12px; color: var(--muted); margin-bottom: 8px;">
              个人请求最大化 H − 影子资源成本；同分时依次按初始锚点、较低总带宽、动作 ID 决胜。协调器再检查共同容量并可能调整。
            </p>
            <div id="candidate-actions-table" class="table-wrap" style="max-height: 240px;"></div>
          </div>

          <h4 style="font-size: 15px; margin: 20px 0 8px;">⚖️ 资源协调每一步事件记录</h4>
          <div id="round-events-table" class="table-wrap"></div>

          <h4 style="font-size: 15px; margin: 20px 0 8px;">💰 效用分项与影子成本</h4>
          <div id="round-components-table" class="table-wrap" style="max-height: 280px;"></div>
        </section>
      </div>
    </div>
  </main>

  <script>
    {_render_svg_script()}
    {_render_single_scene_script()}

    const GROUP_DATA = window.CURVES_DATA_U{total_users} || {{ scenes: [] }};
    const SCENES = GROUP_DATA.scenes || [];

    function switchTab(tabName) {{
      const tabCompareBtn = document.getElementById('tab-btn-compare');
      const tabDetailBtn = document.getElementById('tab-btn-detail');
      const compareContent = document.getElementById('tab-compare-content');
      const detailContent = document.getElementById('tab-detail-content');

      if (tabName === 'compare') {{
        tabCompareBtn.classList.add('active');
        tabDetailBtn.classList.remove('active');
        compareContent.style.display = 'block';
        detailContent.style.display = 'none';
      }} else {{
        tabCompareBtn.classList.remove('active');
        tabDetailBtn.classList.add('active');
        compareContent.style.display = 'none';
        detailContent.style.display = 'block';
        const sceneId = document.getElementById('scene-select').value || (SCENES[0] && SCENES[0].scene_id);
        if (sceneId) {{
          loadAndShowScene(sceneId);
        }}
      }}
    }}

    function openSceneDetail(sceneId) {{
      document.getElementById('scene-select').value = sceneId;
      switchTab('detail');
      loadAndShowScene(sceneId);
      window.scrollTo({{ top: 0, behavior: 'smooth' }});
    }}

    // Stats Cards Render
    function renderStats() {{
      const successCount = SCENES.filter(s => s.status === 'SUCCESS').length;
      const avgMetRate = SCENES.length ? (SCENES.reduce((a, b) => a + b.final_vip_met_percent, 0) / SCENES.length).toFixed(1) : '—';
      const avgIters = SCENES.length ? (SCENES.reduce((a, b) => a + b.iterations, 0) / SCENES.length).toFixed(1) : '—';
      const avgDeltaH = SCENES.length ? (SCENES.reduce((a, b) => a + b.delta_H, 0) / SCENES.length).toFixed(1) : '—';

      document.getElementById('stats-cards').innerHTML = `
        <div class="card"><div class="card-label">本组场景总数</div><div class="card-val">${{SCENES.length}}</div></div>
        <div class="card"><div class="card-label">求解成功率</div><div class="card-val" style="color: var(--success);">${{((successCount/Math.max(1, SCENES.length))*100).toFixed(0)}}%</div></div>
        <div class="card"><div class="card-label">最终 VIP 平均达标率</div><div class="card-val" style="color: var(--primary);">${{avgMetRate}}%</div></div>
        <div class="card"><div class="card-label">平均收敛轮数</div><div class="card-val">${{avgIters}} 轮</div></div>
        <div class="card"><div class="card-label">平均总效用增益 ΔH</div><div class="card-val" style="color: var(--success);">+${{avgDeltaH}}</div></div>
      `;
    }}

    function updateCharts() {{
      const filtered = getFilteredScenes(SCENES);
      renderMultiCurveChart('main-curve-svg', filtered, activeMetric);

      const s15 = SCENES.filter(s => s.vip_ratio_percent === 15);
      const s20 = SCENES.filter(s => s.vip_ratio_percent === 20);
      const s25 = SCENES.filter(s => s.vip_ratio_percent === 25);
      renderMultiCurveChart('facet-15-svg', s15, activeMetric, {{ compact: true, width: 400, height: 220 }});
      renderMultiCurveChart('facet-20-svg', s20, activeMetric, {{ compact: true, width: 400, height: 220 }});
      renderMultiCurveChart('facet-25-svg', s25, activeMetric, {{ compact: true, width: 400, height: 220 }});

      renderTable(filtered);
    }}

    function renderTable(scenes) {{
      const tbody = document.getElementById('scenario-tbody');
      tbody.innerHTML = '';
      document.getElementById('table-count-label').textContent = `显示 ${{scenes.length}} / ${{SCENES.length}} 个场景`;

      scenes.forEach(s => {{
        const tr = document.createElement('tr');
        tr.dataset.sceneId = s.scene_id;
        const statusBadge = s.status === 'SUCCESS' ? '<span class="badge badge-success">成功</span>' : '<span class="badge badge-fail">未收敛</span>';

        tr.innerHTML = `
          <td><strong>${{s.scene_id}}</strong></td>
          <td>${{s.vip_ratio_percent}}%</td>
          <td>${{s.vip_users}}</td>
          <td>${{s.requested_unmet_percent}}%</td>
          <td>${{s.initial_unmet_count}}</td>
          <td style="color: #ea580c; font-weight: 600;">${{s.initial_severe_count}}</td>
          <td style="color: #0284c7;">${{s.initial_mild_unmet_count}}</td>
          <td style="font-weight: 600;">${{s.final_vip_met_count}} / ${{s.vip_users}}</td>
          <td style="color: ${{s.final_vip_met_percent >= 90 ? 'var(--success)' : 'var(--warning)'}}; font-weight: 700;">${{s.final_vip_met_percent}}%</td>
          <td>${{s.initial_H}}</td>
          <td>${{s.final_H}}</td>
          <td style="color: var(--success); font-weight: 600;">+${{s.delta_H}}</td>
          <td>${{s.iterations}}</td>
          <td>${{statusBadge}}</td>
          <td><button class="tab-btn" onclick="event.stopPropagation(); openSceneDetail('${{s.scene_id}}')" style="padding: 4px 10px; font-size: 11px; background: #e0f2fe; color: #0284c7; border-color: #bae6fd; font-weight: 600;">🔍 深入分析</button></td>
        `;

        tr.addEventListener('click', () => {{
          highlightScene(s.scene_id);
          document.getElementById('main-curve-svg').scrollIntoView({{ behavior: 'smooth', block: 'center' }});
        }});

        tbody.appendChild(tr);
      }});
    }}

    // Metric Tabs
    document.querySelectorAll('#metric-tabs button').forEach(btn => {{
      btn.addEventListener('click', () => {{
        document.querySelectorAll('#metric-tabs button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        activeMetric = btn.dataset.metric;
        updateCharts();
      }});
    }});

    // Filter controls
    document.getElementById('vip-filter').addEventListener('change', updateCharts);
    document.getElementById('unmet-filter').addEventListener('change', updateCharts);
    if (document.getElementById('headroom-filter')) {{
      document.getElementById('headroom-filter').addEventListener('change', updateCharts);
    }}
    document.getElementById('reset-filter-btn').addEventListener('click', () => {{
      document.getElementById('vip-filter').value = 'ALL';
      document.getElementById('unmet-filter').value = 'ALL';
      if (document.getElementById('headroom-filter')) document.getElementById('headroom-filter').value = 'ALL';
      updateCharts();
    }});

    // Tab 2 Event Listeners
    document.getElementById('scene-select').addEventListener('change', (e) => {{
      loadAndShowScene(e.target.value);
    }});
    document.getElementById('prev-scene-btn').addEventListener('click', () => {{
      const sel = document.getElementById('scene-select');
      if (sel.selectedIndex > 0) {{
        sel.selectedIndex--;
        loadAndShowScene(sel.value);
      }}
    }});
    document.getElementById('next-scene-btn').addEventListener('click', () => {{
      const sel = document.getElementById('scene-select');
      if (sel.selectedIndex < sel.options.length - 1) {{
        sel.selectedIndex++;
        loadAndShowScene(sel.value);
      }}
    }});

    ['user-package-filter', 'user-mos-trend', 'user-kqi-metric', 'user-kqi-direction', 'user-kqi-trend'].forEach(id => {{
      document.getElementById(id).addEventListener('change', renderUserAnalysis);
    }});
    document.getElementById('trajectory-user').addEventListener('change', renderUserTrajectory);

    document.getElementById('round-select').addEventListener('change', renderRound);
    document.getElementById('candidate-user-select').addEventListener('change', renderCandidates);
    document.getElementById('round-prev-btn').addEventListener('click', () => {{
      const sel = document.getElementById('round-select');
      if (sel.selectedIndex > 0) {{
        sel.selectedIndex--;
        renderRound();
      }}
    }});
    document.getElementById('round-next-btn').addEventListener('click', () => {{
      const sel = document.getElementById('round-select');
      if (sel.selectedIndex < sel.options.length - 1) {{
        sel.selectedIndex++;
        renderRound();
      }}
    }});

    document.getElementById('download-evidence-btn').addEventListener('click', () => {{
      if (!currentLoadedScene) return;
      const url = URL.createObjectURL(new Blob([JSON.stringify(currentLoadedScene, null, 2)], {{ type: 'application/json' }}));
      const a = document.createElement('a');
      a.href = url;
      a.download = currentLoadedScene.id + '_evidence.json';
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }});

    // Init
    renderStats();
    updateCharts();

    // Check URL parameters for direct scene navigation
    const urlParams = new URLSearchParams(window.location.search);
    const targetScene = urlParams.get('scene') || window.location.hash.replace('#', '');
    if (targetScene && SCENES.some(s => s.scene_id === targetScene)) {{
      openSceneDetail(targetScene);
    }}
  </script>
</body>
</html>
"""


def _generate_index_html(summary_data: Dict[str, Any], user_groups: List[int]) -> str:
    """Generate the root summary dashboard."""
    all_scenes = summary_data.get("all_scenes", [])
    total_scenes = len(all_scenes)
    success_count = sum(s["status"] == "SUCCESS" for s in all_scenes)
    avg_met_rate = (
        sum(s["final_vip_met_percent"] for s in all_scenes) / total_scenes
        if total_scenes else 0.0
    )
    avg_iters = (
        sum(s["iterations"] for s in all_scenes) / total_scenes
        if total_scenes else 0.0
    )
    total_ms = sum(s["elapsed_ms"] for s in all_scenes)

    group_cards_html = ""
    for u in user_groups:
        g_scenes = summary_data.get("groups", {}).get(u, [])
        g_success = sum(s["status"] == "SUCCESS" for s in g_scenes)
        g_met_avg = sum(s["final_vip_met_percent"] for s in g_scenes) / len(g_scenes) if g_scenes else 0.0
        g_iter_avg = sum(s["iterations"] for s in g_scenes) / len(g_scenes) if g_scenes else 0.0
        group_cards_html += f"""
        <div class="group-card">
          <div class="group-header">
            <h3>{u} 人场景群</h3>
            <span class="badge badge-info">{len(g_scenes)} 个场景</span>
          </div>
          <div class="group-metrics">
            <div>求解成功率: <strong>{((g_success/max(1, len(g_scenes)))*100):.0f}%</strong> ({g_success}/{len(g_scenes)})</div>
            <div>VIP 最终达标率: <strong>{g_met_avg:.1f}%</strong></div>
            <div>平均收敛轮数: <strong>{g_iter_avg:.1f} 轮</strong></div>
          </div>
          <a href="report_u{u}.html" class="enter-btn">进入多曲线对比与全流程回放 ➔</a>
        </div>
        """

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>VIP 初始场景求解 · 实验汇总驾驶舱</title>
  <style>
    :root {{
      --primary: #0284c7;
      --bg: #f8fafc;
      --line: #e2e8f0;
      --text: #0f172a;
      --muted: #64748b;
      --success: #10b981;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
    }}
    header {{
      background: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #0369a1 100%);
      color: #fff;
      padding: 32px 40px;
    }}
    header h1 {{ font-size: 28px; font-weight: 700; }}
    header p {{ color: #94a3b8; font-size: 14px; margin-top: 6px; }}
    .container {{ max-width: 1440px; margin: 0 auto; padding: 28px; }}
    .stats-cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 16px;
      margin-bottom: 28px;
    }}
    .card {{
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 18px 22px;
    }}
    .card-label {{ font-size: 13px; color: var(--muted); }}
    .card-val {{ font-size: 28px; font-weight: 700; margin-top: 6px; }}
    .group-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 20px;
      margin-bottom: 32px;
    }}
    .group-card {{
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 24px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      transition: transform 0.2s, box-shadow 0.2s;
    }}
    .group-card:hover {{
      transform: translateY(-2px);
      box-shadow: 0 8px 24px rgba(0,0,0,0.06);
    }}
    .group-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 16px;
    }}
    .group-header h3 {{ font-size: 20px; }}
    .badge {{
      padding: 4px 10px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 600;
    }}
    .badge-info {{ background: #e0f2fe; color: #0369a1; }}
    .group-metrics {{
      margin: 16px 0 24px;
      display: grid;
      gap: 8px;
      font-size: 14px;
    }}
    .enter-btn {{
      display: block;
      text-align: center;
      background: var(--primary);
      color: #fff;
      text-decoration: none;
      padding: 10px 16px;
      border-radius: 8px;
      font-weight: 600;
      font-size: 14px;
      transition: background 0.15s;
    }}
    .enter-btn:hover {{ background: #0369a1; }}
    .panel {{
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 24px;
      margin-bottom: 24px;
    }}
    .panel h2 {{ font-size: 18px; margin-bottom: 16px; }}
    .table-wrap {{ overflow-x: auto; max-height: 480px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ padding: 10px 14px; text-align: right; border-bottom: 1px solid var(--line); white-space: nowrap; }}
    th:first-child, td:first-child {{ text-align: left; }}
    th {{ background: #f8fafc; color: var(--muted); position: sticky; top: 0; z-index: 2; }}
    tr:hover {{ background: #f8fafc; }}
  </style>
  <script src="data/summary.js"></script>
</head>
<body>
  <header>
    <h1>VIP 初始场景求解 · 实验汇总驾驶舱</h1>
    <p>全量多场景求解概况 · 人数独立专项切片 · 数据存储解耦架构 · 支持逐场景求解过程深度回放</p>
  </header>

  <main class="container">
    <div class="stats-cards">
      <div class="card"><div class="card-label">全量场景数</div><div class="card-val">{total_scenes}</div></div>
      <div class="card"><div class="card-label">求解成功率</div><div class="card-val" style="color: var(--success);">{((success_count/max(1, total_scenes))*100):.0f}%</div></div>
      <div class="card"><div class="card-label">VIP 最终平均达标率</div><div class="card-val" style="color: var(--primary);">{avg_met_rate:.1f}%</div></div>
      <div class="card"><div class="card-label">全局平均收敛轮数</div><div class="card-val">{avg_iters:.1f} 轮</div></div>
      <div class="card"><div class="card-label">总计算耗时</div><div class="card-val">{(total_ms/1000):.1f} s</div></div>
    </div>

    <h2 style="font-size: 18px; margin-bottom: 16px;">📁 按总人数切片独立报告 (点击进入多曲线迭代与详细求解过程回放)</h2>
    <div class="group-grid">
      {group_cards_html}
    </div>

    <!-- Overall Table -->
    <div class="panel">
      <h2>🌐 全量场景索引与求解结果清单</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>场景 ID</th>
              <th>总人数</th>
              <th>VIP 占比</th>
              <th>VIP 人数</th>
              <th>初始未达标%</th>
              <th>初始未达标(人)</th>
              <th>深度未达标 [2.5~3.5)</th>
              <th>轻度未达标 [3.5~4.0)</th>
              <th>最终达标(人)</th>
              <th>最终达标率</th>
              <th>ΔH 提升</th>
              <th>收敛轮数</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {''.join(f"""
            <tr>
              <td><a href="report_u{s['total_users']}.html?scene={s['scene_id']}" style="color: #0284c7; text-decoration: none; font-weight: 600;">{s['scene_id']}</a></td>
              <td>{s['total_users']}</td>
              <td>{s['vip_ratio_percent']}%</td>
              <td>{s['vip_users']}</td>
              <td>{s['requested_unmet_percent']}%</td>
              <td>{s['initial_unmet_count']}</td>
              <td style="color: #ea580c; font-weight: 600;">{s['initial_severe_count']}</td>
              <td style="color: #0284c7;">{s['initial_mild_unmet_count']}</td>
              <td>{s['final_vip_met_count']} / {s['vip_users']}</td>
              <td style="color: {'#10b981' if s['final_vip_met_percent'] >= 90 else '#f59e0b'}; font-weight: 700;">{s['final_vip_met_percent']}%</td>
              <td style="color: #10b981;">+{s['delta_H']}</td>
              <td>{s['iterations']}</td>
              <td>{s['status']}</td>
              <td><a href="report_u{s['total_users']}.html?scene={s['scene_id']}" style="padding: 4px 8px; border-radius: 4px; background: #e0f2fe; color: #0284c7; text-decoration: none; font-size: 11px; font-weight: 600;">🔬 深入回放</a></td>
            </tr>
            """ for s in all_scenes)}
          </tbody>
        </table>
      </div>
    </div>
  </main>
</body>
</html>
"""


def generate_grid_reports(input_dir: str | Path, output_dir: Optional[str | Path] = None) -> Path:
    """Generate decoupled data files, per-scenario detail JS, and per-population HTML reports."""
    src = Path(input_dir)
    out = Path(output_dir) if output_dir else src

    data_dir = out / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # 1. Ensure decoupled per-scenario JS files exist
    scenes_dir = data_dir / "scenes"
    if not scenes_dir.exists() or len(list(scenes_dir.glob("*.js"))) < 200:
        export_scene_detail_payloads(src, out)

    grid_data = build_grid_data(src)
    all_groups = grid_data["user_groups"]

    # 2. Export summary data
    summary_json_str = json.dumps(
        {
            "summary": grid_data["summary"],
            "total_scenes": grid_data["total_scenes"],
            "user_groups": all_groups,
            "all_scenes": [
                {k: v for k, v in sc.items() if k != "curves"}
                for sc in grid_data["all_scenes"]
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
    (data_dir / "summary.json").write_text(summary_json_str, encoding="utf-8")
    (data_dir / "summary.js").write_text(
        f"window.SUMMARY_DATA = {summary_json_str};\n", encoding="utf-8"
    )

    # 3. Export per-population curves & reports
    for users in all_groups:
        group_scenes = grid_data["groups"].get(users, [])
        group_payload = {
            "total_users": users,
            "scenes": group_scenes,
        }
        json_str = json.dumps(group_payload, ensure_ascii=False, indent=2)

        (data_dir / f"curves_u{users}.json").write_text(json_str, encoding="utf-8")
        (data_dir / f"curves_u{users}.js").write_text(
            f"window.CURVES_DATA_U{users} = {json_str};\n", encoding="utf-8"
        )

        report_html = _generate_group_html(users, group_scenes, all_groups)
        (out / f"report_u{users}.html").write_text(report_html, encoding="utf-8")

    # 4. Export global summary index.html
    index_html = _generate_index_html(grid_data, all_groups)
    (out / "index.html").write_text(index_html, encoding="utf-8")

    return out
