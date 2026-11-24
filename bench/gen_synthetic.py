"""Synthetic datasets for Hypothesis A (small-file overhead) and format coverage.

Usage:
  python -m bench.gen_synthetic small  <out_dir> --n 10000 [--depth 3] [--seed 0]
  python -m bench.gen_synthetic bundle <out_dir> --n 10000     # same bytes, few large files
  python -m bench.gen_synthetic formats <src_root> <out_dir> --n 20   # PNG/WebP from real JPEGs

Deterministic given --seed. `small` writes N JPEGs (~50-300 KB) spread over a directory tree;
`bundle` writes the *same total bytes* as ~20 big files so only file-count differs.
Re-use of a small set of encoded payloads (with a per-file unique EXIF comment-free header
tweak) keeps generation fast while hashes stay unique.
"""
import argparse
import io
import os
import random

import numpy as np
from PIL import Image


def _payloads(rng, k=32):
    """k distinct JPEG byte strings of varying size (50-300 KB)."""
    out = []
    for i in range(k):
        side = rng.choice([640, 800, 1024, 1280])
        base = np.random.default_rng(rng.randrange(1 << 30))
        small = base.integers(0, 256, (side // 16, side // 16, 3), dtype=np.uint8)
        im = Image.fromarray(small).resize((side, int(side * 0.75)), Image.BICUBIC)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=rng.choice([70, 80, 90]))
        out.append(buf.getvalue())
    return out


def gen_small(out, n, depth, seed):
    rng = random.Random(seed)
    pays = _payloads(rng)
    total = 0
    for i in range(n):
        parts = [f"d{(i >> (3 * j)) % 8}" for j in range(depth)]
        d = os.path.join(out, *parts)
        os.makedirs(d, exist_ok=True)
        data = bytearray(pays[i % len(pays)])
        data += i.to_bytes(8, "little")             # unique trailer -> unique hash, still valid JPEG
        with open(os.path.join(d, f"img_{i:06d}.jpg"), "wb") as f:
            f.write(data)
        total += len(data)
    print(f"wrote {n} files, {total/2**20:.0f} MiB -> {out}")
    return total


def gen_bundle(out, n, seed, files=20):
    rng = random.Random(seed)
    pays = _payloads(rng)
    os.makedirs(out, exist_ok=True)
    per = n // files
    for k in range(files):
        with open(os.path.join(out, f"bundle_{k:03d}.bin"), "wb") as f:
            for i in range(per):
                f.write(pays[(k * per + i) % len(pays)])
    print(f"wrote {files} bundle files -> {out}")


def gen_replicate(src, out, copies):
    """Copy every real JPEG `copies` times with a unique 8-byte trailer after the JPEG end marker
    (decoders ignore it) so content hashes differ but pixels are identical. Source is only read."""
    jpgs = sorted(os.path.join(dp, f) for dp, _, fs in os.walk(src) for f in fs
                  if f.lower().endswith((".jpg", ".jpeg")))
    total, n = 0, 0
    for k in range(copies):
        d = os.path.join(out, f"copy{k:02d}")
        os.makedirs(d, exist_ok=True)
        for i, p in enumerate(jpgs):
            with open(p, "rb") as f:
                data = f.read()
            data += (k * 100000 + i).to_bytes(8, "little")
            with open(os.path.join(d, f"{i:04d}_{os.path.basename(p)}"), "wb") as f:
                f.write(data)
            total += len(data)
            n += 1
    print(f"wrote {n} files, {total/2**30:.2f} GiB -> {out}")


def gen_formats(src, out, n):
    os.makedirs(out, exist_ok=True)
    jpgs = [os.path.join(dp, f) for dp, _, fs in os.walk(src) for f in fs
            if f.lower().endswith((".jpg", ".jpeg"))]
    jpgs.sort()
    for p in jpgs[:n]:
        base = os.path.splitext(os.path.basename(p))[0]
        with Image.open(p) as im:
            im.load()
            im.save(os.path.join(out, base + ".png"))
            im.save(os.path.join(out, base + ".webp"), quality=85)
    print(f"wrote {min(n, len(jpgs))} PNG + WebP pairs -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["small", "bundle", "formats", "replicate"])
    ap.add_argument("a")
    ap.add_argument("b", nargs="?")
    ap.add_argument("--n", type=int, default=10000)
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    x = ap.parse_args()
    if x.mode == "small":
        gen_small(x.a, x.n, x.depth, x.seed)
    elif x.mode == "bundle":
        gen_bundle(x.a, x.n, x.seed)
    elif x.mode == "replicate":
        gen_replicate(x.a, x.b, x.n)
    else:
        gen_formats(x.a, x.b, x.n)
