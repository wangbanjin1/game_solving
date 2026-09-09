from .mos_model import clip


def weight(role, config):
    p = config['policy']
    business = role['business_id'] if role['business_id'] in ('browsing','download') else 'realtime'
    return p['base_weights'][role['package']][business] * p['position_factors'][role['position_observed']] * p['tolerance_factors'][role['tolerance_observed']]


def utility(role, mos, profile, config):
    if mos is None:
        return 0., 0., 0.
    p = config['utility']
    history = role.get('mos_history')
    history = role['mos_target'] if history is None else history
    debt = clip((role['mos_target']-history)/4, 0, p['debt_cap'])
    stability = abs(mos-role['mos_observed'])/4
    switch = p['switch_penalty'] if profile != role['current_profile_id'] else 0.
    h = (role['weight_raw']/p['weight_reference'] + p['eta']*debt)*(mos-1)/4 - p['beta']*stability-switch
    return h, stability, switch
