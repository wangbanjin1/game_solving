"""User single-score formulas and explicitly provisional Q/I/V aggregation."""
import math


def number(value, name, lo=0, hi=math.inf):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f"INVALID_VALUE: {name}={value!r}")
    return value


def clip(x, lo=1., hi=5.):
    return max(lo, min(hi, x))


def bitrate_score(x):
    return 5 / (1 + math.exp(-number(x, 'bitrate_kbps') / 928.984))


def resolution_score(x):
    return 5 / (1 + math.exp(-number(x, 'resolution') / 410))


def rtt_score(x):
    return 4 * math.exp(-.0035 * number(x, 'rtt_ms')) + 1


def loss_score(x):
    return 4 * math.exp(-180.94 * number(x, 'loss_ratio', hi=1)) + 1


def stall_score(x):
    return 5 - 4 * number(x, 'stall_ratio', hi=1)


def initial_buffer_score(x):
    return 4 * math.exp(-.0003 * number(x, 'buffer_ms')) + 1


class MosModel:
    def __init__(self, config):
        self.config = config['mos']

    def dimensions(self, kqi, formula_profile, stream_phase='steady'):
        if stream_phase not in ('steady', 'initial'):
            raise ValueError('INVALID_STREAM_PHASE')
        for key, value in kqi.items():
            if value is not None:
                number(value, key, hi=1 if key.endswith('_ratio') else math.inf)
        w1, w2, g1, g2 = self.config['formula_groups'][formula_profile]
        q = 4.5 if formula_profile == 'P5' else bitrate_score(kqi['bitrate_kbps'])
        if formula_profile != 'P5' and kqi.get('resolution') is not None:
            q = clip(5 - 4 * (w1 * (5-q) + w2 * (5-resolution_score(kqi['resolution']))))
        loss, stall = kqi.get('loss_ratio'), kqi.get('stall_ratio')
        if loss is None and stall is None:
            raise ValueError('INSUFFICIENT_KQI')
        if loss is None:
            v = stall_score(stall)
        elif stall is None:
            v = loss_score(loss)
        else:
            v = clip(5 - 4 * (g1 * (5-loss_score(loss)) + g2 * (5-stall_score(stall))))
        i = initial_buffer_score(kqi['buffer_ms']) if stream_phase == 'initial' and formula_profile in ('P3', 'P4') else rtt_score(kqi['rtt_ms'])
        return {'quality': q, 'interaction': i, 'view': v}

    def evaluate(self, kqi, formula_profile, stream_phase='steady'):
        dimensions = self.dimensions(kqi, formula_profile, stream_phase)
        return clip(sum(self.config['weights'][k] * v for k, v in dimensions.items()))
