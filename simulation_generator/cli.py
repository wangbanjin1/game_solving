import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from experience_solver.config import load_config
from experience_solver.solver import Solver
from experience_solver.schemas import available, validate_snapshot
from .generator import DatasetGenerator, digest
from .range_catalog import catalog, source_valid
from .reference import universe, exact_reference, metrics


def write(path, value, jsonl=False):
    tmp = path.with_suffix(path.suffix+'.tmp')
    data = '\n'.join(json.dumps(x, ensure_ascii=False, sort_keys=True, allow_nan=False) for x in value)+'\n' if jsonl else json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+'\n'
    tmp.write_text(data, encoding='utf-8')
    tmp.replace(path)


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def validate_outputs(scenes, inputs, config):
    from experience_solver.bandwidth_model import BandwidthModel
    from experience_solver.mos_model import MosModel
    bw, mos = BandwidthModel(config), MosModel(config)
    checks = 0
    forbidden = {'kqi_final','kqi_proposal','mos_computed','requested_mos_band','realized_mos_band','target_bandwidth_mbps','w_eval','tolerance_true','position_true','reference_action_ids'}
    for scene,snapshot in zip(scenes,inputs):
        validate_snapshot(snapshot, config)
        assert sum(r['current_bandwidth_mbps'] for r in snapshot['roles']) <= available(snapshot)+1e-6
        assert len(scene['roles']) == config['population']['users_per_cell']
        for truth,role in zip(scene['roles'],snapshot['roles']):
            assert not forbidden.intersection(role)
            if role['is_key_business']:
                k = truth['kqi_final']
                media = {key:k[key] for key in ('resolution','bitrate_kbps')}
                forward = bw.forward(role['current_bandwidth_mbps'], media, role['current_profile_id'])
                assert source_valid(role['business_id'], forward)
                assert all(abs(forward[key]-value) < 1e-8 for key,value in k.items() if value is not None)
                assert abs(mos.evaluate(forward,role['formula_profile'],role['stream_phase'])-truth['mos_computed']) < 1e-10
            checks += 1
    return {'valid': True, 'scenes_validated': len(scenes), 'roles_validated': checks, 'checks': ['schema_units','capacity','source_ranges','forward_roundtrip','mos_recomputed','label_isolation','serialized_json_reloaded']}


def main(argv=None):
    parser = argparse.ArgumentParser(description='合成数据生成、资源博弈求解与闭环评测')
    parser.add_argument('command', choices=['generate','solve','run'])
    parser.add_argument('--config', type=Path)
    parser.add_argument('--output', type=Path, default=Path('outputs/demo'))
    parser.add_argument('--input', type=Path, help='solve 命令的 solver_inputs.jsonl')
    parser.add_argument('--max-steps', type=int, help='覆盖 solver.max_iterations，并写入 resolved_config')
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.max_steps is not None:
            if args.max_steps < 1:
                raise ValueError('max-steps 必须为正整数')
            config['solver']['max_iterations'] = args.max_steps
        out = args.output
        out.mkdir(parents=True, exist_ok=True)
        # Do not silently mix a new run with artifacts from an old run.
        if any(out.iterdir()):
            raise ValueError('OUTPUT_NOT_EMPTY: 请使用新的输出目录')
        write(out/'resolved_config.json', config)
        try:
            revision = subprocess.check_output(['git','rev-parse','HEAD'], stderr=subprocess.DEVNULL, text=True).strip()
        except (OSError,subprocess.CalledProcessError):
            revision = 'unavailable'
        root = Path(__file__).resolve().parents[1]
        hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for folder in ('experience_solver','simulation_generator','configs') for p in sorted((root/folder).rglob('*')) if p.is_file() and p.suffix in ('.py','.json')}
        write(out/'manifest.json', {'source': 'synthetic', 'seed': config['seed'], 'config_hash': digest(config), 'source_revision': revision, 'source_file_hashes': hashes, 'python_version': sys.version, 'dependencies': 'Python standard library only', 'input_source': str(args.input) if args.input else 'generated', 'input_sha256': hashlib.sha256(args.input.read_bytes()).hexdigest() if args.input else None})
        if args.command != 'solve':
            scenes, inputs, labels, report = DatasetGenerator(config).generate()
            write(out/'scenes.jsonl', scenes, True)
            write(out/'solver_inputs.jsonl', inputs, True)
            write(out/'labels.jsonl', labels, True)
            write(out/'source_catalog.json', catalog())
            write(out/'generation_report.json', report)
            validation = validate_outputs(read_lines(out/'scenes.jsonl'),read_lines(out/'solver_inputs.jsonl'),config)
            validation['complete'] = report['failed'] == 0
            write(out/'validation_report.json', validation)
            if report['failed'] and not config['allow_partial']:
                print(json.dumps({'status':'GENERATION_FAILED', 'report':str(out/'generation_report.json'), 'failures': report['failures']},ensure_ascii=False))
                return 2
        else:
            if not args.input:
                raise ValueError('solve 需要 --input')
            inputs = read_lines(args.input)
        if args.command in ('solve','run'):
            results, evaluations, references, candidate_sets = [], [], [], []
            for snapshot in inputs:
                pools = universe(snapshot,config) if config['reference']['exact_enabled'] else None
                ref = exact_reference(snapshot,pools,config) if pools is not None else {'reference_status':'not_computed'}
                result = Solver(config).solve(snapshot,candidate_universe=pools)
                results.append(result)
                references.append({'scene_id': snapshot['scene_id'], **ref})
                evaluations.append(metrics(snapshot,result,ref,config))
                if pools is not None:
                    candidate_sets.append({'scene_id':snapshot['scene_id'], 'candidates':pools})
            write(out/'solve_results.jsonl', results, True)
            write(out/'metrics.jsonl', evaluations, True)
            write(out/'references.jsonl', references, True)
            if candidate_sets:
                write(out/'candidate_sets.jsonl', candidate_sets, True)
            summary = {'scenes':len(inputs), 'statuses': {s:sum(r['status'] == s for r in results) for s in sorted({r['status'] for r in results})}, 'feasible_scenes':sum(bool(r['role_decisions']) and not r['constraint_violations'] for r in results), 'max_iterations_used':max((r['iterations'] for r in results),default=0), 'max_elapsed_ms':max((r['elapsed_ms'] for r in results),default=0), 'weighted_mos_gain':sum(m.get('weighted_mos_gain',0) for m in evaluations)}
            write(out/'summary.json', summary)
            print(json.dumps(summary,ensure_ascii=False,indent=2))
            return 0 if summary['feasible_scenes'] == len(inputs) else 3
        print(f'生成完成：{len(inputs)} 个场景，输出 {out}')
        return 0
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
