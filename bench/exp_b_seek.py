"""Hypothesis B: does random seeking in video cause redundant decoding?

Usage: python -m bench.exp_b_seek <dataset_root> [--n 8 32 128] [--repeats 3] [--stride 4]
                                   [--out results/exp_b.jsonl]

For each clip and N, N target timestamps are spaced evenly over the clip. Strategies:
  sequential : decode from start, keep first frame with t >= target, stop after last target
  seek       : per target, seek to preceding keyframe, decode forward to first frame t >= target
  keyframe   : decode keyframes only (skip_frame=NONKEY), keep nearest keyframe per target  [NOT equal output]
  decord     : decord.VideoReader.get_batch on the frame indices of the sequential/seek picks
Equal-output strategies (sequential, seek) must return identical PTS and pixel hashes.
Decode work is counted in frames, plus wall time per returned frame. Pixel conversion is excluded
from timing (raw frame -> ndarray hash is taken *after* the timer) so only decode cost is compared.
"""
import argparse
import hashlib
import json
import os
import statistics
import time

import av
import numpy as np

from .stages import VID_EXT


def clip_info(path):
    with av.open(path) as c:
        s = c.streams.video[0]
        start = float(s.start_time * s.time_base) if s.start_time is not None else 0.0
        dur = float(c.duration / av.time_base)
        return dict(start=start, dur=dur, tb=float(s.time_base), fps=float(s.average_rate or 0),
                    codec=s.codec_context.name, w=s.width, h=s.height)


def targets(info, n):
    return [info["start"] + i * info["dur"] / n for i in range(n)]


def _t(fr, tb):
    return fr.pts * tb if fr.pts is not None else None


def run_sequential(path, tgts, info):
    tb, picks, decoded, ti = info["tb"], [], 0, 0
    with av.open(path) as c:
        s = c.streams.video[0]
        s.thread_type = "AUTO"
        t0 = time.perf_counter()
        for fr in c.decode(s):
            decoded += 1
            t = _t(fr, tb)
            while ti < len(tgts) and t is not None and t >= tgts[ti] - 1e-9:
                picks.append(fr)       # frame may satisfy several targets if clip is sparse
                ti += 1
            if ti >= len(tgts):
                break
        dt = time.perf_counter() - t0
        return picks, decoded, dt, _hashes(picks, tb)


def run_seek(path, tgts, info):
    tb, picks, decoded = info["tb"], [], 0
    with av.open(path) as c:
        s = c.streams.video[0]
        s.thread_type = "AUTO"
        t0 = time.perf_counter()
        for tg in tgts:
            c.seek(int(tg / tb), stream=s, backward=True, any_frame=False)
            for fr in c.decode(s):
                decoded += 1
                t = _t(fr, tb)
                if t is not None and t >= tg - 1e-9:
                    picks.append(fr)
                    break
        dt = time.perf_counter() - t0
        return picks, decoded, dt, _hashes(picks, tb)


def run_keyframe(path, tgts, info):
    tb, keys, decoded = info["tb"], [], 0
    with av.open(path) as c:
        s = c.streams.video[0]
        s.thread_type = "AUTO"
        s.codec_context.skip_frame = "NONKEY"
        t0 = time.perf_counter()
        for fr in c.decode(s):
            decoded += 1
            keys.append(fr)
        dt = time.perf_counter() - t0
        ts = [_t(f, tb) for f in keys]
        picks = []
        for tg in tgts:
            j = int(np.argmin([abs(t - tg) for t in ts])) if ts else None
            if j is not None:
                picks.append(keys[j])
        return picks, decoded, dt, _hashes(picks, tb)


def run_decord(path, idx):
    import decord
    decord.bridge.set_bridge("native")
    t0 = time.perf_counter()
    vr = decord.VideoReader(path, num_threads=0)
    open_s = time.perf_counter() - t0
    idx = [min(i, len(vr) - 1) for i in idx]
    t1 = time.perf_counter()
    batch = vr.get_batch(idx).asnumpy()
    dt = time.perf_counter() - t1
    return batch.shape[0], open_s, dt, [hashlib.md5(b.tobytes()).hexdigest()[:8] for b in batch]


def _hashes(frames, tb):
    """(pts_seconds, pixel-hash) per picked frame; computed after the timer stops."""
    return [(round(_t(f, tb), 6), hashlib.md5(f.to_ndarray(format="rgb24").tobytes()).hexdigest()[:8])
            for f in frames]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--n", type=int, nargs="+", default=[8, 32, 128])
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--stride", type=int, default=4, help="take every k-th clip by size")
    ap.add_argument("--out", default="results/exp_b.jsonl")
    ap.add_argument("--no-decord", action="store_true")
    a = ap.parse_args()

    clips = sorted((os.path.join(dp, f) for dp, _, fs in os.walk(a.root) for f in fs
                    if os.path.splitext(f)[1].lower() in VID_EXT), key=os.path.getsize)
    clips = clips[:: a.stride]
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    out = open(a.out, "w", encoding="utf-8")

    for path in clips:
        info = clip_info(path)
        name = os.path.relpath(path, a.root)
        for n in a.n:
            tg = targets(info, n)
            ref = None
            for strat, fn in (("sequential", run_sequential), ("seek", run_seek),
                              ("keyframe", run_keyframe)):
                times, decoded, picks = [], 0, 0
                for _ in range(a.repeats):
                    p, decoded, dt, hs = fn(path, tg, info)
                    times.append(dt)
                    picks = len(p)
                if strat == "sequential":
                    ref = hs
                same = (hs == ref) if strat != "keyframe" else None
                row = dict(file=name, codec=info["codec"], w=info["w"], h=info["h"], dur_s=round(info["dur"], 2),
                           fps=info["fps"], n_targets=n, strategy=strat, frames_decoded=decoded,
                           frames_returned=picks, decode_s=statistics.median(times),
                           identical_to_sequential=same)
                out.write(json.dumps(row) + "\n")
                out.flush()
                print(f"{name[:42]:42s} N={n:3d} {strat:10s} dec={decoded:6d} ret={picks:3d} "
                      f"{statistics.median(times)*1000:8.0f}ms same={same}")
            if not a.no_decord:
                try:
                    idx = [int(round((t - info["start"]) * info["fps"])) for t in tg]
                    times = []
                    for _ in range(a.repeats):
                        k, open_s, dt, _h = run_decord(path, idx)
                        times.append(open_s + dt)
                    row = dict(file=name, codec=info["codec"], w=info["w"], h=info["h"], dur_s=round(info["dur"], 2),
                               fps=info["fps"], n_targets=n, strategy="decord", frames_decoded=None,
                               frames_returned=k, decode_s=statistics.median(times),
                               identical_to_sequential=None)
                    out.write(json.dumps(row) + "\n")
                    print(f"{name[:42]:42s} N={n:3d} {'decord':10s} ret={k:3d} {statistics.median(times)*1000:8.0f}ms")
                except Exception as e:  # noqa: BLE001
                    print("decord failed:", repr(e)[:120])
                    a.no_decord = True
    out.close()


if __name__ == "__main__":
    main()
