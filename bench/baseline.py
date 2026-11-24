"""M2: single-threaded reference pipeline with per-stage timing.

Usage:
  python -m bench.baseline <dataset_root> --regime cold|warm [--limit N] [--no-model]
         [--out results/baseline_<regime>.jsonl] [--thumbs out/thumbs]

Stages are run per file in order. S5/S6 are skipped with --no-model (CPU-only baseline).
Each (file, stage) row goes to JSONL via instrument.Recorder; a resource sampler runs alongside.
"""
import argparse
import os
import time
import uuid

from . import stages as S
from .instrument import Recorder, ResourceSampler


def load_model(device="cuda"):
    """Fixed stand-in embedding model; replaced by CLIP ViT-B/32 once weights are chosen."""
    import torch
    import torchvision
    m = torchvision.models.resnet50(weights=None)   # random weights: compute cost only
    m.fc = torch.nn.Identity()
    return m.eval().half().to(device)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--regime", default="warm", choices=["cold", "warm"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--no-model", action="store_true")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--out")
    ap.add_argument("--thumbs", default="out/thumbs")
    a = ap.parse_args()

    run_id = f"baseline_{a.regime}_{uuid.uuid4().hex[:6]}"
    out = a.out or f"results/{run_id}.jsonl"
    rec = Recorder(out, run_id=run_id, cache_regime=a.regime, backend="pillow+pyav")

    torch = model = None
    sync = None
    if not a.no_model:
        import torch
        model = load_model()
        sync = torch.cuda.synchronize
        with torch.no_grad():                      # warm-up (excluded from stats)
            model(torch.zeros(a.batch, 3, S.MODEL_SIZE, S.MODEL_SIZE, device="cuda").half())
        sync()

    wall0 = time.perf_counter()
    with ResourceSampler(out.replace(".jsonl", "_res.jsonl")) as rs:
        with rec.span("S0_discover", n_files=-1) as sp:
            files = S.discover_scandir(a.root)
            sp.set(n_files=len(files))
        if a.limit:
            files = files[: a.limit]

        pending = []                                # model-input arrays awaiting a batch

        def flush():
            if not pending:
                return
            import numpy as np
            with rec.span("S5_transfer", sync=sync, items=len(pending)):
                x = torch.from_numpy(np.stack(pending)).to("cuda").half()
            with rec.span("S6_inference", sync=sync, items=len(pending)):
                with torch.no_grad():
                    model(x)
            pending.clear()

        for path, size, _ in files:
            name = os.path.relpath(path, a.root)
            is_img = os.path.splitext(path)[1].lower() in S.IMG_EXT
            with rec.span("S1_hash", file=name, bytes_in=size):
                S.hash_full(path)
            with rec.span("S2_metadata", file=name):
                S.metadata(path)
            if is_img:
                with rec.span("S3_decode", file=name, bytes_in=size, kind="image"):
                    frames = [S.decode_image(path)]
            else:
                with rec.span("S3_decode", file=name, bytes_in=size, kind="video") as sp:
                    frames, st = S.decode_video_sequential(path)
                    sp.set(**st)
            with rec.span("S4_transform", file=name, frames=len(frames)):
                thumb = S.make_thumbnail(frames[0])
                arrs = [S.to_model_input(f) for f in frames]
            with rec.span("S7_write", file=name):
                S.write_thumbnail(thumb, os.path.join(a.thumbs, name + ".jpg"))
            if model:
                for arr in arrs:
                    pending.append(arr)
                    if len(pending) >= a.batch:
                        flush()
        if model:
            flush()
    wall = time.perf_counter() - wall0
    rec.log(stage="E2E_wall", active_ns=int(wall * 1e9), n_files=len(files),
            peak_rss=rs.peak_rss, peak_vram=rs.peak_vram)
    rec.close()
    print(f"{run_id}: {len(files)} files in {wall:.1f}s  peakRSS={rs.peak_rss/2**20:.0f}MiB "
          f"peakVRAM={rs.peak_vram/2**20:.0f}MiB -> {out}")


if __name__ == "__main__":
    main()
