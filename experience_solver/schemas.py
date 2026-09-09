from .mos_model import number, MosModel


def multiplier(role):
    n = number(role.get('sample_count', 1), 'sample_count', 1)
    if int(n) != n:
        raise ValueError('INTEGER_SAMPLE_COUNT_REQUIRED')
    p = number(role.get('inclusion_probability', 1), 'inclusion_probability', hi=1)
    if p == 0:
        raise ValueError('ZERO_INCLUSION_PROBABILITY')
    return n/p


def available(snapshot):
    return snapshot['bandwidth_total_mbps']-snapshot['unmanaged_mbps']-snapshot['safety_mbps']


def validate_snapshot(s, c):
    if s.get('unit_profile') != c['mos']['unit_profile'] or s.get('schema_version') != '1.0':
        raise ValueError('UNSUPPORTED_UNITS_OR_SCHEMA')
    for key in ('bandwidth_total_mbps','unmanaged_mbps','safety_mbps'):
        number(s[key], key)
    if available(s) < 0:
        raise ValueError('INVALID_CAPACITY_INPUT')
    ids = set()
    if not s['roles']:
        raise ValueError('EMPTY_ROLES')
    for r in s['roles']:
        if r['role_id'] in ids:
            raise ValueError('DUPLICATE_ROLE_ID')
        ids.add(r['role_id'])
        multiplier(r)
        if r['current_profile_id'] not in c['profiles']:
            raise ValueError('UNKNOWN_PROFILE')
        for key in ('current_bandwidth_mbps','bandwidth_max_mbps','contract_protected_mbps','weight_raw'):
            number(r[key], key)
        if r.get('stream_phase') not in ('initial','steady'):
            raise ValueError('INVALID_STREAM_PHASE')
        for key in ('user_bandwidth_caps_mbps','flow_bandwidth_caps_mbps'):
            for value in s.get(key, {}).values():
                number(value, key)
        if r['is_key_business']:
            if r['business_id'] not in c['mos']['mapping'] or r['formula_profile'] != c['mos']['mapping'][r['business_id']]:
                raise ValueError('INVALID_BUSINESS_FORMULA_MAPPING')
            if r['kqi_observed'].get('bitrate_kbps', 0) <= 0:
                raise ValueError('UNSERVED_INPUT')
            MosModel(c).evaluate(r['kqi_observed'],r['formula_profile'],r['stream_phase'])
            for key in ('mos_observed','mos_baseline','mos_target'):
                number(r[key], key, 1, 5)
            if r.get('mos_history') is not None:
                number(r['mos_history'], 'mos_history', 1, 5)


def violations(actions, roles, snapshot, tolerance=1e-6):
    errors = []
    if len(actions) != len(roles):
        return ['MISSING_DECISIONS']
    total = sum(multiplier(r)*a['bandwidth_mbps'] for r, a in zip(roles, actions))
    if total > available(snapshot)+tolerance:
        errors.append('CELL_CAPACITY_EXCEEDED')
    users, flows = {}, {}
    for r, a in zip(roles, actions):
        b = a['bandwidth_mbps']
        if a['role_id'] != r['role_id'] or not a['hard_constraints_satisfied'] or b > r['bandwidth_max_mbps']+tolerance or b < r['contract_protected_mbps']-tolerance:
            errors.append('ROLE_HARD_CONSTRAINT:'+r['role_id'])
        users[r['user_id']] = users.get(r['user_id'], 0)+b*multiplier(r)
        if r.get('flow_id'):
            flows[r['flow_id']] = flows.get(r['flow_id'], 0)+b*multiplier(r)
    for key, val in users.items():
        if val > snapshot.get('user_bandwidth_caps_mbps', {}).get(key, float('inf'))+tolerance:
            errors.append('USER_CAP:'+key)
    for key, val in flows.items():
        if val > snapshot.get('flow_bandwidth_caps_mbps', {}).get(key, float('inf'))+tolerance:
            errors.append('FLOW_CAP:'+key)
    return errors
