"""Single JSON configuration; unknown options fail instead of being ignored."""
import copy
import json
from pathlib import Path
from .mos_model import number

DEFAULT_PATH = Path(__file__).resolve().parents[1] / 'configs' / 'default.json'


def merge(base, override, path=''):
    for key, value in override.items():
        if key not in base:
            raise ValueError(f'UNKNOWN_CONFIG_KEY: {path}{key}')
        # Probability/media/policy maps may replace the full table.
        if isinstance(value, dict) and isinstance(base[key], dict) and key not in ('package_probs', 'business_probs', 'compliance_probs', 'target_by_package', 'baseline_by_package', 'profile_probs', 'environment'):
            merge(base[key], value, path+key+'.')
        else:
            base[key] = copy.deepcopy(value)
    return base


def probabilities(table, name):
    if not table:
        raise ValueError(f'EMPTY_PROBABILITIES: {name}')
    for v in table.values():
        number(v, name, hi=1)
    if abs(sum(table.values()) - 1) > 1e-9:
        raise ValueError(f'INVALID_PROBABILITY_SUM: {name}')


def validate(c):
    for section in ('population', 'mos', 'policy'):
        for key, table in c[section].items():
            if key.endswith('_probs'):
                probabilities(table, key)
    for section, keys in [('solver', ['max_iterations','raw_candidates_per_role','kept_candidates_per_role','stable_rounds']), ('generation', ['pool_points','scene_max_attempts','role_max_attempts']), ('population', ['users_per_cell'])]:
        for key in keys:
            if type(c[section][key]) is not int or c[section][key] < 1:
                raise ValueError(f'POSITIVE_INTEGER_REQUIRED: {section}.{key}')
    if type(c['num_scenes']) is not int or c['num_scenes'] < 1:
        raise ValueError('INVALID_SCENE_COUNT')
    if c['generation']['pool_points'] < 2:
        raise ValueError('POOL_REQUIRES_TWO_POINTS')
    number(c['solver']['time_budget_ms'], 'time_budget_ms')
    for key in ('lambda_initial','gamma0','epsilon_strategy','epsilon_resource_relative','epsilon_utility_relative','epsilon_gain','numerical_tolerance_mbps'):
        number(c['solver'][key], 'solver.'+key)
    if type(c['solver']['exchange_pairs_per_iteration']) is not int or c['solver']['exchange_pairs_per_iteration'] < 0:
        raise ValueError('NONNEGATIVE_EXCHANGE_LIMIT_REQUIRED')
    for table in (c['solver']['margin_grid_mbps'],c['reference']['margin_grid_mbps']):
        if not table or any(number(v, 'margin_grid') <= 0 for v in table):
            raise ValueError('POSITIVE_NONEMPTY_MARGIN_GRID_REQUIRED')
    number(c['generation']['jitter_fraction'], 'jitter_fraction', hi=.5)
    for key in ('eta','beta','switch_penalty','debt_cap'):
        number(c['utility'][key], key)
    for key in ('bandwidth_max_mbps','contract_protected_mbps'):
        number(c['policy'][key], key)
    if c['policy']['contract_protected_mbps'] > c['policy']['bandwidth_max_mbps']:
        raise ValueError('CONTRACT_EXCEEDS_MAX')
    for profile in c['policy']['profile_probs']:
        if profile not in c['profiles']:
            raise ValueError('UNKNOWN_PROFILE')
    if type(c['reference']['max_combinations']) is not int or c['reference']['max_combinations'] < 1:
        raise ValueError('INVALID_REFERENCE_BUDGET')
    for key in ('delta_over','delta_severe'):
        number(c['mos'][key], key)
    for business, media in c['media_profiles'].items():
        if not media:
            raise ValueError('EMPTY_MEDIA_PROFILES')
        for resolution, rate in media:
            if number(rate, 'media_bitrate') <= 0:
                raise ValueError('UNSERVED_MEDIA_NOT_ALLOWED_IN_CLOSED_LOOP')
            if resolution is not None:
                number(resolution, 'resolution')
    if c['cell']['capacity_mode'] not in ('derived', 'fixed'):
        raise ValueError('INVALID_CAPACITY_MODE')
    if c['cell']['direction'] not in ('abstract_direction','uplink','downlink'):
        raise ValueError('INVALID_DIRECTION')
    if c['cell']['direction'] != 'abstract_direction' and c['population']['business_probs'].get('openlive', 0) and len(c['population']['business_probs']) > 1:
        raise ValueError('MIXED_DIRECTION_REQUIRES_ABSTRACT_BUDGET')
    if c['generation']['quota_mode'] not in ('reachable', 'specified'):
        raise ValueError('INVALID_QUOTA_MODE')
    if c['generation']['stream_phase'] not in ('initial', 'steady'):
        raise ValueError('INVALID_STREAM_PHASE')
    if c['generation']['stream_phase'] == 'initial' and c['population']['business_probs'].get('meeting', 0):
        raise ValueError('MEETING_INITIAL_RANGE_MISSING')
    probabilities(c['mos']['weights'], 'mos.weights')
    if c['mos']['aggregation'] != 'weighted_mean_demo_v1':
        raise ValueError('UNSUPPORTED_AGGREGATION')
    for package in c['population']['package_probs']:
        for key in ('target_by_package','baseline_by_package'):
            number(c['mos'][key][package], key, 1, 5)
        if package not in c['policy']['base_weights']:
            raise ValueError('MISSING_PACKAGE_WEIGHT')
    for business in c['population']['business_probs']:
        if business not in c['mos']['mapping'] and business not in c['policy']['non_key_floor_mbps']:
            raise ValueError(f'UNKNOWN_BUSINESS: {business}')
    for profile in c['profiles']:
        if set(c['profiles'][profile]['environment']) - (set(c['bandwidth'])-{'model','calibrated'}):
            raise ValueError('UNKNOWN_PROFILE_ENVIRONMENT_PARAMETER')
        p = {**c['bandwidth'], **c['profiles'][profile]['environment']}
        for key, value in p.items():
            if key not in ('model', 'calibrated'):
                number(value, key)
        for key in ('efficiency','margin_min_mbps','rtt_coeff_ms_mbps','loss_scale_mbps','stall_scale_mbps'):
            if p[key] <= 0:
                raise ValueError(f'POSITIVE_REQUIRED: {key}')
        if p['efficiency'] > 1 or p['loss_floor'] > 1 or p['stall_floor'] > 1:
            raise ValueError('INVALID_ENVIRONMENT')
        if any(n not in c['profiles'] for n in c['profiles'][profile]['neighbors']):
            raise ValueError('UNKNOWN_PROFILE_NEIGHBOR')
    for key in ('capacity_mbps','unmanaged_mbps','safety_mbps'):
        number(c['cell'][key], key)
    lo, hi = c['cell']['allocation_ratio_range']
    if not 0 < lo <= hi <= 1:
        raise ValueError('INVALID_ALLOCATION_RATIO')
    lo, hi = c['generation']['margin_range_mbps']
    if not 0 < lo < hi:
        raise ValueError('INVALID_MARGIN_RANGE')
    for key in ('weight_reference','bandwidth_reference_mbps'):
        if number(c['utility'][key], key) <= 0:
            raise ValueError('POSITIVE_REFERENCE_REQUIRED')
    return c


def load_config(path=None):
    c = json.loads(DEFAULT_PATH.read_text(encoding='utf-8'))
    if path and Path(path).resolve() != DEFAULT_PATH:
        merge(c, json.loads(Path(path).read_text(encoding='utf-8')))
    return validate(c)
