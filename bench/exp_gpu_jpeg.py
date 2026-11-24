"""Step 7: does GPU JPEG decoding (nvJPEG via torchvision) improve photo throughput?

    python -m bench.exp_gpu_jpeg <photo_root> [--repeat 8] [--batches 8 16 32] [--cpu-workers 16] [--repeats 3]
                                 [--out results/exp_gpu_jpeg.jsonl]

Input bytes are held in memory (no disk effects). Per photo the full job is: decode -> thumbnail (512 px) and
224 px model input -> CLIP ViT-B/32 embedding. Thumbnail writing is excluded for every variant.
Variants:
  cpu_draft{W}   : reduced-size JPEG decode (draft_m2) in W CPU threads, then CLIP on the GPU (current pipeline)
  gpu_nvjpeg_B{b}: one feeder thread: batched nvJPEG decode of b photos at FULL size on the GPU, GPU resize/crop/
                   normalise (antialiased), CLIP embedding without leaving the GPU
  hybrid_B{b}+W  : the GPU feeder AND W CPU draft threads pull from one shared photo queue
Correctness (unique photos only): PSNR of the model input vs the exact CPU reference, and the same search-level
gate as before (top-1/top-5 neighbour overlap within 2 points of the reducing_gap control).
"""
import argparse
import io
import json
import os
import queue
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from torchvision.io import ImageReadMode, decode_jpeg

from . import stages as S
from .exp_d_nvdec import psnr
from .exp_images import knn_overlap

MEAN = torch.tensor(S._MEAN).view(3, 1, 1)
STD = torch.tensor(S._STD).view(3, 1, 1)


def orient_of(data):
    with Image.open(io.BytesIO(data)) as im:
        return im.getexif().get(274, 1)


def apply_orient(t, o):
    """t: CxHxW tensor. Same transforms as PIL ImageOps.exif_transpose."""
    if o == 2:
        return t.flip(-1)
    if o == 3:
        return t.flip(-1).flip(-2)
    if o == 4:
        return t.flip(-2)
    if o == 5:
        return t.transpose(-1, -2)
    if o == 6:
        return torch.rot90(t, 3, (-2, -1))
    if o == 7:
        return t.transpose(-1, -2).flip(-1).flip(-2)
    if o == 8:
        return torch.rot90(t, 1, (-2, -1))
    return t


def gpu_model_input(img_u8):
    """img_u8: CxHxW uint8 on GPU (already oriented) -> normalised 3x224x224 float32 on GPU."""
    c, h, w = img_u8.shape
    x = img_u8.float().unsqueeze(0) / 255.0
    s = S.MODEL_SIZE / min(h, w)
    nh, nw = max(S.MODEL_SIZE, round(h * s)), max(S.MODEL_SIZE, round(w * s))
    x = F.interpolate(x, size=(nh, nw), mode="bicubic", antialias=True, align_corners=False).clamp_(0, 1)
    t, l = (nh - S.MODEL_SIZE) // 2, (nw - S.MODEL_SIZE) // 2
    x = x[0, :, t:t + S.MODEL_SIZE, l:l + S.MODEL_SIZE]
    return (x - MEAN.to(x.device)) / STD.to(x.device)


def gpu_thumb(img_u8):
    c, h, w = img_u8.shape
    r = S.THUMB_LONG_EDGE / max(h, w)
    if r >= 1:
        return img_u8
    x = img_u8.float().unsqueeze(0)
    x = F.interpolate(x, size=(max(1, round(h * r)), max(1, round(w * r))), mode="bilinear", antialias=True,
                      align_corners=False)
    return x[0].round().clamp(0, 255).to(torch.uint8)


def gpu_batch(datas, model, want_inputs=False):
    """Decode + resize + embed a list of JPEG byte strings on the GPU. Returns embeddings (n,512) float32 numpy."""
    tens = [torch.frombuffer(bytearray(d), dtype=torch.uint8) for d in datas]
    orients = [orient_of(d) for d in datas]
    imgs = decode_jpeg(tens, device="cuda", mode=ImageReadMode.RGB)
    xs, thumbs = [], []
    for im, o in zip(imgs, orients):
        im = apply_orient(im, o)
        xs.append(gpu_model_input(im))
        thumbs.append(gpu_thumb(im))
    x = torch.stack(xs)
    with torch.no_grad():
        e = model.encode_image(x.half()).float()
        e = torch.nn.functional.normalize(e, dim=-1).cpu().numpy()
    torch.cuda.synchronize()
    return (e, x.cpu().numpy()) if want_inputs else e


def cpu_item(data):
    im, orig, sc = S.decode_image_draft(io.BytesIO(data), margin=2)
    S.make_thumbnail(im, orig, sc)
    return S.to_model_input(im, orig, sc)


def cpu_embed(model, arrs):
    out = []
    with torch.no_grad():
        for i in range(0, len(arrs), 64):
            x = torch.from_numpy(np.stack(arrs[i:i + 64])).cuda().half()
            out.append(torch.nn.functional.normalize(model.encode_image(x).float(), dim=-1).cpu().numpy())
    return np.concatenate(out)


def run_cpu(datas, model, workers):
    t = time.perf_counter()
    with ThreadPoolExecutor(workers) as ex:
        arrs = list(ex.map(cpu_item, datas))
    e = cpu_embed(model, arrs)
    return e, time.perf_counter() - t


def run_gpu(datas, model, b):
    t = time.perf_counter()
    es = [gpu_batch(datas[i:i + b], model) for i in range(0, len(datas), b)]
    return np.concatenate(es), time.perf_counter() - t


