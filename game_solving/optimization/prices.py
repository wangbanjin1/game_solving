"""Capacity-normalized, damped dual prices; legacy mode remains reproducible."""
import math


def update_prices(prices, demand, capacity, iteration, previous_signal, config):
    cfg = config["solver"]
    signal, updated = [], []
    for j, direction in enumerate(("ul", "dl")):
        excess = getattr(demand, direction) - getattr(capacity, direction)
        if cfg["price_update"] == "legacy":
            value = excess / config["utility"]["bandwidth_reference_kbps"]
            step = cfg["gamma0"] / math.sqrt(iteration + 1) * value
        else:
            residual = excess / max(getattr(capacity, direction), 1.0)
            residual = 0.0 if abs(residual) <= cfg["price_deadband"] else residual
            alpha = cfg["price_smoothing"]
            value = alpha * residual + (1 - alpha) * previous_signal[j]
            step = cfg["gamma0"] / math.sqrt(iteration + 1) * value
            step = min(cfg["price_max_step"], max(-cfg["price_max_step"], step))
            if cfg["price_update"] == "monotone":
                step = max(0.0, step)
        signal.append(value)
        updated.append(max(0.0, prices[j] + step))
    return updated, signal
