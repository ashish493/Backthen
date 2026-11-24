"""Hybrid CPU+NVDEC decode test (follow-up to Hypothesis D; overlaps with Hypothesis F).

Usage: python -m bench.exp_hybrid <dataset_root> [--configs "1,0 2,0 0,1 1,1 ..."] [--repeats 3]
                                  [--stride 1] [--out results/exp_hybrid.jsonl]

A config "c,n" = c CPU decode workers + n NVDEC decode workers (threads) pulling clips from ONE shared
queue (largest clip first). Each worker does the full per-clip video work: S3 decode + S4 transform +
S5 upload (same code as exp_d_nvdec). "1,0" is the serial CPU baseline.
Reported per run: wall time, clips/s, effective decode fps, mean CPU %, mean NVDEC engine %, peak RSS/VRAM,
how many clips each decoder handled, and correctness vs a CPU reference (PTS equal; model-input PSNR).
Config order is shuffled within each repeat round to avoid order/thermal bias.
"""
import argparse
import json
import os
import queue
import random
import statistics
import threading
import time

import psutil
import torch

from . import stages as S
from .cache import _disk_read_bytes, evict
from .exp_d_nvdec import decode_cpu, decode_nvdec, psnr, s4_s5

try:
    import pynvml
    pynvml.nvmlInit()
    _NV = pynvml.nvmlDeviceGetHandleByIndex(0)
except Exception:  # noqa: BLE001
    _NV = None


class Monitor:
    def __init__(self, interval=0.2):
        self.interval, self.cpu, self.dec, self.peak_rss, self.peak_vram = interval, [], [], 0, 0
        self._stop = threading.Event()

    def _loop(self):
        p = psutil.Process()
        psutil.cpu_percent()
        while not self._stop.wait(self.interval):
            self.cpu.append(psutil.cpu_percent())
            self.peak_rss = max(self.peak_rss, p.memory_info().rss)
            if _NV:
                try:
                    self.dec.append(pynvml.nvmlDeviceGetDecoderUtilization(_NV)[0])
                except Exception:  # noqa: BLE001
                    pass
                self.peak_vram = max(self.peak_vram, pynvml.nvmlDeviceGetMemoryInfo(_NV).used)

    def __enter__(self):
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._t.join()


def run_config(clips, ncpu, nnv, ref):
    q = queue.Queue()
    for c in clips:
        q.put(c)
    results, errors, lock = [], [], threading.Lock()

    def worker(kind):
        while True:
            try:
                path = q.get_nowait()
            except queue.Empty:
                return
            used = kind
            try:
                if kind == "nvdec":
                    try:
                        picks, pts, st = decode_nvdec(path)
                    except Exception as e:  # noqa: BLE001
                        with lock:
                            errors.append((path, repr(e)[:100]))
                        picks, pts, st = decode_cpu(path)
                        used = "nvdec_fallback_cpu"
                else:
                    picks, pts, st = decode_cpu(path)
                arrs, *_ = s4_s5(picks)
                del picks
            except Exception as e:  # noqa: BLE001
                with lock:
                    errors.append((path, "FATAL " + repr(e)[:100]))
                continue
            with lock:
                results.append((path, used, pts, arrs, st["frames_decoded"]))

    threads = [threading.Thread(target=worker, args=("cpu",)) for _ in range(ncpu)] + \
              [threading.Thread(target=worker, args=("nvdec",)) for _ in range(nnv)]
    d0 = _disk_read_bytes()
    with Monitor() as mon:
        t0 = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        wall = time.perf_counter() - t0
    disk = _disk_read_bytes() - d0

    # correctness (outside the timed region)
    pts_bad, ps_all, ps_nv = [], [], []
    for path, used, pts, arrs, _ in results:
        rpts, rarrs = ref[path]
        if pts != rpts:
            pts_bad.append(path)
        n = min(len(arrs), len(rarrs))
        m = min(psnr(arrs[i], rarrs[i]) for i in range(n)) if n else 99.0
        ps_all.append(m)
        if used == "nvdec":
            ps_nv.append(m)
    total = sum(os.path.getsize(c) for c in clips)
    return dict(
        wall_s=wall, clips_done=len(results), clips_per_s=len(results) / wall,
        physical_read_ratio=disk / total, disk_mb_s=disk / 1e6 / wall,
        frames_decoded=sum(r[4] for r in results), decode_fps=sum(r[4] for r in results) / wall,
        cpu_mean=statistics.mean(mon.cpu) if mon.cpu else None,
        nvdec_util_mean=statistics.mean(mon.dec) if mon.dec else None,
        peak_rss_mib=mon.peak_rss / 2**20, peak_vram_mib=mon.peak_vram / 2**20,
        n_cpu_clips=sum(r[1] == "cpu" for r in results), n_nvdec_clips=sum(r[1] == "nvdec" for r in results),
        pts_mismatch=len(pts_bad), psnr_min_all=min(ps_all) if ps_all else None,
        psnr_min_nvdec=min(ps_nv) if ps_nv else None, errors=errors[:3], n_errors=len(errors))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--configs", default="1,0 2,0 3,0 4,0 0,1 0,2 1,1 2,1 3,1 2,2")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--out", default="results/exp_hybrid.jsonl")
    ap.add_argument("--cold", action="store_true", help="evict all clips from the OS cache before every run")
    a = ap.parse_args()

    clips = [os.path.join(dp, f) for dp, _, fs in os.walk(a.root) for f in fs
             if os.path.splitext(f)[1].lower() in S.VID_EXT]
    clips = sorted(clips, key=os.path.getsize, reverse=True)[:: a.stride]   # largest first (LPT order)
    configs = [tuple(int(x) for x in c.split(",")) for c in a.configs.split()]
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)

    torch.zeros(1).cuda()
    print(f"{len(clips)} clips; building CPU reference (serial) ...")
    ref = {}
    for p in clips:
        picks, pts, _ = decode_cpu(p)
        arrs, *_ = s4_s5(picks)
        ref[p] = (pts, arrs)
        del picks
    decode_nvdec(clips[-1])                      # warm-up NVDEC session

    rng = random.Random(0)
    out = open(a.out, "w", encoding="utf-8")
    for rep in range(a.repeats):
        order = configs[:]
        rng.shuffle(order)
        for c, n in order:
            if a.cold:
                evict(clips)
            r = run_config(clips, c, n, ref)
            row = dict(cpu_workers=c, nvdec_workers=n, rep=rep, n_clips=len(clips),
                       regime="cold" if a.cold else "warm", **r)
            out.write(json.dumps(row) + "\n")
            out.flush()
            print(f"rep{rep} phys={r['physical_read_ratio']:.2f} disk={r['disk_mb_s']:.0f}MB/s "
                  f"cpu={c} nv={n}: {r['wall_s']:6.1f}s {r['clips_per_s']:.2f} clips/s "
                  f"cpu%={r['cpu_mean'] or 0:4.0f} nvdec%={r['nvdec_util_mean'] or 0:4.0f} "
                  f"clips cpu/nv={r['n_cpu_clips']}/{r['n_nvdec_clips']} pts_bad={r['pts_mismatch']} "
                  f"psnr_nv={r['psnr_min_nvdec']} err={r['n_errors']}")
    out.close()


if __name__ == "__main__":
    main()