def run_hybrid(datas, model, b, workers):
    """GPU feeder + CPU pool share one queue of photo indices; returns throughput only (order-free)."""
    q = queue.Queue()
    for i in range(len(datas)):
        q.put(i)
    done = dict(cpu=0, gpu=0)
    arrs, lock = [], threading.Lock()

    def cpu_w():
        while True:
            try:
                i = q.get_nowait()
            except queue.Empty:
                return
            a = cpu_item(datas[i])
            with lock:
                arrs.append(a)
                done["cpu"] += 1

    def gpu_w():
        while True:
            idx = []
            for _ in range(b):
                try:
                    idx.append(q.get_nowait())
                except queue.Empty:
                    break
            if not idx:
                return
            gpu_batch([datas[i] for i in idx], model)
            with lock:
                done["gpu"] += len(idx)

    t = time.perf_counter()
    ths = [threading.Thread(target=cpu_w) for _ in range(workers)] + [threading.Thread(target=gpu_w)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    if arrs:
        cpu_embed(model, arrs)
    return done, time.perf_counter() - t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--repeat", type=int, default=8, help="repeat the photo list this many times for timing")
    ap.add_argument("--batches", type=int, nargs="+", default=[8, 16, 32])
    ap.add_argument("--cpu-workers", type=int, default=16)
    ap.add_argument("--hybrid-workers", type=int, nargs="+", default=[8, 12])
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", default="results/exp_gpu_jpeg.jsonl")
    a = ap.parse_args()

    paths = sorted(os.path.join(dp, f) for dp, _, fs in os.walk(a.root) for f in fs if f.lower().endswith((".jpg", ".jpeg")))
    uniq = []
    for p in paths:
        with open(p, "rb") as f:
            uniq.append(f.read())
    datas = uniq * a.repeat
    mb = sum(len(d) for d in datas) / 1e6
    print(f"{len(uniq)} unique JPEGs, {len(datas)} timed decodes ({mb:.0f} MB)")

    import open_clip
    model, _, _ = open_clip.create_model_and_transforms("ViT-B-32", pretrained="laion2b_s34b_b79k")
    model = model.eval().half().cuda()
    out = open(a.out, "w", encoding="utf-8")

    # ---- correctness on unique photos
    ref_in, ctl_in = [], []
    for d in uniq:
        im = S.decode_image(io.BytesIO(d))
        ref_in.append(S.to_model_input(im))
        ctl_in.append(S.to_model_input(im, reducing_gap=3.0))
    e_ref, e_ctl = cpu_embed(model, ref_in), cpu_embed(model, ctl_in)
    e_cpu, _ = run_cpu(uniq, model, a.cpu_workers)
    e_gpu_parts, x_gpu = [], []
    for i in range(0, len(uniq), 16):
        e, x = gpu_batch(uniq[i:i + 16], model, want_inputs=True)
        e_gpu_parts.append(e)
        x_gpu.append(x)
    e_gpu, x_gpu = np.concatenate(e_gpu_parts), np.concatenate(x_gpu)
    ctl1, ctl5 = knn_overlap(e_ref, e_ctl, 1), knn_overlap(e_ref, e_ctl, 5)
    q = dict(kind="quality", n=len(uniq),
             gpu_model_psnr_min=float(min(psnr(x_gpu[i], ref_in[i]) for i in range(len(uniq)))),
             gpu_model_psnr_median=float(np.median([psnr(x_gpu[i], ref_in[i]) for i in range(len(uniq))])),
             gpu_cos_min=float((e_ref * e_gpu).sum(1).min()), gpu_cos_median=float(np.median((e_ref * e_gpu).sum(1))),
             gpu_knn1=knn_overlap(e_ref, e_gpu, 1), gpu_knn5=knn_overlap(e_ref, e_gpu, 5),
             cpu_draft_knn1=knn_overlap(e_ref, e_cpu, 1), cpu_draft_knn5=knn_overlap(e_ref, e_cpu, 5),
             control_knn1=ctl1, control_knn5=ctl5)
    q["gate_pass_psnr"] = q["gpu_model_psnr_min"] >= 40
    q["gate_pass_knn"] = q["gpu_knn1"] >= ctl1 - 0.02 and q["gpu_knn5"] >= ctl5 - 0.02
    out.write(json.dumps(q) + "\n")
    print(json.dumps(q, indent=1))

    # ---- throughput
    configs = [("cpu", a.cpu_workers, None)] + [("gpu", None, b) for b in a.batches] + \
              [("hybrid", w, a.batches[-2] if len(a.batches) > 1 else a.batches[0]) for w in a.hybrid_workers]
    run_gpu(uniq[:32], model, 16)                                   # warm-up (cuda kernels, nvjpeg)
    run_cpu(uniq[:32], model, 4)
    for rep in range(a.repeats):
        for name, w, b in configs:
            if name == "cpu":
                _, dt = run_cpu(datas, model, w)
                row = dict(variant=f"cpu_draft{w}", photos_per_s=len(datas) / dt)
            elif name == "gpu":
                _, dt = run_gpu(datas, model, b)
                row = dict(variant=f"gpu_nvjpeg_B{b}", photos_per_s=len(datas) / dt)
            else:
                done, dt = run_hybrid(datas, model, b, w)
                row = dict(variant=f"hybrid_B{b}+W{w}", photos_per_s=len(datas) / dt, gpu_share=done["gpu"] / len(datas))
            row.update(kind="throughput", rep=rep, n=len(datas), wall_s=dt, peak_vram_mib=torch.cuda.max_memory_allocated() / 2**20)
            out.write(json.dumps(row) + "\n")
            out.flush()
            print(f"rep{rep} {row['variant']:20s} {row['photos_per_s']:7.1f} photos/s  ({dt:5.1f}s)"
                  + (f" gpu_share={row['gpu_share']:.2f}" if "gpu_share" in row else ""))
    out.close()


if __name__ == "__main__":
    main()
