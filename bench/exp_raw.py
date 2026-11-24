"""NEF RAW: embedded JPEG preview vs LibRaw demosaic (aggregate output only; reads sources read-only).

    python -m bench.exp_raw <root> [--sample 100] [--out results/raw_preview.json]

Gate (fixed before running, ADR-002 style): top-10 neighbour overlap between preview embeddings and full-demosaic
embeddings must be within 2 points of the control (half-size demosaic vs full demosaic) on the same sample.
Also reports, for every NEF: whether a preview exists, its size, and per-path decode times.
"""
import argparse
import io
import json
import os
import time

import numpy as np
import rawpy
from PIL import Image

from roamingest import transforms as T
from roamingest.embedder import ClipEmbedder
from roamingest.images import decode_photo


def knn(e, k=10):
    s = e @ e.T
    np.fill_diagonal(s, -9)
    return np.argsort(-s, axis=1)[:, :k]


def overlap(a, b):
    return float(np.mean([len(set(x) & set(y)) / len(x) for x, y in zip(a, b)]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--sample", type=int, default=100)
    ap.add_argument("--out")
    a = ap.parse_args()
    paths = sorted(os.path.join(dp, f) for dp, _, fs in os.walk(a.root) for f in fs if f.lower().endswith(".nef"))
    st = dict(files=len(paths), preview_ok=0, preview_demosaic_fallback=0, errors=0, preview_long_edge=[], t_preview_ms=[],
              bytes=0)
    for p in paths:
        try:
            data = open(p, "rb").read()
            st["bytes"] += len(data)
            t = time.perf_counter()
            im, orig, s, strat = decode_photo(data, ".nef")
            st["t_preview_ms"].append((time.perf_counter() - t) * 1000)
            st["preview_ok" if strat == "raw_preview" else "preview_demosaic_fallback"] += 1
            st["preview_long_edge"].append(max(orig or im.size))
        except Exception:  # noqa: BLE001
            st["errors"] += 1
    rng = np.random.default_rng(0)
    pick = sorted(rng.choice(len(paths), size=min(a.sample, len(paths)), replace=False))
    emb = ClipEmbedder(batch=16)
    rows = {"preview": [], "half": [], "full": []}
    t_half, t_full = [], []
    for i in pick:
        data = open(paths[i], "rb").read()
        im, orig, s, _ = decode_photo(data, ".nef")
        rows["preview"].append(T.to_model_input(im, orig, s) if orig else T.to_model_input(im))
        with rawpy.imread(io.BytesIO(data)) as raw:
            t = time.perf_counter()
            half = raw.postprocess(half_size=True, use_camera_wb=True, output_bps=8)
            t_half.append((time.perf_counter() - t) * 1000)
            t = time.perf_counter()
            full = raw.postprocess(use_camera_wb=True, output_bps=8)
            t_full.append((time.perf_counter() - t) * 1000)
        rows["half"].append(T.to_model_input(Image.fromarray(half)))
        rows["full"].append(T.to_model_input(Image.fromarray(full)))
    E = {}
    for k, v in rows.items():
        E[k] = np.concatenate([emb.encode(v[j:j + 16]) for j in range(0, len(v), 16)]).astype(np.float32)
    nn = {k: knn(v) for k, v in E.items()}
    res = dict(
        all_files=dict(files=st["files"], gib=round(st["bytes"] / 2**30, 2), preview_ok=st["preview_ok"],
                       demosaic_fallback=st["preview_demosaic_fallback"], errors=st["errors"],
                       preview_long_edge_px=dict(p5=int(np.percentile(st["preview_long_edge"], 5)),
                                                 p50=int(np.median(st["preview_long_edge"]))),
                       preview_decode_ms_p50=round(float(np.median(st["t_preview_ms"])), 1)),
        sample=len(pick),
        demosaic_ms_p50=dict(half=round(float(np.median(t_half))), full=round(float(np.median(t_full)))),
        cosine_vs_full=dict(preview=dict(p5=round(float(np.percentile((E["preview"] * E["full"]).sum(1), 5)), 3),
                                         p50=round(float(np.median((E["preview"] * E["full"]).sum(1))), 3)),
                            half=dict(p5=round(float(np.percentile((E["half"] * E["full"]).sum(1), 5)), 3),
                                      p50=round(float(np.median((E["half"] * E["full"]).sum(1))), 3))),
        knn10_overlap_vs_full=dict(preview=round(overlap(nn["preview"], nn["full"]), 3),
                                   control_half=round(overlap(nn["half"], nn["full"]), 3)))
    res["gate_pass"] = res["knn10_overlap_vs_full"]["preview"] >= res["knn10_overlap_vs_full"]["control_half"] - 0.02
    print(json.dumps(res, indent=1))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
