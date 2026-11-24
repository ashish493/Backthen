"""Same metrics as evaluate.py, but captions are encoded in batches (GPU when available) and all queries are scored at
once with the product's rule (a video's score = its best frame). A sample is checked against Engine.search, and the
product latency (CPU text encoder, as in the app) is measured on a sample.

usage: evaluate_fast.py NAME LIBRARY QUERIES.json [--out FILE] [--check 300]
"""
import argparse, json, os, sqlite3, statistics, time

import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("name"); ap.add_argument("library"); ap.add_argument("queries")
ap.add_argument("--out"); ap.add_argument("--check", type=int, default=300)
a = ap.parse_args()

import torch
from roamengine.engine import Engine

Q = json.load(open(a.queries, encoding="utf-8"))
dev = "cuda" if torch.cuda.is_available() else "cpu"
e = Engine(a.library, text_device=dev)
c = e.cat
con = sqlite3.connect(f"file:{os.path.join(a.library, 'manifest.sqlite')}?mode=ro", uri=True)
f2a = {os.path.basename(p): aid for p, aid in con.execute("SELECT rel_path, asset_id FROM asset_files")}
assert all(q["file"] in f2a for q in Q)

t0 = time.perf_counter()
texts = [q["text"] for q in Q]
T = np.concatenate([e.text.encode(texts[i:i + 256]) for i in range(0, len(texts), 256)])
enc_s = time.perf_counter() - t0

starts = c.f_start[c.emb_idx]
pos = np.searchsorted(c.emb_idx, np.array([c.index[f2a[q["file"]]] for q in Q]))
ranks = np.empty(len(Q))
mean_ranks = np.empty(len(Q))
M = np.add.reduceat(c.vectors, starts, axis=0)
M /= np.linalg.norm(M, axis=1, keepdims=True)
for b in range(0, len(Q), 512):
    Tb = T[b:b + 512]
    best = np.maximum.reduceat(c.vectors @ Tb.T, starts, axis=0).T          # (q, assets) best frame
    idx = np.arange(len(Tb))
    ranks[b:b + 512] = (best > best[idx, pos[b:b + 512]][:, None]).sum(1) + 1
    mp = Tb @ M.T
    mean_ranks[b:b + 512] = (mp > mp[idx, pos[b:b + 512]][:, None]).sum(1) + 1


def metrics(r):
    out = {f"R@{k}": float((r <= k).mean()) for k in (1, 5, 10, 50)}
    out["MRR"] = float((1.0 / r).mean())
    out["nDCG@10"] = float(np.where(r <= 10, 1.0 / np.log2(r + 1), 0).mean())
    out["median_rank"] = float(np.median(r)); out["mean_rank"] = float(r.mean())
    return out


out = dict(benchmark=a.name, model_id=c.model_id, dim=int(c.vectors.shape[1]), assets=len(c.asset_ids),
           queries=len(Q), product=metrics(ranks), encode_all_s=round(enc_s, 1))
if len(starts) and np.diff(np.append(starts, len(c.vectors))).max() > 1:
    out["video_mean_pool"] = metrics(mean_ranks)

# product path, as the app runs it: CPU text encoder, Engine.search
ep = Engine(a.library)
rng = np.random.default_rng(0)
sample = rng.choice(len(Q), min(a.check, len(Q)), replace=False)
agree, wall, enc = 0, [], []
for i in sample:
    s = time.perf_counter()
    res = ep.search(Q[i]["text"], k=10)
    wall.append((time.perf_counter() - s) * 1e3); enc.append(res["timing_ms"]["encode"])
    ids = [it["asset_id"] for it in res["items"]]
    tgt = f2a[Q[i]["file"]]
    r = ids.index(tgt) + 1 if tgt in ids else 11
    agree += (r == ranks[i]) or (r == 11 and ranks[i] > 10)
out["check_vs_engine_search"] = f"{agree}/{len(sample)} identical ranks"
out["latency_ms_cpu"] = dict(p50=statistics.median(wall[5:]), p95=float(np.percentile(wall[5:], 95)),
                             encode_p50=statistics.median(enc[5:]))
print(json.dumps(out, indent=1))
if a.out:
    json.dump(out, open(a.out, "w"), indent=1)
