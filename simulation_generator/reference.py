"""Independent unpruned finite universe and bounded Cartesian-product oracle."""
import itertools
import math
from experience_solver.candidates import CandidateBuilder
from experience_solver.schemas import violations, multiplier
from .generator import digest


def universe(snapshot, config):
    builder = CandidateBuilder(config)
    # Union makes every tested algorithm action comparable with the independent grid.
    result = []
    for r in snapshot['roles']:
        actions = builder.build(r, snapshot, grid=config['reference']['margin_grid_mbps'], prune=False)
        actions += builder.build(r, snapshot, prune=False)
        unique = {a['action_id']: a for a in actions if a['hard_constraints_satisfied'] and a['baseline_satisfied']}
        result.append(sorted(unique.values(), key=lambda a: a['action_id']))
    return result


def exact_reference(snapshot, pools, config):
    size = math.prod(map(len,pools))
    metadata = {'reference_candidate_version': digest(pools), 'reference_constraints_hash': digest(snapshot), 'combinations': size}
    if size > config['reference']['max_combinations']:
        return {**metadata, 'reference_status': 'not_computed', 'reason': 'COMBINATION_BUDGET_EXCEEDED'}
    best_mos, best_h, best_actions = None, None, None
    roles = snapshot['roles']
    for actions in itertools.product(*pools):
        if violations(actions, roles, snapshot):
            continue
        score = sum(multiplier(r)*r['weight_raw']*a['predicted_mos'] for r,a in zip(roles,actions) if r['is_key_business'])
        h = sum(multiplier(r)*a['H'] for r,a in zip(roles,actions))
        if best_mos is None or score > best_mos:
            best_mos, best_actions = score, [a['action_id'] for a in actions]
        best_h = h if best_h is None else max(best_h,h)
    return {**metadata, 'reference_status': 'exact_discrete' if best_actions is not None else 'infeasible',
            'U_reference': best_mos, 'H_reference': best_h, 'reference_action_ids': best_actions,
            'reference_objective_type': 'weighted_mos', 'H_objective_type': 'fixed_history_stability'}


def metrics(snapshot, result, reference, config):
    roles, decisions = snapshot['roles'], result['role_decisions']
    out = {'snapshot_id': snapshot['snapshot_id'], 'WGR_exact': None, 'WGR_reason': 'reference_not_exact',
           'evaluation_environment': 'matched_model', 'N_improved': 0, 'N_worsened': 0, 'N_unchanged': 0,
           'newly_met': 0, 'lost_met': 0}
    if not decisions or violations(decisions, roles, snapshot):
        return {**out, 'WGR_reason': 'algorithm_infeasible'}
    before = after = 0.
    for r,a in zip(roles,decisions):
        if not r['is_key_business']:
            continue
        n, m0, m1 = multiplier(r), r['mos_observed'], a['predicted_mos']
        before += n*r['weight_raw']*m0
        after += n*r['weight_raw']*m1
        delta = m1-m0
        out['N_improved' if delta > config['metrics']['epsilon_mos'] else 'N_worsened' if delta < -config['metrics']['epsilon_mos'] else 'N_unchanged'] += n
        out['newly_met'] += n*(m0 < r['mos_target'] <= m1)
        out['lost_met'] += n*(m1 < r['mos_target'] <= m0)
    out.update({'U_current': before, 'U_algorithm': after, 'weighted_mos_gain': after-before, 'net_met': out['newly_met']-out['lost_met']})
    if reference.get('reference_status') == 'exact_discrete':
        if any(r['is_key_business'] and r['mos_observed'] < r['mos_baseline'] for r in roles):
            out['WGR_reason'] = 'invalid_baseline'
        elif reference['U_reference']-before <= config['metrics']['denominator_epsilon']:
            out['WGR_reason'] = 'no_valid_improvement_denominator'
        else:
            out['WGR_exact'] = (after-before)/(reference['U_reference']-before)
            out['WGR_reason'] = None
            if out['WGR_exact'] > 1+1e-6:
                raise ValueError('WGR_COMPARABILITY_FAILURE')
    return out
