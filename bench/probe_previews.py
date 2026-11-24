"""Which photos carry an embedded preview big enough to skip the full decode? (aggregate output only)

    python -m bench.probe_previews <root> [<root> ...] [--head-kb 256]

Reads only the first --head-kb of each JPEG (EXIF + MPF index live there) and HEIC thumbnails via pillow-heif.
Reports, per format: how many have an EXIF thumbnail, an MPF (multi-picture) preview, or a HEIF thumbnail,
bucketed by short side (>= 224 px is enough for the CLIP model input; >= 512 px long side for our thumbnail).
"""
import argparse
import collections
import io
import os
import sys

import piexif
from PIL import Image

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:  # noqa: BLE001
    pillow_heif = None


def bucket(w, h):
    s, l = min(w, h), max(w, h)
    if s >= 224 and l >= 512:
        return "enough for model input AND 512 px thumbnail"
    if s >= 224:
        return "enough for model input only"
    return "too small (<224 px short side)"


def jpeg_previews(head):
    out = []
    try:
        ex = piexif.load(head)
        t = ex.get("thumbnail")
        if t:
            with Image.open(io.BytesIO(t)) as im:
                out.append(("exif_thumbnail", im.size))
    except Exception:  # noqa: BLE001
        pass
    try:
        with Image.open(io.BytesIO(head)) as im:
            mp = getattr(im, "mpinfo", None) or {}
            for e in (mp.get(0xB002) or [])[1:]:          # entry 0 is the primary image
                out.append(("mpf_preview", e.get("Attribute", {}).get("MPType", "?"), e.get("Size")))
    except Exception:  # noqa: BLE001
        pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--head-kb", type=int, default=256)
    a = ap.parse_args()
    stats = collections.Counter()
    sizes = collections.Counter()
    for root in a.roots:
        for dp, _, fs in os.walk(root):
            for f in fs:
                ext = os.path.splitext(f)[1].lower()
                p = os.path.join(dp, f)
                if ext in (".jpg", ".jpeg"):
                    stats["jpeg files"] += 1
                    with open(p, "rb") as fh:
                        head = fh.read(a.head_kb * 1024)
                    pv = jpeg_previews(head)
                    if not pv:
                        stats["jpeg: no embedded preview"] += 1
                    for x in pv:
                        if x[0] == "exif_thumbnail":
                            stats["jpeg: EXIF thumbnail"] += 1
                            sizes[f"jpeg EXIF thumbnail: {bucket(*x[1])}"] += 1
                            sizes[f"jpeg EXIF thumbnail size {x[1][0]}x{x[1][1]}"] += 1
                        else:
                            stats["jpeg: MPF preview"] += 1
                            sizes[f"jpeg MPF preview type {x[1]} (bytes ~{(x[2] or 0)//1000} KB)"] += 1
                elif ext in (".heic", ".heif") and pillow_heif:
                    stats["heic files"] += 1
                    try:
                        hf = pillow_heif.open_heif(p)
                        th = [t.size for t in getattr(hf, "thumbnails", [])]
                        if not th:
                            stats["heic: no thumbnail"] += 1
                        for w, h in th:
                            stats["heic: thumbnail"] += 1
                            sizes[f"heic thumbnail: {bucket(w, h)}"] += 1
                            sizes[f"heic thumbnail size {w}x{h}"] += 1
                    except Exception as e:  # noqa: BLE001
                        stats[f"heic: error {type(e).__name__}"] += 1
    for k, v in sorted(stats.items()):
        print(f"{k:45s} {v}")
    print()
    for k, v in sorted(sizes.items(), key=lambda kv: -kv[1])[:25]:
        print(f"{v:6d}  {k}")


if __name__ == "__main__":
    sys.exit(main())
