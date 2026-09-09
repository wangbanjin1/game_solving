"""Frozen, separable analytic Mbps model shared by generation and solving."""
import math
from .mos_model import number


class BandwidthModel:
    def __init__(self, config):
        self.config = config

    def params(self, profile):
        return {**self.config['bandwidth'], **self.config['profiles'][profile].get('environment', {})}

    def forward(self, bandwidth_mbps, media, profile='demo_non_gbr', context=None):
        p = self.params(profile)
        c = number(bandwidth_mbps, 'bandwidth_mbps') * p['efficiency'] / (1+p['overhead_ratio'])
        x = c - media['bitrate_kbps']/1000 - p['audio_mbps']
        if x < p['margin_min_mbps'] - 1e-10:
            raise ValueError('INSUFFICIENT_SERVICE_MARGIN')
        return {**media, 'rtt_ms': p['rtt_floor_ms'] + p['rtt_coeff_ms_mbps']/x,
                'loss_ratio': min(1., p['loss_floor'] + p['loss_amp']*math.exp(-x/p['loss_scale_mbps'])),
                'stall_ratio': min(1., p['stall_floor'] + p['stall_amp']*math.exp(-x/p['stall_scale_mbps'])),
                'buffer_ms': p['buffer_floor_ms'] + 1000*p['burst_mbit']/c}

    def inverse(self, target, profile='demo_non_gbr', context=None):
        p = self.params(profile)
        phase = (context or {}).get('stream_phase', 'steady')
        x = p['margin_min_mbps']
        c_buffer = 0.
        for key, floor, amp, scale in [('rtt_ms','rtt_floor_ms','rtt_coeff_ms_mbps',None),
                                      ('loss_ratio','loss_floor','loss_amp','loss_scale_mbps'),
                                      ('stall_ratio','stall_floor','stall_amp','stall_scale_mbps'),
                                      ('buffer_ms','buffer_floor_ms','burst_mbit',None)]:
            if target.get(key) is None or (key == 'buffer_ms' and phase != 'initial'):
                continue
            value = number(target[key], key, hi=1 if key.endswith('_ratio') else math.inf)
            delta = value-p[floor]
            if delta < 0 or (delta <= 0 and p[amp] > 0):
                raise ValueError(f'UNREACHABLE_FLOOR: {key}')
            if p[amp] == 0:
                continue
            if key == 'buffer_ms':
                c_buffer = 1000*p[amp]/delta
            elif scale is None:
                x = max(x, p[amp]/delta)
            else:
                x = max(x, p[scale]*math.log(p[amp]/delta))
        c = max(number(target['bitrate_kbps'], 'bitrate_kbps')/1000 + p['audio_mbps'] + x, c_buffer)
        return c*(1+p['overhead_ratio'])/p['efficiency']
