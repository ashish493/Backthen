"""Query latency per model on an idle machine: Engine.search end to end, text encoder on CPU (the app default) and on
CUDA. usage: latency.py QUERIES.json LIB [LIB ...]   (prints one JSON line per library)"""
import json, statistics, sys, time

import numpy as np
import torch
from roamengine.engine import Engine

Q = [q["text"] for q in json.load(open(sys.argv[1], encoding="utf-8"))]
rng = np.random.default_rng(1)
sample = [Q[i] for i in rng.choice(len(Q), 205, replace=False)]
for lib in sys.argv[2:]:
    row = dict(library="/".join(lib.replace("\\", "/").split("/")[-2:]))
    for dev in ["cpu"] + (["cuda"] if torch.cuda.is_available() else []):
        e = Engine(lib, text_device=dev)
        wall = []
        for i, q in enumerate(sample):
            s = time.perf_counter()
            e.search(q, k=50)
            if i >= 5:
                wall.append((time.perf_counter() - s) * 1e3)
        row[dev] = dict(p50=round(statistics.median(wall), 1), p95=round(float(np.percentile(wall, 95)), 1))
        if dev == "cuda":
            row["cuda_text_vram_mb"] = round(torch.cuda.max_memory_allocated() / 2**20)
            torch.cuda.reset_peak_memory_stats()
        del e
        torch.cuda.empty_cache()
    print(json.dumps(row), flush=True)
