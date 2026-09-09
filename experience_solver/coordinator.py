import time
from .schemas import multiplier, violations, available


def totals(actions, roles):
    return (sum(multiplier(r)*a['bandwidth_mbps'] for r,a in zip(roles,actions)),
            sum(multiplier(r)*a['H'] for r,a in zip(roles,actions)))


class ResourceCoordinator:
    def __init__(self, config):
        self.c = config

    def repair_and_upgrade(self, raw, pools, snapshot, deadline=float('inf')):
        roles = snapshot['roles']
        chosen = list(raw)
        eps = self.c['solver']['epsilon_gain']
        tol = self.c['solver']['numerical_tolerance_mbps']
        # Repair within users/flows as well as the cell; anchor resource class fixes protection.
        while violations(chosen, roles, snapshot, tol):
            moves = []
            errors = violations(chosen, roles, snapshot, tol)
            for i, (r, old, pool) in enumerate(zip(roles, chosen, pools)):
                if 'CELL_CAPACITY_EXCEEDED' not in errors and not any(e == 'USER_CAP:'+r['user_id'] or e == 'FLOW_CAP:'+str(r.get('flow_id')) for e in errors):
                    continue
                stage = 0 if not r['is_key_business'] else 1 if self.c['profiles'][r['current_profile_id']]['resource_type'] == 'Non-GBR' else 2
                for a in pool:
                    released = (old['bandwidth_mbps']-a['bandwidth_mbps'])*multiplier(r)
                    if released > tol:
                        loss = (old['H']-a['H'])*multiplier(r)
                        moves.append((stage, loss/released, a['action_id'], i, a))
            if not moves or time.perf_counter() >= deadline:
                return None, False
            _, _, _, i, a = min(moves, key=lambda x: x[:4])
            chosen[i] = a
        while time.perf_counter() < deadline:
            moves = []
            used = totals(chosen, roles)[0]
            user_used, flow_used = {}, {}
            for r, a in zip(roles, chosen):
                weighted = a['bandwidth_mbps']*multiplier(r)
                user_used[r['user_id']] = user_used.get(r['user_id'], 0)+weighted
                if r.get('flow_id'):
                    flow_used[r['flow_id']] = flow_used.get(r['flow_id'], 0)+weighted
            for i, (r, old, pool) in enumerate(zip(roles,chosen,pools)):
                if time.perf_counter() >= deadline:
                    return chosen, False
                for a in pool:
                    gain = (a['H']-old['H'])*multiplier(r)
                    delta = (a['bandwidth_mbps']-old['bandwidth_mbps'])*multiplier(r)
                    if gain <= eps:
                        continue
                    fits = used+delta <= available(snapshot)+tol
                    fits = fits and user_used[r['user_id']]+delta <= snapshot.get('user_bandwidth_caps_mbps', {}).get(r['user_id'],float('inf'))+tol
                    if r.get('flow_id'):
                        fits = fits and flow_used[r['flow_id']]+delta <= snapshot.get('flow_bandwidth_caps_mbps', {}).get(r['flow_id'],float('inf'))+tol
                    if fits:
                        moves.append((float('inf') if delta <= tol else gain/delta, gain, a['action_id'], i, a))
            if not moves:
                break
            _, _, _, i, a = max(moves, key=lambda x: (x[0],x[1],x[2]))
            chosen[i] = a
        # Bounded pair exchanges use the same donor protection stages.
        limit = self.c['solver']['exchange_pairs_per_iteration']
        downgrades, upgrades = [], []
        for i, (r, old, pool) in enumerate(zip(roles, chosen, pools)):
            stage = 0 if not r['is_key_business'] else 1 if self.c['profiles'][r['current_profile_id']]['resource_type'] == 'Non-GBR' else 2
            for a in pool:
                db = (a['bandwidth_mbps']-old['bandwidth_mbps'])*multiplier(r)
                dh = (a['H']-old['H'])*multiplier(r)
                if db < -tol:
                    downgrades.append((stage, -dh/-db, i, a, db, dh))
                elif db > tol and dh > eps:
                    upgrades.append((-dh/db, i, a, db, dh))
        downgrades.sort(key=lambda x:(x[0],x[1],x[2],x[3]['action_id']))
        upgrades.sort(key=lambda x:(x[0],x[1],x[2]['action_id']))
        evaluated, best, best_gain = 0, None, eps
        for stage, _, i, down, db, dh in downgrades:
            for _, j, up, ub, uh in upgrades:
                if i == j:
                    continue
                if evaluated >= limit or time.perf_counter() >= deadline:
                    break
                evaluated += 1
                if dh+uh <= best_gain:
                    continue
                trial = chosen[:]
                trial[i], trial[j] = down, up
                if not violations(trial, roles, snapshot, tol):
                    best, best_gain = trial, dh+uh
            if best is not None or evaluated >= limit:
                break
        if best is not None:
            chosen = best
        return chosen, best is None and time.perf_counter() < deadline
