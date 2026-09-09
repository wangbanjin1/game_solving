import hashlib
import random


def rng_for(seed, *parts):
    digest = hashlib.sha256('/'.join(map(str, (seed, *parts))).encode()).digest()
    return random.Random(int.from_bytes(digest[:16], 'big'))


def counts(n, probabilities):
    raw = {k: n*v for k, v in probabilities.items()}
    result = {k: int(v) for k, v in raw.items()}
    for key in sorted(raw, key=lambda k: (-(raw[k]-result[k]), k))[:n-sum(result.values())]:
        result[key] += 1
    return result


def allocate(n, probabilities, rng):
    values = [k for k, size in counts(n, probabilities).items() for _ in range(size)]
    rng.shuffle(values)
    return values


def population(c, scene_index):
    p = c['population']
    n = p['users_per_cell']
    rng = rng_for(c['seed'], scene_index, 'population')
    packages = allocate(n, p['package_probs'], rng)
    businesses = allocate(n, p['business_probs'], rng)
    positions = allocate(n, p['position_probs'], rng)
    tolerances = iter(allocate(packages.count('normal'), p['normal_tolerance_probs'], rng))
    return [{'user_id': f'user_{i:05d}', 'role_id': f'user_{i:05d}_{business}', 'business_id': business,
             'package': package, 'position_observed': position,
             'tolerance_observed': next(tolerances) if package == 'normal' else 'intolerant'}
            for i, (package, business, position) in enumerate(zip(packages, businesses, positions))]
