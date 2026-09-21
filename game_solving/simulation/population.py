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


def allocate_with_minimums(n, probs, minimums, rng):
    """Allocate exactly n labels while reserving explicit coverage floors."""
    reserved = [key for key, size in minimums.items() for _ in range(size)]
    result = reserved + [
        key
        for key, size in counts(n - len(reserved), probs).items()
        for _ in range(size)
    ]
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
    non_mos_ratio = float(p.get("non_mos_fraction", 0.0))
    n_non_mos = min(n, max(0, int(round(n * non_mos_ratio)))) if non_mos_ratio > 0 else 0
    qoe_people = people[:n - n_non_mos] if n_non_mos > 0 else people
    non_mos_people = people[n - n_non_mos:] if n_non_mos > 0 else []

    if p["qoe_counts"] and qoe_people:
        size = sum(p["qoe_counts"].values())
        categories = allocate_with_minimums(
            len(qoe_people),
            {k: v / size for k, v in p["qoe_counts"].items()},
            p["minimum_qoe_counts"],
            rng_for(c["seed"], index, "qoe"),
        )
        for person, category in zip(qoe_people, categories):
            person["qoe_category"] = category
        for category, probs in p["qoe_business_mapping"].items():
            group = [person for person in qoe_people if person["qoe_category"] == category]
            mapped = allocate(len(group), probs, rng_for(c["seed"], index, "qoe_mapping", category))
            for person, business in zip(group, mapped):
                person["business"] = business

    if non_mos_people:
        non_mos_probs = p.get("non_mos_business_probs") or {"browsing": 0.5, "download": 0.5}
        mapped = allocate(len(non_mos_people), non_mos_probs, rng_for(c["seed"], index, "non_mos_mapping"))
        for person, business in zip(non_mos_people, mapped):
            person["business"] = business
            person["qoe_category"] = business
            person["app_id"] = business
    # App is a first-class grouping dimension.  Coverage floors make every
    # configured app observable when the sampled business has enough users.
    for business in sorted({person["business"] for person in people}):
        group = [person for person in people if person["business"] == business]
        apps = {
            app_id: app
            for app_id, app in c["applications"].items()
            if app["business"] == business
        }
        if not apps:
            for person in group:
                person["app_id"] = business
            continue
        probabilities = {app_id: 1 / len(apps) for app_id in apps}
        minimums = (
            {app_id: 1 for app_id in apps}
            if p["minimum_app_coverage"] and len(group) >= len(apps)
            else {}
        )
        labels = allocate_with_minimums(
            len(group), probabilities, minimums,
            rng_for(c["seed"], index, "application", business),
        )
        for person, app_id in zip(group, labels):
            person["app_id"] = app_id
    profiles = allocate(n, p["profile_probs"], rng)
    return [
        dict(person, user_id=f"user_{i:05d}", profile=profiles[i])
        for i, person in enumerate(people)
    ]
