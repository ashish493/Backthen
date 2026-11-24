"""Step 3: overlap GPU inference (real CLIP ViT-B/32) with CPU photo work; tests Hypothesis C.

Usage:
  python -m bench.exp_pipeline <photo_root> [--configs "pipelined:draft_m2:16:64 staged:draft_m2:16:64 ..."]
         [--serial-ref] [--repeats 3] [--out results/exp_pipeline.jsonl]

Per photo, CPU work = S1 hash + S2 metadata + S3 decode + S4 thumbnail/model input + S7 thumbnail write.
GPU work = S5 upload + S6 CLIP image encoding, in batches.
Config "kind:mode:workers:batch":
  staged    : all CPU work (thread pool), THEN all GPU work - no overlap
  pipelined : CPU thread pool feeds a bounded queue; a GPU thread consumes batches concurrently
--serial-ref adds the original baseline structure once: full decode, one thread, inference inline.
Hypothesis C signal (pipelined): GPU thread time waiting for input vs feeder time blocked on a full queue.
"""
import argparse
import collections
import json
import os
import queue
import random
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import psutil
import torch

from . import stages as S
from .cache import _disk_read_bytes, evict

try:
    import pynvml
    pynvml.nvmlInit()
    _NV = pynvml.nvmlDeviceGetHandleByIndex(0)
except Exception:  # noqa: BLE001
    _NV = None

THUMBS = os.path.join("out", "pipe_thumbs")


class Monitor:
    def __init__(self, interval=0.2):
        self.interval, self.cpu, self.gpu, self.peak_rss, self.peak_vram = interval, [], [], 0, 0
        self._stop = threading.Event()

    def _loop(self):
        p = psutil.Process()
        psutil.cpu_percent()
        while not self._stop.wait(self.interval):
            self.cpu.append(psutil.cpu_percent())
            self.peak_rss = max(self.peak_rss, p.memory_info().rss)
            if _NV:
                self.gpu.append(pynvml.nvmlDeviceGetUtilizationRates(_NV).gpu)
                self.peak_vram = max(self.peak_vram, pynvml.nvmlDeviceGetMemoryInfo(_NV).used)

    def __enter__(self):
        self._d0 = _disk_read_bytes()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._t.join()
        self.disk_read_bytes = _disk_read_bytes() - self._d0

    def summary(self):
        return dict(cpu_mean=statistics.mean(self.cpu) if self.cpu else None,
                    gpu_util_mean=statistics.mean(self.gpu) if self.gpu else None,
                    peak_rss_mib=self.peak_rss / 2**20, peak_vram_mib=self.peak_vram / 2**20,
                    disk_read_bytes=self.disk_read_bytes)


def load_clip():
    import open_clip
    m, _, _ = open_clip.create_model_and_transforms("ViT-B-32", pretrained="laion2b_s34b_b79k")
    return m.eval().half().cuda()


def bench_model(model, batches=(16, 32, 64, 128), iters=20):
    rows = []
    for b in batches:
        x_host = np.random.default_rng(0).standard_normal((b, 3, S.MODEL_SIZE, S.MODEL_SIZE), dtype=np.float32)
        torch.cuda.reset_peak_memory_stats()
        with torch.no_grad():
            for _ in range(3):
                model.encode_image(torch.from_numpy(x_host).cuda().half())
            torch.cuda.synchronize()
            t_up = t_inf = 0.0
            for _ in range(iters):
                t0 = time.perf_counter()
                x = torch.from_numpy(x_host).cuda().half()
                torch.cuda.synchronize()
                t1 = time.perf_counter()
                model.encode_image(x).float().cpu()
                t2 = time.perf_counter()
                t_up += t1 - t0
                t_inf += t2 - t1
        r = dict(kind="model_bench", batch=b, upload_ms_per_img=1000 * t_up / iters / b,
                 infer_ms_per_img=1000 * t_inf / iters / b, imgs_per_s=b * iters / (t_up + t_inf),
                 peak_alloc_mib=torch.cuda.max_memory_allocated() / 2**20)
        rows.append(r)
        print(f"CLIP batch {b:3d}: upload {r['upload_ms_per_img']:.2f} ms/img, infer {r['infer_ms_per_img']:.2f} ms/img, "
              f"{r['imgs_per_s']:.0f} img/s, peak alloc {r['peak_alloc_mib']:.0f} MiB")
    return rows


