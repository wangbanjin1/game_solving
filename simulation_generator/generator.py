import copy
import hashlib
import json
import math
from collections import Counter, defaultdict
from experience_solver.bandwidth_model import BandwidthModel
from experience_solver.mos_model import MosModel
from experience_solver.utility import weight
from experience_solver.candidates import CandidateBuilder
from experience_solver.schemas import validate_snapshot, available
from .population import population, rng_for, allocate
from .range_catalog import source_valid


def band(mos, target, config):
    if mos < target-config['mos']['delta_severe']:
        return 'severe'
    if mos < target:
        return 'unmet'
    if mos < target+config['mos']['delta_over']:
        return 'met'
    return 'over'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


class DatasetGenerator:
    def __init__(self, config):
        self.c = config
        self.bw, self.mos = BandwidthModel(config), MosModel(config)
        self.pools = {}
        self.preflight = []

    def pool(self, business, profile, phase):
        key = business, profile, phase
        if key in self.pools:
            return self.pools[key]
        c, points = self.c, []
        lo, hi = c['generation']['margin_range_mbps']
        env = self.bw.params(profile)
        for resolution, rate in c['media_profiles'][business]:
            media = {'resolution': resolution, 'bitrate_kbps': rate}
            for i in range(c['generation']['pool_points']):
                x = lo*(hi/lo)**(i/(c['generation']['pool_points']-1))
                b = (rate/1000+env['audio_mbps']+x)*(1+env['overhead_ratio'])/env['efficiency']
                if b > c['policy']['bandwidth_max_mbps'] or b < c['policy']['contract_protected_mbps']:
                    continue
                try:
                    k = self.bw.forward(b, media, profile)
                    if not source_valid(business, k):
                        continue
                    m = self.mos.evaluate(k, c['mos']['mapping'][business], phase)
                    points.append((b, media, k, m))
                except ValueError:
                    continue
        self.pools[key] = points
        self.preflight.append({'business_id': business, 'profile_id': profile, 'stream_phase': phase,
                               'candidate_count': len(points), 'min_mos': min((x[3] for x in points), default=None),
                               'max_mos': max((x[3] for x in points), default=None),
                               'bands_by_package': {package: dict(Counter(band(x[3],target,c) for x in points)) for package,target in c['mos']['target_by_package'].items()},
                               'claim': 'finite_search_evidence_only'})
        return points

    def initialize(self, index):
        c = self.c
        roles = population(c, index)
        rng = rng_for(c['seed'], index, 'profiles')
        profiles = allocate(len(roles), c['policy']['profile_probs'], rng)
        for r, profile in zip(roles, profiles):
            key = r['business_id'] in c['mos']['mapping']
            r.update({'is_key_business': key, 'current_profile_id': profile, 'stream_phase': c['generation']['stream_phase'],
                      'formula_profile': c['mos']['mapping'].get(r['business_id']), 'sample_count': 1, 'inclusion_probability': 1.,
                      'mos_target': c['mos']['target_by_package'][r['package']] if key else None,
                      'mos_baseline': c['mos']['baseline_by_package'][r['package']] if key else None,
                      'mos_history': None, 'bandwidth_max_mbps': c['policy']['bandwidth_max_mbps'],
                      'contract_protected_mbps': c['policy']['contract_protected_mbps'], 'allow_soft_degrade': c['policy']['allow_soft_degrade'],
                      'media_control': c['policy']['media_control'], 'intent_version': 1})
            r['weight_raw'] = weight(r, c)
        groups = defaultdict(list)
        for r in roles:
            if r['is_key_business']:
                groups[(r['package'], r['business_id'])].append(r)
        for key, group in groups.items():
            requested = allocate(len(group), c['mos']['compliance_probs'], rng_for(c['seed'],index,'quota',*key)) if c['generation']['quota_mode'] == 'specified' else [None]*len(group)
            for r, req in zip(group, requested):
                r['requested_mos_band'] = req
        return roles

    def realize_role(self, role, index, attempt):
        c, r = self.c, copy.deepcopy(role)
        if not r['is_key_business']:
            b = max(c['policy']['non_key_floor_mbps'][r['business_id']], r['contract_protected_mbps'])
            if b > r['bandwidth_max_mbps']:
                raise ValueError('NON_KEY_FLOOR_EXCEEDS_MAX')
            r.update({'qos_floor_mbps': b, 'current_bandwidth_mbps': b, 'kqi_observed': {}, 'mos_observed': None})
            return r, {'kqi_final': {}, 'mos_computed': None, 'requested_mos_band': None, 'realized_mos_band': None, 'quota_status': 'not_applicable'}
        pool = self.pool(r['business_id'], r['current_profile_id'], r['stream_phase'])
        req = r['requested_mos_band']
        matching = [x for x in pool if req is None or band(x[3],r['mos_target'],c) == req]
        if not matching:
            if c['generation']['strict_quotas'] or not pool:
                raise ValueError(f'MOS_BAND_UNREACHABLE_OR_LOW_ACCEPTANCE: current search found no candidate for {r["business_id"]}/{r["package"]}/{req}')
            matching = pool
        rng = rng_for(c['seed'], index, attempt, 'kqi', r['role_id'])
        for tries in range(c['generation']['role_max_attempts']):
            b, media, _, _ = rng.choice(matching)
            b = max(b*(1+rng.uniform(-c['generation']['jitter_fraction'], c['generation']['jitter_fraction'])), r['contract_protected_mbps'])
            try:
                proposal = self.bw.forward(b, media, r['current_profile_id'])
                b = self.bw.inverse(proposal, r['current_profile_id'], {'stream_phase': r['stream_phase']})
                final = self.bw.forward(b, media, r['current_profile_id'])
                m = self.mos.evaluate(final, r['formula_profile'], r['stream_phase'])
            except ValueError:
                continue
            realized = band(m, r['mos_target'], c)
            if b > r['bandwidth_max_mbps'] or not source_valid(r['business_id'], final) or (req is not None and realized != req and c['generation']['strict_quotas']):
                continue
            observed = dict(final)
            if r['business_id'] == 'game':
                observed['jitter_ms'] = rng.uniform(0,50)
            r.update({'current_bandwidth_mbps': b, 'kqi_observed': observed, 'mos_observed': m,
                      'mos_observation_source': 'synthetic_model', 'quality_flags': ['identity_demo', 'aggregation_demo', 'resolution_assumed'],
                      'latency_value_ms': final['rtt_ms'], 'latency_semantics': 'interaction' if r['business_id'] in ('game','cloudgame','openlive') else 'end_to_end',
                      'rtt_proxy_ms': final['rtt_ms'], 'proxy_mapping': 'identity_demo', 'unused_mos_metrics': ['jitter_ms'] if r['business_id'] == 'game' else []})
            r.pop('requested_mos_band', None)
            audit = {'kqi_proposal': proposal, 'kqi_final': final, 'mos_computed': m, 'requested_mos_band': req,
                     'realized_mos_band': realized, 'quota_status': 'met' if req is None or realized == req else 'gap', 'proposal_attempts': tries+1}
            return r, audit
        raise ValueError('MOS_BAND_UNREACHABLE_OR_LOW_ACCEPTANCE: retry budget exhausted')

    def generate(self):
        c, scenes, inputs, labels, failures = self.c, [], [], [], []
        builder = CandidateBuilder(c)
        for i in range(c['num_scenes']):
            base = self.initialize(i)
            reason = None
            for attempt in range(c['generation']['scene_max_attempts']):
                try:
                    realized = [self.realize_role(r, i, attempt) for r in base]
                    roles = [r for r, audit in realized]
                    current = sum(r['current_bandwidth_mbps'] for r in roles)
                    cell = c['cell']
                    if cell['capacity_mode'] == 'derived':
                        rho = rng_for(c['seed'],i,attempt,'capacity').uniform(*cell['allocation_ratio_range'])
                        total = current/rho+cell['unmanaged_mbps']+cell['safety_mbps']
                    else:
                        total = cell['capacity_mbps']
                    if current > total-cell['unmanaged_mbps']-cell['safety_mbps']+1e-9:
                        raise ValueError('FIXED_CAPACITY_CONFLICT')
                    sid = f'scene_{i:06d}'
                    versions = {'mos_single_score': 'user_formula_v1', 'mos_aggregation': c['mos']['aggregation'], 'bandwidth': c['bandwidth']['model'], 'weight': 'policy_demo_v1', 'range_catalog': 'user_ranges_20260909_v1'}
                    snapshot = {'schema_version': '1.0', 'scene_id': sid, 'snapshot_id': sid, 'cell_id': f'cell_{i:06d}',
                                'unit_profile': c['mos']['unit_profile'], 'direction': cell['direction'], 'control_epoch': 0,
                                'bandwidth_total_mbps': total, 'unmanaged_mbps': cell['unmanaged_mbps'], 'safety_mbps': cell['safety_mbps'],
                                'roles': roles, 'model_versions': versions, 'config_version': digest(c), 'source': 'synthetic'}
                    validate_snapshot(snapshot, c)
                    private_roles = []
                    target_sum, target_unknown = 0., []
                    for r,audit in realized:
                        pool = builder.build(r, snapshot, prune=False)
                        targets = [a['bandwidth_mbps'] for a in pool if a['hard_constraints_satisfied'] and (not r['is_key_business'] or a['predicted_mos'] >= r['mos_target'])]
                        target = min(targets) if targets else None
                        if target is None:
                            target_unknown.append(r['role_id'])
                        else:
                            target_sum += target
                        private_roles.append({**r, **audit, 'target_bandwidth_mbps': target, 'w_eval': r['weight_raw'], 'tolerance_true': r['tolerance_observed'], 'position_true': r['position_observed']})
                    scene = {**snapshot, 'seed': c['seed'], 'split': ('train','validation','test')[i%3], 'suite': 'closed_loop',
                             'roles': private_roles, 'generation_status': 'validated', 'capacity_mode': cell['capacity_mode']}
                    scenes.append(scene)
                    inputs.append(snapshot)
                    labels.append({'scene_id': sid, 'current_feasible': True, 'source_range_valid': True, 'model_roundtrip_valid': True,
                                   'baseline_feasible_status': 'unknown', 'best_known_target_requirement': None if target_unknown else target_sum,
                                   'target_unreachable_roles': target_unknown, 'allocation_ratio': current/available(snapshot),
                                   'baseline_pressure': None if target_unknown else target_sum/available(snapshot),
                                   'reference_status': 'not_computed', 'evaluation_environment': 'matched_model'})
                    break
                except ValueError as exc:
                    reason = str(exc)
            else:
                failures.append({'scene_id': f'scene_{i:06d}', 'attempts': c['generation']['scene_max_attempts'], 'reason': reason})
        distribution = Counter(r['realized_mos_band'] for s in scenes for r in s['roles'] if r['is_key_business'])
        report = {'requested': c['num_scenes'], 'successful': len(scenes), 'failed': len(failures), 'failures': failures,
                  'realized_mos_counts': dict(distribution), 'quota_gaps': sum(r['quota_status'] == 'gap' for s in scenes for r in s['roles']),
                  'quota_mode': c['generation']['quota_mode'], 'preflight': self.preflight,
                  'population_model': 'independent_marginals_with_vip_override', 'sampling': 'full_population',
                  'duplicate_state_fraction': 1-len({digest(r['kqi_final']) for s in scenes for r in s['roles'] if r['is_key_business']})/max(1,sum(r['is_key_business'] for s in scenes for r in s['roles']))}
        return scenes, inputs, labels, report
