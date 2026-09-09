import hashlib
import json
from .bandwidth_model import BandwidthModel
from .mos_model import MosModel
from .utility import utility
from simulation_generator.range_catalog import source_valid


def level(mos):
    return None if mos is None else 'L'+str(sum(mos >= x for x in (2.5,3.5,4,4.5)))


class CandidateBuilder:
    def __init__(self, config):
        self.c = config
        self.bw = BandwidthModel(config)
        self.mos = MosModel(config)
        self.truncated = False

    def action(self, role, b, media, profile, anchor=False):
        if b < role['contract_protected_mbps']-1e-9 or b > role['bandwidth_max_mbps']+1e-9:
            return None
        if role['is_key_business']:
            try:
                kqi = self.bw.forward(b, media, profile)
                if not source_valid(role['business_id'], kqi):
                    return None
                mos = self.mos.evaluate(kqi, role['formula_profile'], role['stream_phase'])
            except ValueError:
                return None
            baseline = mos+1e-9 >= role['mos_baseline']
            hard = baseline or role.get('allow_soft_degrade', False)
        else:
            kqi, mos = {}, None
            baseline = b+1e-9 >= role['qos_floor_mbps']
            hard = baseline
        h, stability, switch = utility(role, mos, profile, self.c)
        payload = [profile, media, round(b, 10)]
        action_id = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:20]
        return {'role_id': role['role_id'], 'action_id': action_id, 'profile_id': profile,
                'level_id': level(mos), 'theta_target': kqi, 'media_profile_id': json.dumps(media, sort_keys=True),
                'predicted_kqi': kqi, 'predicted_mos': mos, 'bandwidth_mbps': b,
                'baseline_satisfied': baseline, 'hard_constraints_satisfied': hard,
                'stability_cost': stability, 'switch_cost': switch, 'H': h, 'U': None,
                'executable': False, 'model_confidence': 'synthetic_uncalibrated',
                'model_version': self.c['bandwidth']['model'], 'rejection_reasons': [], 'is_anchor': anchor}

    def build(self, role, snapshot=None, expansion_scope='full', grid=None, prune=True):
        profile = role['current_profile_id']
        media = {k: role['kqi_observed'][k] for k in ('bitrate_kbps','resolution') if k in role['kqi_observed']}
        anchor = self.action(role, role['current_bandwidth_mbps'], media, profile, True)
        if not role['is_key_business']:
            b = max(role['qos_floor_mbps'], role['contract_protected_mbps'])
            return [self.action(role, b, {}, profile)] if b <= role['bandwidth_max_mbps'] else []
        profiles = [profile] + self.c['profiles'][profile]['neighbors']
        media_list = [media]
        if role.get('media_control', False):
            media_list += [{'resolution': res, 'bitrate_kbps': rate} for res, rate in self.c['media_profiles'][role['business_id']]]
        result = {anchor['action_id']: anchor} if anchor else {}
        count = 0
        margins = grid or self.c['solver']['margin_grid_mbps']
        for p in profiles:
            env = self.bw.params(p)
            for m in media_list:
                for margin in margins:
                    count += 1
                    if count > self.c['solver']['raw_candidates_per_role']:
                        self.truncated = True
                        break
                    b0 = (m['bitrate_kbps']/1000 + env['audio_mbps'] + margin)*(1+env['overhead_ratio'])/env['efficiency']
                    b = max(b0, role['contract_protected_mbps'])
                    # Round-trip every target through the same inverse/forward adapters.
                    try:
                        target = self.bw.forward(b, m, p)
                        b = self.bw.inverse(target, p, {'stream_phase': role['stream_phase']})
                    except ValueError:
                        continue
                    a = self.action(role, b, m, p)
                    if a and a['action_id'] not in result:
                        result[a['action_id']] = a
        values = sorted(result.values(), key=lambda a: (a['bandwidth_mbps'], a['action_id']))
        if not prune:
            return values
        kept = []
        for a in values:
            dominated = any(b['profile_id'] == a['profile_id'] and b['baseline_satisfied'] == a['baseline_satisfied'] and
                            b['bandwidth_mbps'] <= a['bandwidth_mbps'] and b['predicted_mos'] >= a['predicted_mos'] and
                            b['stability_cost'] <= a['stability_cost'] and b['switch_cost'] <= a['switch_cost'] and
                            (b['bandwidth_mbps'] < a['bandwidth_mbps'] or b['predicted_mos'] > a['predicted_mos']) for b in values)
            if a['is_anchor'] or not dominated:
                kept.append(a)
        limit = self.c['solver']['kept_candidates_per_role']
        if len(kept) > limit:
            self.truncated = True
            protected = [a for a in kept if a['is_anchor']]
            for p in profiles:
                for baseline in (True, False):
                    group = [a for a in kept if a['profile_id'] == p and a['baseline_satisfied'] == baseline]
                    if group:
                        protected.append(min(group, key=lambda a: a['bandwidth_mbps']))
            unique = {a['action_id']: a for a in protected}
            for a in sorted(kept, key=lambda a: (-a['H'], a['bandwidth_mbps'], a['action_id'])):
                if len(unique) >= max(limit, len(protected)):
                    break
                unique[a['action_id']] = a
            kept = sorted(unique.values(), key=lambda a: (a['bandwidth_mbps'], a['action_id']))
        return kept
