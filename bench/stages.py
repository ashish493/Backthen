"""Reference (CPU, single-thread-friendly) implementations of stages S0-S4, S7.

These define the *reference backend* used for correctness checks (BENCHMARK_PLAN §7).
Fixed pipeline parameters live here so every experiment changes only one variable.
"""
import os
import json

import av
import numpy as np
import xxhash
from PIL import Image, ImageOps

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VID_EXT = {".mp4", ".mov"}
from roamingest.transforms import FRAME_EVERY_S, MAX_FRAMES, MODEL_SIZE, THUMB_LONG_EDGE  # noqa: F401


# ---- S0 discover --------------------------------------------------------------------
def discover_scandir(root):
    """Recursive os.scandir; returns [(path, size, mtime_ns)] for media files."""
    out, stack = [], [root]
    while stack:
        d = stack.pop()
        with os.scandir(d) as it:
            for e in it:
                if e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
                elif os.path.splitext(e.name)[1].lower() in IMG_EXT | VID_EXT:
                    st = e.stat()
                    out.append((e.path, st.st_size, st.st_mtime_ns))
    out.sort()
    return out


def discover_walk(root):
    out = []
    for dp, _, fns in os.walk(root):
        for fn in fns:
            if os.path.splitext(fn)[1].lower() in IMG_EXT | VID_EXT:
                p = os.path.join(dp, fn)
                st = os.stat(p)
                out.append((p, st.st_size, st.st_mtime_ns))
    out.sort()
    return out


# ---- S1 change detection ------------------------------------------------------------
def hash_full(path, bufsize=1 << 20):
    h = xxhash.xxh3_128()
    with open(path, "rb") as f:
        while b := f.read(bufsize):
            h.update(b)
    return h.hexdigest()


def hash_partial(path, size, n=1 << 16):
    """size + first/last 64 KiB. Fast but can miss mid-file edits (documented trade-off)."""
    h = xxhash.xxh3_128()
    h.update(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(n))
        if size > 2 * n:
            f.seek(-n, os.SEEK_END)
            h.update(f.read(n))
    return h.hexdigest()


# ---- S2 metadata --------------------------------------------------------------------
def metadata_image(path):
    with Image.open(path) as im:
        ex = im.getexif()
        return dict(kind="image", format=im.format, width=im.width, height=im.height,
                    orientation=ex.get(274), exif_datetime=ex.get(306) or ex.get(36867))


def metadata_video(path):
    with av.open(path) as c:
        s = c.streams.video[0]
        return dict(kind="video", codec=s.codec_context.name, width=s.width, height=s.height,
                    fps=float(s.average_rate) if s.average_rate else None,
                    duration_s=round(float(c.duration / av.time_base), 3) if c.duration else None)


def metadata(path):
    ext = os.path.splitext(path)[1].lower()
    return metadata_image(path) if ext in IMG_EXT else metadata_video(path)


# ---- S3 decode: reference definitions live in roamingest.transforms (shared with the package)
from roamingest.transforms import (  # noqa: E402,F401
    _MEAN, _ROTATED, _STD, _normalise, decode_image, decode_image_draft, make_thumbnail, to_model_input)


def sample_times(duration_s):
    if not duration_s:
        return [0.0]
    n = min(MAX_FRAMES, max(1, int(duration_s // FRAME_EVERY_S) + 1))
    return [round(i * duration_s / n, 3) for i in range(n)]


def decode_video_sequential(path):
    """Decode sequentially, keep the first frame at/after each target timestamp.

    Returns (list[PIL RGB], stats). stats: frames_decoded, frames_returned, open_ms.
    """
    import time
    t0 = time.perf_counter()
    with av.open(path) as c:
        s = c.streams.video[0]
        s.thread_type = "AUTO"
        open_ms = (time.perf_counter() - t0) * 1000
        dur = float(c.duration / av.time_base) if c.duration else 0
        targets = sample_times(dur)
        ti, frames, decoded = 0, [], 0
        for fr in c.decode(s):
            decoded += 1
            t = float(fr.pts * s.time_base) if fr.pts is not None else 0.0
            if ti < len(targets) and t >= targets[ti]:
                frames.append(fr.to_image())     # RGB PIL
                ti += 1
                if ti >= len(targets):
                    break
    return frames, dict(frames_decoded=decoded, frames_returned=len(frames), open_ms=open_ms)


# ---- S4 transform: make_thumbnail, to_model_input are imported above


# ---- S7 write -----------------------------------------------------------------------
def write_thumbnail(img, out_path):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.save(out_path, "JPEG", quality=85)
    return os.path.getsize(out_path)


def write_manifest_row(f, row):
    f.write(json.dumps(row, sort_keys=True) + "\n")
