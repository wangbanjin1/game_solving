"""Normalized source ranges; demo bitrate and RTT proxy stay explicitly marked."""
import math

RESOLUTIONS = {'openlive': [480,540,720,1080], 'video': [360,480,540,720,1080,2160],
               'cloudgame': [480,540,720,1080], 'meeting': [270,360,720], 'voip': [270,360,720]}
BITRATE_MAX = {'openlive': 6000, 'cloudgame': 20000, 'meeting': 6000, 'voip': 6000}


def classify(value, good_max, middle_max):
    return 'good' if value <= good_max else 'average' if value <= middle_max else 'poor'


def source_valid(business, k):
    if not all(v is None or (math.isfinite(v) and v >= 0) for v in k.values()):
        return False
    if not 0 <= k['loss_ratio'] <= .05 or not 0 <= k['stall_ratio'] <= 1:
        return False
    if k['rtt_ms'] > (500 if business == 'video' else 460 if business == 'game' else 300):
        return False
    if business in RESOLUTIONS and k.get('resolution') not in RESOLUTIONS[business]:
        return False
    if business in BITRATE_MAX and k['bitrate_kbps'] > BITRATE_MAX[business]:
        return False
    return business != 'video' or k['buffer_ms'] <= 10000


def catalog():
    return {'version': 'user_ranges_20260909_v1', 'source': 'synthetic', 'resolutions': RESOLUTIONS,
            'bitrate_max_kbps': BITRATE_MAX, 'loss_ratio': {'good': [0,.0015], 'average': [.0015,.005], 'poor': [.005,.05], 'boundary': 'upper_inclusive_lower_exclusive_except_zero'},
            'stall_ratio': {'good': [0,.1], 'average': [.1,.3], 'poor': [.3,1]},
            'bitrate_mbps': {'openlive_meeting_voip': [0,.7,1.5,6], 'cloudgame': [0,3,6,20]},
            'latency_ms': {'openlive_cloudgame_meeting_voip': [0,80,150,300], 'video': [0,100,200,500], 'game': [0,50,100,460]},
            'video_buffer_ms': [0,1000,2000,10000], 'game_jitter_ms': [0,10,20,50],
            'meeting_initial_range': None, 'notes': ['video bitrate and game demand are demo values', 'resolution interpreted as vertical pixels (assumed)', 'identity_demo RTT proxy is not measured RTT', 'meeting initial delay range missing; steady only', 'game jitter does not enter MOS', 'default model does not cover all source ranges']}
