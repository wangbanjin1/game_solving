"""Stable random streams and exact integer marginal/joint allocations."""

import random
from game_solving.infrastructure.config import digest


def rng_for(seed, *parts):
    return random.Random(int(digest([seed, *parts]), 16))


def counts(n, probs):
    raw = {k: n * v for k, v in probs.items()}
    result = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: (-(raw[k] - result[k]), k))[
        : n - sum(result.values())
    ]:
        result[k] += 1
    return result


def allocate(n, probs, rng):
    result = [k for k, size in counts(n, probs).items() for _ in range(size)]
    rng.shuffle(result)
    return result


def generate_people(c, index):
    p = c["population"]
    n = p["users_per_cell"]
    rng = rng_for(c["seed"], index, "population")
    if p["distribution_mode"] == "joint":
        rows = p["joint_rows"]
        choices = allocate(
            n, {str(i): r["probability"] for i, r in enumerate(rows)}, rng
        )
        people = [
            {k: v for k, v in rows[int(choice)].items() if k != "probability"}
            for choice in choices
        ]
    else:
        columns = {
            key: allocate(n, p[key + "_probs"], rng)
            for key in ("package", "business", "position", "tolerance")
        }
        people = [{key: values[i] for key, values in columns.items()} for i in range(n)]
        for package, probs in p["business_probs_by_package"].items():
            group = [person for person in people if person["package"] == package]
            businesses = allocate(len(group), probs, rng_for(c["seed"], index, "business", package))
            for person, business in zip(group, businesses):
                person["business"] = business
    if p["qoe_counts"]:
        size = sum(p["qoe_counts"].values())
        categories = allocate(n, {k: v / size for k, v in p["qoe_counts"].items()}, rng_for(c["seed"], index, "qoe"))
        for person, category in zip(people, categories):
            person["qoe_category"] = category
        for category, probs in p["qoe_business_mapping"].items():
            group = [person for person in people if person["qoe_category"] == category]
            mapped = allocate(len(group), probs, rng_for(c["seed"], index, "qoe_mapping", category))
            for person, business in zip(group, mapped):
                person["business"] = business
    profiles = allocate(n, p["profile_probs"], rng)
    return [
        dict(person, user_id=f"user_{i:05d}", profile=profiles[i])
        for i, person in enumerate(people)
    ]
