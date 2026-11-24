"""Photo-path optimisation: parallel workers (Step 1) x reduced-size JPEG decode (Step 2).

Usage: python -m bench.exp_images <dataset_root> [--workers 1 2 4 8 10 16] [--repeats 3]
                                  [--out results/exp_images.jsonl]

Measures S3 decode + S4 transform (thumbnail + 224 px model input) per photo over all photos.
Modes: full  = reference (stages.decode_image + make_thumbnail + to_model_input)
       draft = stages.decode_image_draft + make_thumbnail(orig_size) + to_model_input
Correctness (outside timed regions):
  full, N workers : thumbnails and model inputs byte-identical to the serial reference
  draft           : thumbnail sizes identical; PSNR of model input / thumbnail; CLIP ViT-B/32
                    image-embedding cosine vs reference (gate: min cosine >= 0.999)
"""
import argparse
import json
import os
import random
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import psutil

from . import stages as S
from .exp_d_nvdec import psnr


def work_full(p):
    im = S.decode_image(p)
    return S.make_thumbnail(im), S.to_model_input(im)


def work_draft(p):
    im, orig, s = S.decode_image_draft(p)
    return S.make_thumbnail(im, orig, s), S.to_model_input(im, orig, s)


def work_draft_m2(p):
    im, orig, s = S.decode_image_draft(p, margin=2)
    return S.make_thumbnail(im, orig, s), S.to_model_input(im, orig, s)


def work_full_rgap(p):
    """Control: reference decode + Pillow's documented reducing_gap=3.0 resize speed-up."""
    im = S.decode_image(p)
    return S.make_thumbnail(im, reducing_gap=3.0), S.to_model_input(im, reducing_gap=3.0)


MODES = {"full": work_full, "draft": work_draft, "draft_m2": work_draft_m2, "full_rgap": work_full_rgap}


def knn_overlap(e_a, e_b, k=5):
    """Mean overlap@k of each photo's k nearest neighbours (self excluded): index built with e_a vs e_b."""
    def nn(e):
        s = e @ e.T
        np.fill_diagonal(s, -np.inf)
        return np.argsort(-s, axis=1)[:, :k]
    a, b = nn(e_a), nn(e_b)
    return float(np.mean([len(set(x) & set(y)) / k for x, y in zip(a, b)]))


def run(mode, paths, workers):
    fn = MODES[mode]
    psutil.cpu_percent()
    t = time.perf_counter()
    if workers == 1:
        out = [fn(p) for p in paths]
    else:
        with ThreadPoolExecutor(workers) as ex:
            out = list(ex.map(fn, paths))
    wall = time.perf_counter() - t
    return out, wall, psutil.cpu_percent()


def thumb_psnr(a, b):
    a = np.asarray(a, dtype=np.float32) / 255
    b = np.asarray(b, dtype=np.float32) / 255
    m = float(np.mean((a - b) ** 2))
    return 99.0 if m == 0 else 10 * np.log10(1 / m)


def clip_embed(arrs, batch=32):
    import open_clip
    import torch
    model, _, _ = open_clip.create_model_and_transforms("ViT-B-32", pretrained="laion2b_s34b_b79k")
    model = model.eval().half().cuda()
    embs = []
    with torch.no_grad():
        for i in range(0, len(arrs), batch):
            x = torch.from_numpy(np.stack(arrs[i:i + batch])).cuda().half()
            e = model.encode_image(x).float()
            embs.append(torch.nn.functional.normalize(e, dim=-1).cpu().numpy())
    return np.concatenate(embs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4, 8, 10, 16])
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", default="results/exp_images.jsonl")
    a = ap.parse_args()

    paths = sorted(os.path.join(dp, f) for dp, _, fs in os.walk(a.root) for f in fs
                   if os.path.splitext(f)[1].lower() in S.IMG_EXT)
    print(f"{len(paths)} photos")
    ref, _, _ = run("full", paths, 1)                    # serial reference (also warms the file cache)
    run("draft", paths[:8], 1)                           # warm-up

    out = open(a.out, "w", encoding="utf-8")
    rng = random.Random(0)
    configs = [(m, w) for m in MODES for w in a.workers]
    first = {}
    for rep in range(a.repeats):
        rng.shuffle(configs)
        for mode, w in configs:
            res, wall, cpu = run(mode, paths, w)
            row = dict(kind="throughput", mode=mode, workers=w, rep=rep, n=len(paths), wall_s=wall,
                       ms_per_photo=1000 * wall / len(paths), photos_per_s=len(paths) / wall, cpu_pct=cpu)
            if mode == "full":
                row["identical_to_ref"] = all(
                    r[0].tobytes() == x[0].tobytes() and np.array_equal(r[1], x[1]) for r, x in zip(ref, res))
            first.setdefault(mode, res)
            out.write(json.dumps(row) + "\n")
            out.flush()
            print(f"rep{rep} {mode:5s} workers={w:2d} {row['ms_per_photo']:6.1f} ms/photo "
                  f"{row['photos_per_s']:6.1f} photos/s cpu={cpu:3.0f}% identical={row.get('identical_to_ref')}")

    # quality vs reference, for each non-reference mode; also the CLIP noise floor (ref vs ref)
    e_ref = clip_embed([r[1] for r in ref])
    e_ref2 = clip_embed([r[1] for r in ref])
    floor = (e_ref * e_ref2).sum(1)
    print(f"CLIP determinism floor (ref vs ref): min cos {floor.min():.6f}")
    for mode in ("draft", "draft_m2", "full_rgap"):
        dr = first[mode]
        sizes_ok = all(r[0].size == d[0].size for r, d in zip(ref, dr))
        pm = [psnr(d[1], r[1]) for r, d in zip(ref, dr)]
        pt = [thumb_psnr(d[0], r[0]) for r, d in zip(ref, dr) if r[0].size == d[0].size]
        e_m = clip_embed([d[1] for d in dr])
        cos = (e_ref * e_m).sum(1)
        worst = int(np.argmin(cos))
        q = dict(kind="quality", mode=mode, thumb_sizes_identical=sizes_ok,
                 model_psnr_min=min(pm), model_psnr_median=float(np.median(pm)),
                 thumb_psnr_min=min(pt) if pt else None, thumb_psnr_median=float(np.median(pt)) if pt else None,
                 clip_cos_min=float(cos.min()), clip_cos_p05=float(np.percentile(cos, 5)),
                 clip_cos_median=float(np.median(cos)), clip_cos_below_0999=int((cos < 0.999).sum()),
                 clip_floor_min=float(floor.min()), knn5_overlap=knn_overlap(e_ref, e_m, 5),
                 knn1_same=knn_overlap(e_ref, e_m, 1), worst_file=os.path.relpath(paths[worst], a.root))
        out.write(json.dumps(q) + "\n")
        print(json.dumps(q, indent=1))
    out.close()


if __name__ == "__main__":
    main()