def cpu_work(path, mode, root):
    t = {}
    t0 = time.perf_counter()
    S.hash_full(path)
    t1 = time.perf_counter()
    S.metadata_image(path)
    t2 = time.perf_counter()
    if mode == "full":
        im = S.decode_image(path)
        t3 = time.perf_counter()
        thumb, arr = S.make_thumbnail(im), S.to_model_input(im)
    else:
        im, orig, s = S.decode_image_draft(path, margin=2)
        t3 = time.perf_counter()
        thumb, arr = S.make_thumbnail(im, orig, s), S.to_model_input(im, orig, s)
    t4 = time.perf_counter()
    S.write_thumbnail(thumb, os.path.join(THUMBS, os.path.relpath(path, root) + ".jpg"))
    t5 = time.perf_counter()
    t.update(S1_hash=t1 - t0, S2_metadata=t2 - t1, S3_decode=t3 - t2, S4_transform=t4 - t3, S7_write=t5 - t4)
    return arr, t


def gpu_encode(model, arrs):
    with torch.no_grad():
        x = torch.from_numpy(np.stack(arrs)).cuda().half()
        e = model.encode_image(x).float()
        return torch.nn.functional.normalize(e, dim=-1).cpu().numpy()


def add_times(acc, t):
    for k, v in t.items():
        acc[k] += v


def run_serial_ref(paths, root, model, batch):
    emb, pend, idx, acc = [], [], [], collections.defaultdict(float)
    t_gpu = 0.0
    with Monitor() as mon:
        t0 = time.perf_counter()
        for i, p in enumerate(paths):
            arr, t = cpu_work(p, "full", root)
            add_times(acc, t)
            pend.append(arr)
            if len(pend) == batch or i == len(paths) - 1:
                g = time.perf_counter()
                emb.append(gpu_encode(model, pend))
                t_gpu += time.perf_counter() - g
                pend = []
        wall = time.perf_counter() - t0
    return np.concatenate(emb), dict(wall_s=wall, gpu_busy_s=t_gpu, cpu_stage_s=dict(acc), **mon.summary())


def run_staged(paths, root, model, mode, workers, batch):
    acc = collections.defaultdict(float)
    with Monitor() as mon:
        t0 = time.perf_counter()
        with ThreadPoolExecutor(workers) as ex:
            res = list(ex.map(lambda p: cpu_work(p, mode, root), paths))
        t_cpu = time.perf_counter() - t0
        arrs = [r[0] for r in res]
        for r in res:
            add_times(acc, r[1])
        g = time.perf_counter()
        emb = np.concatenate([gpu_encode(model, arrs[i:i + batch]) for i in range(0, len(arrs), batch)])
        t_gpu = time.perf_counter() - g
        wall = time.perf_counter() - t0
    return emb, dict(wall_s=wall, cpu_phase_s=t_cpu, gpu_busy_s=t_gpu, cpu_stage_s=dict(acc), **mon.summary())


