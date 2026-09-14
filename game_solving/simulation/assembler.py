"""Iterative bounded backtracking; independent minima are pruning bounds only."""

from game_solving.domain.entities import Bandwidth


class AssemblyFailure(Exception):
    pass


def assemble(pools, capacity, max_nodes, budget):
    order = sorted(range(len(pools)), key=lambda i: (len(pools[i]), i))
    ordered = [pools[i] for i in order]
    n = len(ordered)
    suffix = [Bandwidth() for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        suffix[i] = suffix[i + 1] + Bandwidth(
            min(x[0].current.ul for x in ordered[i]),
            min(x[0].current.dl for x in ordered[i]),
        )
    chosen = [None] * n
    indices = [0] * n
    prefix = [Bandwidth() for _ in range(n + 1)]
    depth = nodes = 0
    while depth >= 0:
        if depth == n:
            restored = [None] * n
            for i, index in enumerate(order):
                restored[index] = chosen[i]
            return restored, nodes
        if indices[depth] >= len(ordered[depth]):
            indices[depth] = 0
            depth -= 1
            continue
        if nodes >= max_nodes:
            raise AssemblyFailure("CAPACITY_QUOTA_SEARCH_EXHAUSTED")
        budget.consume(kind="assembly")
        nodes += 1
        candidate = ordered[depth][indices[depth]]
        indices[depth] += 1
        used = prefix[depth] + candidate[0].current
        if not (used + suffix[depth + 1]).fits(capacity):
            continue
        chosen[depth] = candidate
        prefix[depth + 1] = used
        depth += 1
    raise AssemblyFailure("FINITE_CURRENT_STATE_POOLS_INFEASIBLE")
