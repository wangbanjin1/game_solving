import math
import time
from .schemas import validate_snapshot, available, violations, multiplier
from .candidates import CandidateBuilder
from .coordinator import ResourceCoordinator, totals


class Solver:
    def __init__(self, config):
        self.c = config

    def solve(self, snapshot, previous_solver_state=None, candidate_universe=None):
        start = time.perf_counter()
        c, roles = self.c, snapshot['roles']
        p = c['solver']
        validate_snapshot(snapshot, c)
        budget = available(snapshot)
        deadline = start+p['time_budget_ms']/1000
        builder = CandidateBuilder(c)
        # A validated floor is required even if initialization consumes the soft time budget.
        full = candidate_universe if candidate_universe is not None else [builder.build(r, snapshot) for r in roles]
        normal = [[a for a in pool if a['hard_constraints_satisfied'] and a['baseline_satisfied']] for pool in full]
        soft = [[a for a in pool if a['hard_constraints_satisfied']] for pool in full]
        floor = lambda pools: [min(pool, key=lambda a:(a['bandwidth_mbps'], -a['H'], a['action_id'])) for pool in pools] if all(pools) else None
        pools = normal
        best = floor(pools)
        degraded = False
        if best is None or violations(best, roles, snapshot):
            pools, degraded = soft, True
            best = floor(pools)
        price = p['lambda_initial']
        if previous_solver_state and previous_solver_state.get('config_version') == snapshot.get('config_version'):
            price = max(0., float(previous_solver_state['lambda_final']))
        trace, best_iteration = [], 0
        if best is None or violations(best, roles, snapshot):
            required = totals(best, roles)[0] if best else None
            return {'snapshot_id': snapshot['snapshot_id'], 'status': 'INFEASIBLE_HARD_FLOOR', 'role_decisions': [],
                    'available_mbps': budget, 'required_floor_mbps': required,
                    'capacity_gap_mbps': max(0., required-budget) if required is not None else None,
                    'constraint_violations': violations(best, roles, snapshot) if best else ['NO_HARD_FEASIBLE_CANDIDATE'],
                    'affected_roles': [r['role_id'] for r,pool in zip(roles,pools) if not pool],
                    'iterations': 0, 'trace': [], 'executable': False, 'elapsed_ms': (time.perf_counter()-start)*1000}
        coord = ResourceCoordinator(c)
        previous, seen, stable = None, set(), 0
        status = 'MAX_ITERATIONS'
        raw_b = totals(best, roles)[0]
        metrics = {}
        for k in range(p['max_iterations']):
            if time.perf_counter() >= deadline:
                status = 'TIME_BUDGET'
                break
            scopes = pools
            if k == 0:
                scopes = []
                for r, pool in zip(roles,pools):
                    near = sorted([a for a in pool if a['profile_id'] == r['current_profile_id']], key=lambda a: abs(a['bandwidth_mbps']-r['current_bandwidth_mbps']))[:3]
                    # Baseline repair may leave the current MOS level.
                    scopes.append(near or pool)
            raw = [max(pool, key=lambda a: (a['H']-price*a['bandwidth_mbps']/c['utility']['bandwidth_reference_mbps'], a['is_anchor'], a['profile_id'] == r['current_profile_id'], -a['switch_cost'], -a['bandwidth_mbps'], a['action_id'])) for r,pool in zip(roles,scopes)]
            raw_b = totals(raw, roles)[0]
            repaired, local = coord.repair_and_upgrade(raw, scopes, snapshot, deadline)
            if repaired is None:
                repaired, local = best, False
            if violations(repaired, roles, snapshot):
                raise AssertionError('INTERNAL_INFEASIBLE_REPAIR')
            b, h = totals(repaired, roles)
            # In degraded mode first minimize protected target violations.
            def rank(actions):
                losses = tuple(-sum(multiplier(r) for r,a in zip(roles,actions) if r['package'] == package and not a['baseline_satisfied']) for package in ('super_vip','vip','normal')) if degraded else ()
                return (*losses, totals(actions, roles)[1])
            if rank(repaired) > rank(best):
                best, best_iteration = repaired[:], k+1
            next_price = max(0., price+p['gamma0']/math.sqrt(k+1)*(raw_b-budget)/c['utility']['bandwidth_reference_mbps'])
            vector = tuple(a['action_id'] for a in repaired)
            if previous:
                old, old_b, old_h = previous
                metrics = {'strategy_change': sum(x != y for x,y in zip(vector,old))/len(roles), 'resource_change': abs(b-old_b)/max(budget,1), 'utility_change': abs(h-old_h)/max(abs(old_h),1)}
                stable = stable+1 if metrics['strategy_change'] <= p['epsilon_strategy'] and metrics['resource_change'] <= p['epsilon_resource_relative'] and metrics['utility_change'] <= p['epsilon_utility_relative'] and local and k > 0 else 0
            trace.append({'iteration': k+1, 'raw_demand_mbps': raw_b, 'allocated_mbps': b, 'objective_H': h, 'lambda': price, 'lambda_next': next_price, 'scope': 'anchor_neighborhood' if k == 0 else 'expanded', **metrics})
            price = next_price
            if time.perf_counter() >= deadline:
                status = 'TIME_BUDGET'
                break
            if stable >= p['stable_rounds']:
                status = 'CONVERGED_LOCAL'
                break
            if vector in seen and previous and vector != previous[0]:
                status = 'STOPPED_CYCLE'
                break
            seen.add(vector)
            previous = vector, b, h
        b, h = totals(best, roles)
        decisions = [{**a, 'U': a['H']-price*a['bandwidth_mbps']/c['utility']['bandwidth_reference_mbps'], 'population_weight': multiplier(r), 'old_profile_id': r['current_profile_id'], 'old_bandwidth_mbps': r['current_bandwidth_mbps'], 'target_gap': max(0.,r['mos_target']-a['predicted_mos']) if r['is_key_business'] else None,
                      'reason': 'best_known_feasible_candidate'} for r,a in zip(roles,best)]
        return {'solve_id': snapshot['snapshot_id']+'_solve', 'snapshot_id': snapshot['snapshot_id'], 'status': status, 'mode': 'DEGRADED' if degraded else 'NORMAL',
                'best_iteration': best_iteration, 'iterations': len(trace), 'elapsed_ms': (time.perf_counter()-start)*1000,
                'raw_demand_mbps': raw_b, 'allocated_mbps': b, 'available_mbps': budget, 'lambda_final': price,
                'objective_H_total': h, 'constraint_violations': violations(best, roles, snapshot), 'unmet_targets': [r['role_id'] for r,a in zip(roles,best) if r['is_key_business'] and a['predicted_mos'] < r['mos_target']],
                'candidate_truncated': builder.truncated, 'convergence_metrics': metrics, 'role_decisions': decisions,
                'trace': trace, 'executable': False, 'model_versions': snapshot.get('model_versions', {}), 'config_version': snapshot.get('config_version')}