def run_pipelined(paths, root, model, mode, workers, batch, qbatches=4):
    q = queue.Queue(maxsize=qbatches * batch)          # bounded: about 4 batches of 0.6 MB model inputs
    emb = [None] * len(paths)
    gstat = dict(wait_s=0.0, busy_s=0.0)
    acc = collections.defaultdict(float)

    def consumer():
        done = False
        while not done:
            items = []
            t = time.perf_counter()
            while len(items) < batch:
                it = q.get()
                if it is None:
                    done = True
                    break
                items.append(it)
            g = time.perf_counter()
            gstat["wait_s"] += g - t
            if items:
                e = gpu_encode(model, [a for _, a in items])
                for (i, _), v in zip(items, e):
                    emb[i] = v
            gstat["busy_s"] += time.perf_counter() - g

    put_block = res_wait = 0.0
    window = 2 * workers                                # bounded number of in-flight CPU tasks
    with Monitor() as mon:
        t0 = time.perf_counter()
        th = threading.Thread(target=consumer)
        th.start()
        with ThreadPoolExecutor(workers) as ex:
            dq = collections.deque()
            def drain_one():
                nonlocal put_block, res_wait
                i, f = dq.popleft()
                t = time.perf_counter()
                arr, tt = f.result()
                res_wait += time.perf_counter() - t
                add_times(acc, tt)
                t = time.perf_counter()
                q.put((i, arr))
                put_block += time.perf_counter() - t
            for i, p in enumerate(paths):
                if len(dq) >= window:
                    drain_one()
                dq.append((i, ex.submit(cpu_work, p, mode, root)))
            while dq:
                drain_one()
        q.put(None)
        th.join()
        wall = time.perf_counter() - t0
    return np.stack(emb), dict(wall_s=wall, gpu_wait_s=gstat["wait_s"], gpu_busy_s=gstat["busy_s"],
                               feeder_put_block_s=put_block, feeder_result_wait_s=res_wait,
                               cpu_stage_s=dict(acc), **mon.summary())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--configs", default="pipelined:draft_m2:16:64 staged:draft_m2:16:64 "
                                         "pipelined:draft_m2:8:64 pipelined:draft_m2:16:32 "
                                         "pipelined:draft_m2:16:128 pipelined:full:16:64")
    ap.add_argument("--serial-ref", action="store_true")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--cold", action="store_true", help="evict all files from the OS cache before every run")
    ap.add_argument("--out", default="results/exp_pipeline.jsonl")
    ap.add_argument("--thumbs-dir", help="where thumbnails are written (default out/pipe_thumbs)")
    ap.add_argument("--stride", type=int, default=1, help="take every k-th photo (sorted) as a subset")
    a = ap.parse_args()
    global THUMBS
    if a.thumbs_dir:
        THUMBS = a.thumbs_dir

    paths = sorted(os.path.join(dp, f) for dp, _, fs in os.walk(a.root) for f in fs
                   if os.path.splitext(f)[1].lower() in S.IMG_EXT)[:: a.stride][: a.limit]
    print(f"{len(paths)} photos")
    model = load_clip()
    out = open(a.out, "w", encoding="utf-8")
    for r in bench_model(model):
        out.write(json.dumps(r) + "\n")

    total_bytes = sum(os.path.getsize(p) for p in paths)
    regime = "cold" if a.cold else "warm"

    def prepare():
        """cold: evict every file; warm: read every file so it is cached. Excluded from timing."""
        if a.cold:
            evict(paths)
        else:
            for p in paths:
                with open(p, "rb") as f:
                    while f.read(1 << 22):
                        pass

    def phys(r):
        return r["disk_read_bytes"] / total_bytes

    embs = {}
    if a.serial_ref:
        prepare()
        e, r = run_serial_ref(paths, a.root, model, 64)
        embs["serial_ref"] = e
        row = dict(kind="run", config="serial_ref:full:1:64", rep=0, n=len(paths), regime=regime,
                   physical_read_ratio=phys(r), **r)
        out.write(json.dumps(row) + "\n")
        out.flush()
        print(f"serial_ref [{regime}]: {r['wall_s']:.1f}s ({len(paths)/r['wall_s']:.1f} photos/s) phys={phys(r):.2f}")

    configs = a.configs.split()
    rng = random.Random(0)
    for rep in range(a.repeats):
        rng.shuffle(configs)
        for c in configs:
            kind, mode, w, b = c.split(":")
            w, b = int(w), int(b)
            prepare()
            if kind == "staged":
                e, r = run_staged(paths, a.root, model, mode, w, b)
            else:
                e, r = run_pipelined(paths, a.root, model, mode, w, b)
            row = dict(kind="run", config=c, rep=rep, n=len(paths), regime=regime, physical_read_ratio=phys(r),
                       disk_mb_s=r["disk_read_bytes"] / 1e6 / r["wall_s"], photos_per_s=len(paths) / r["wall_s"], **r)
            key = f"{mode}:{b}"
            if key in embs:                              # same mode + batch -> embeddings should agree
                row["cos_min_vs_first_same_mode"] = float((embs[key] * e).sum(1).min())
            else:
                embs[key] = e
            if "serial_ref" in embs:
                row["cos_min_vs_serial_ref"] = float((embs["serial_ref"] * e).sum(1).min())
            out.write(json.dumps(row) + "\n")
            out.flush()
            extra = (f" gpu_wait={r['gpu_wait_s']:.1f}s feeder_block={r['feeder_put_block_s']:.1f}s"
                     if kind == "pipelined" else f" cpu_phase={r['cpu_phase_s']:.1f}s")
            print(f"rep{rep} [{regime}] phys={phys(r):.2f} disk={row['disk_mb_s']:.0f}MB/s {c:26s} {r['wall_s']:6.1f}s {len(paths)/r['wall_s']:6.1f} photos/s "
                  f"gpu_busy={r['gpu_busy_s']:.1f}s{extra} cpu={r['cpu_mean']:.0f}% gpu={r['gpu_util_mean']:.0f}% "
                  f"rss={r['peak_rss_mib']:.0f}MiB vram={r['peak_vram_mib']:.0f}MiB")
    out.close()


if __name__ == "__main__":
    main()
