"""Text -> media retrieval metrics on a public benchmark, through the product's own search (roamengine.Engine.search).

usage: evaluate.py NAME LIBRARY QUERIES.json [--reference MEDIA_DIR] [--video]
  --reference : also score a plain OpenCLIP baseline (official preprocessing, full decode) on the same files, to see
                what the pipeline's fast decode costs.  --video: also score mean-of-frames pooling vs the product's max.
"""
import argparse, json, math, os, sqlite3, statistics, sys, time

import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("name"); ap.add_argument("library"); ap.add_argument("queries")
ap.add_argument("--reference"); ap.add_argument("--video", action="store_true")
a = ap.parse_args()

from roamengine.engine import Engine

Q = json.load(open(a.queries, encoding="utf-8"))
e = Engine(a.library)
con = sqlite3.connect(f"file:{os.path.join(a.library, 'manifest.sqlite')}?mode=ro", uri=True)
f2a = {os.path.basename(p): aid for p, aid in con.execute("SELECT rel_path, asset_id FROM asset_files")}
missing = sorted({q["file"] for q in Q} - set(f2a))
n_assets = len(e.cat.asset_ids)
print(f"{a.name}: {n_assets} assets, {len(Q)} queries, {len(missing)} relevant files not in the index", flush=True)


def metrics(ranks, n_items):
    r = np.array([x if x is not None else n_items + 1 for x in ranks], dtype=float)
    out = {f"R@{k}": float((r <= k).mean()) for k in (1, 5, 10, 50)}
    out["MRR"] = float((1.0 / r).mean())
    out["nDCG@10"] = float(np.where(r <= 10, 1.0 / np.log2(r + 1), 0).mean())   # one relevant item per query
    out["median_rank"] = float(np.median(r))
    out["mean_rank"] = float(r.mean())
    return out


# 1) the product path: Engine.search, exactly as the app calls it
K = 50
ranks, enc, rank_ms, wall = [], [], [], []
t0 = time.perf_counter()
for i, q in enumerate(Q):
    s = time.perf_counter()
    res = e.search(q["text"], k=K)
    wall.append((time.perf_counter() - s) * 1e3)
    enc.append(res["timing_ms"]["encode"]); rank_ms.append(res["timing_ms"]["rank"])
    ids = [it["asset_id"] for it in res["items"]]
    tgt = f2a.get(q["file"])
    ranks.append(ids.index(tgt) + 1 if tgt in ids else None)
    if i % 2000 == 0:
        print(f"  {i}/{len(Q)}", flush=True)
# ranks beyond K: exact rank from the full score vector (same vectors, same text encoder)
full = {}
over = [i for i, r in enumerate(ranks) if r is None and f2a.get(Q[i]["file"])]
if over:
    c = e.cat
    starts = c.f_start[c.emb_idx]
    for i in over:
        qv = e.text_vector(Q[i]["text"])
        best = np.maximum.reduceat(c.vectors @ qv, starts)
        tgt = c.index[f2a[Q[i]["file"]]]
        pos = int(np.flatnonzero(c.emb_idx == tgt)[0])
        ranks[i] = int((best > best[pos]).sum()) + 1
out = dict(benchmark=a.name, assets=n_assets, queries=len(Q), missing=len(missing),
           product=metrics(ranks, n_assets),
           latency_ms=dict(p50=statistics.median(wall), p95=float(np.percentile(wall, 95)),
                           encode_p50=statistics.median(enc), rank_p50=statistics.median(rank_ms)),
           eval_wall_s=round(time.perf_counter() - t0, 1))
print(json.dumps(out["product"], indent=1), flush=True)

c = e.cat
T = None
if a.video or a.reference:
    T = np.stack([e.text_vector(q["text"]) for q in Q])          # (q, dim) cached per text
    rel = np.array([c.index[f2a[q["file"]]] for q in Q])

# 2) video aggregation: mean of frame vectors instead of the product's best frame
if a.video:
    starts = c.f_start[c.emb_idx]
    M = np.add.reduceat(c.vectors, starts, axis=0)
    M /= np.linalg.norm(M, axis=1, keepdims=True)
    S = T @ M.T                                                    # (q, assets)
    pos = np.searchsorted(c.emb_idx, rel)
    r = (S > S[np.arange(len(Q)), pos][:, None]).sum(1) + 1
    out["video_mean_pool"] = metrics(r.tolist(), n_assets)
    fr = np.diff(np.append(starts, len(c.vectors)))
    out["frames_per_video"] = dict(median=float(np.median(fr)), min=int(fr.min()), max=int(fr.max()))

# 3) plain OpenCLIP reference on the original files (official preprocessing, full decode, fp32)
if a.reference:
    import open_clip, torch
    from PIL import Image
    from roamengine.text import parse_model_id
    arch, pre = parse_model_id(c.model_id)
    model, _, prep = open_clip.create_model_and_transforms(arch, pretrained=pre)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = model.eval().to(dev)
    files = sorted({q["file"] for q in Q})
    V = []
    with torch.no_grad():
        for b in range(0, len(files), 64):
            x = torch.stack([prep(Image.open(os.path.join(a.reference, f)).convert("RGB")) for f in files[b:b + 64]])
            V.append(torch.nn.functional.normalize(model.encode_image(x.to(dev)).float(), dim=-1).cpu().numpy())
    V = np.concatenate(V)
    fi = {f: i for i, f in enumerate(files)}
    S = T @ V.T
    tgt = np.array([fi[q["file"]] for q in Q])
    r = (S > S[np.arange(len(Q)), tgt][:, None]).sum(1) + 1
    out["reference_openclip"] = metrics(r.tolist(), len(files))
    # agreement of the pipeline's stored vectors with the reference vectors for the same files
    pv = np.stack([c.vectors[c.f_start[c.index[f2a[f]]]] for f in files])
    cos = (pv * V).sum(1)
    out["pipeline_vs_reference_cosine"] = dict(median=float(np.median(cos)), p1=float(np.percentile(cos, 1)), min=float(cos.min()))

print(json.dumps({k: v for k, v in out.items() if k != "product"}, indent=1))
json.dump(out, open(os.path.join(os.path.dirname(a.library), f"{a.name}_metrics.json"), "w"), indent=1)
