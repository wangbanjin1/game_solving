"""Read stored results only; never regenerate data or invoke the solver."""
import json
from pathlib import Path
from importlib.resources import files


def _index(path, required=False):
    if not path.exists() and not required:
        return {}
    records = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = row["scene_id"]
        if key in records:
            raise ValueError(f"DUPLICATE_SCENE: {path.name}: {key}")
        records[key] = row
    return records


def generate_report(input_dir, output_path):
    source = Path(input_dir)
    target = Path(output_path)
    if target.suffix.lower() != ".html":
        raise ValueError("OUTPUT_MUST_BE_HTML")
    if target.exists():
        raise ValueError("OUTPUT_EXISTS: choose a new HTML path")
    results = _index(source / "solve_results.jsonl", required=True)
    if not results:
        raise ValueError("EMPTY_RESULTS")
    comparisons = _index(source / "comparison.jsonl")
    references = _index(source / "reference_results.jsonl")
    distributions = _index(source / "initial_distribution.jsonl")
    inputs = _index(source / "solver_inputs.jsonl")
    detailed_traces = {}
    trace_path = source / "iteration_trace.jsonl"
    if trace_path.exists():
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                detailed_traces.setdefault(row["scene_id"], []).append(row)
    config_path = source / "resolved_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    cell = config.get("cell", {})
    scenes = []
    for sid, result in results.items():
        snapshot = inputs.get(sid, {})
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
        scenes.append({"id": sid, "status": result.get("run_status") or ("SUCCESS" if result.get("decisions") and result.get("stop_reason") == "CONVERGED_LOCAL" else "FAILED"), "solution": result.get("solution_status"), "stop": result.get("stop_reason"), "iterations": result.get("iterations", 0), "steps": result.get("total_steps", 0), "ms": result.get("elapsed_ms", 0), "feasible": bool(result.get("decisions")), "trace": result.get("trace", []), "comparison": comparisons.get(sid), "capacity": capacity})
        scenes[-1].update(reference=references.get(sid), distribution=distributions.get(sid),
                          resource_budget=resource_budget,
                          capacity_mode=config.get("generation", {}).get("capacity_mode"),
                          users=snapshot.get("users", []),
                          decisions=result.get("decisions", []),
                          terminal_decisions=result.get("terminal_decisions", []),
                          detail_trace=detailed_traces.get(sid, []),
                          H_returned=sum(a["h"] for a in result.get("decisions", [])),
                          H_terminal=sum(a["h"] for a in result.get("terminal_decisions", [])) if result.get("terminal_decisions") else None,
                          returned_matches_terminal=result.get("returned_matches_terminal"),
                          output_selection=result.get("output_selection"))
    payload = json.dumps({"scenes": scenes}, ensure_ascii=False, allow_nan=False)
    # Embedded JSON cannot terminate the script element, even for external inputs.
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = files(__package__).joinpath("template.html").read_text(encoding="utf-8")
    document = template.replace("__REPORT_DATA__", payload)
    detail = files(__package__).joinpath("strategy_detail.html").read_text(encoding="utf-8")
    document = document.replace("</body>", detail + "</body>")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(document)
    return target.resolve()
