#!/usr/bin/env python3
"""Read-only topic probe: rate/jitter + message metadata. Usage: topic_probe.py DURATION topic[:type] ..."""
import sys, time, json, statistics, importlib
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data, QoSProfile, ReliabilityPolicy, HistoryPolicy
import numpy as np

def load_type(t):
    pkg, _, name = t.split('/')
    return getattr(importlib.import_module(pkg + '.msg'), name)

class Probe(Node):
    def __init__(self, specs):
        super().__init__('measure_topic_probe')
        self.data = {}
        types = dict(self.get_topic_names_and_types())
        for topic in specs:
            if topic not in types:
                self.data[topic] = {'error': 'topic not present'}
                continue
            tname = types[topic][0]
            self.data[topic] = {'type': tname, 'stamps': [], 'meta': None, 'depth': None}
            qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT, history=HistoryPolicy.KEEP_LAST)
            self.create_subscription(load_type(tname), topic, lambda m, t=topic: self.cb(t, m), qos)

    def cb(self, topic, m):
        d = self.data[topic]
        d['stamps'].append(time.monotonic())
        tn = d['type']
        if tn == 'sensor_msgs/msg/LaserScan':
            r = np.array(m.ranges, dtype=np.float32)
            fin = np.isfinite(r) & (r > 0)
            if d['meta'] is None:
                d['meta'] = dict(frame=m.header.frame_id, n_ranges=len(r), angle_min=m.angle_min, angle_max=m.angle_max,
                                 angle_increment=m.angle_increment, range_min=m.range_min, range_max=m.range_max,
                                 scan_time=m.scan_time, time_increment=m.time_increment)
                d['valid_frac'] = []; d['n_list'] = []
            d['valid_frac'].append(float(fin.mean())); d['n_list'].append(len(r))
        elif tn == 'sensor_msgs/msg/Image':
            if d['meta'] is None:
                d['meta'] = dict(frame=m.header.frame_id, width=m.width, height=m.height, encoding=m.encoding)
            if m.encoding in ('16UC1', 'mono16') and len(d.setdefault('depth_samples', [])) < 30:
                a = np.frombuffer(bytes(m.data), dtype=np.uint16).reshape(m.height, m.width)
                h, w = a.shape
                c = a[h//2-32:h//2+32, w//2-32:w//2+32]
                cz = c[c > 0]; az = a[a > 0]
                d['depth_samples'].append(dict(center64_median_mm=float(np.median(cz)) if cz.size else None,
                                               center64_valid_frac=float(cz.size / c.size),
                                               full_median_mm=float(np.median(az)) if az.size else None,
                                               full_valid_frac=float(az.size / a.size)))
        elif tn == 'sensor_msgs/msg/PointCloud2':
            if d['meta'] is None:
                d['meta'] = dict(frame=m.header.frame_id, width=m.width, height=m.height, point_step=m.point_step)
        else:
            if d['meta'] is None:
                try:
                    from rosidl_runtime_py import message_to_ordereddict
                    s = json.dumps(message_to_ordereddict(m), default=str)
                    d['meta'] = s[:1500]
                except Exception as e:
                    d['meta'] = repr(e)

def main():
    dur = float(sys.argv[1]); topics = sys.argv[2:]
    rclpy.init()
    n = Probe(topics)
    t0 = time.monotonic()
    while time.monotonic() - t0 < dur:
        rclpy.spin_once(n, timeout_sec=0.1)
    out = {}
    for t, d in n.data.items():
        if 'error' in d:
            out[t] = d; continue
        s = d['stamps']
        r = {'type': d['type'], 'count': len(s), 'window_s': dur}
        if len(s) > 1:
            dt = np.diff(s)
            r.update(mean_hz=float((len(s)-1) / (s[-1]-s[0])), dt_min_ms=float(dt.min()*1e3), dt_max_ms=float(dt.max()*1e3),
                     dt_std_ms=float(dt.std()*1e3))
        r['meta'] = d['meta']
        if 'valid_frac' in d:
            r['valid_frac_mean'] = float(np.mean(d['valid_frac'])); r['n_ranges_set'] = sorted(set(d['n_list']))
        if d.get('depth_samples'):
            ds = d['depth_samples']
            r['depth_samples_n'] = len(ds)
            for k in ds[0]:
                vals = [x[k] for x in ds if x[k] is not None]
                r['depth_' + k + '_median_over_frames'] = float(np.median(vals)) if vals else None
        out[t] = r
    print(json.dumps(out, indent=1, default=str))
    n.destroy_node(); rclpy.shutdown()

main()
