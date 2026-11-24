"""M1: dataset inventory. Probes every file with PyAV / Pillow and writes a CSV.

Usage: python -m bench.probe_dataset <dataset_root> <out.csv>
Records codec, profile, resolution, fps, duration, rotation, keyframe interval (GOP) for
videos and format/size/EXIF presence for images. Also flags byte-identical duplicates.
"""
import csv
import hashlib
import os
import sys
import time

import av
from PIL import Image

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VID_EXT = {".mp4", ".mov"}


def walk(root):
    for dp, _, fns in os.walk(root):
        for fn in sorted(fns):
            yield os.path.join(dp, fn)


def sha1(path, bufsize=1 << 20):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while b := f.read(bufsize):
            h.update(b)
    return h.hexdigest()


def probe_video(path):
    t0 = time.perf_counter()
    with av.open(path) as c:
        s = c.streams.video[0]
        open_ms = (time.perf_counter() - t0) * 1000
        rot = 0
        try:
            for sd in s.side_data if hasattr(s, "side_data") else []:
                rot = getattr(sd, "rotation", rot)
        except Exception:  # noqa: BLE001
            pass
        rot = rot or int(s.metadata.get("rotate", 0) or 0)
        dur = float(c.duration / av.time_base) if c.duration else None
        # keyframe interval: scan packet flags only (no decode)
        pts_key, n_pkt = [], 0
        for p in c.demux(s):
            if p.dts is None:
                continue
            n_pkt += 1
            if p.is_keyframe:
                pts_key.append(n_pkt)
        gop = (n_pkt / len(pts_key)) if pts_key else None
        fps = float(s.average_rate) if s.average_rate else None
        return dict(
            kind="video", codec=s.codec_context.name, profile=s.codec_context.profile,
            width=s.width, height=s.height, fps=fps, duration_s=dur, frames=n_pkt,
            rotation=rot, avg_gop_frames=round(gop, 1) if gop else None,
            bit_rate=c.bit_rate, pix_fmt=s.codec_context.pix_fmt, open_ms=round(open_ms, 2),
            n_audio=len(c.streams.audio),
        )


def probe_image(path):
    with Image.open(path) as im:
        exif = im.getexif() if hasattr(im, "getexif") else {}
        return dict(kind="image", codec=im.format, width=im.width, height=im.height,
                    mode=im.mode, has_exif=bool(exif), exif_orientation=exif.get(274))


def main(root, out_csv):
    rows, seen = [], {}
    for p in walk(root):
        ext = os.path.splitext(p)[1].lower()
        row = dict(path=os.path.relpath(p, root), size_bytes=os.path.getsize(p), ext=ext)
        try:
            if ext in VID_EXT:
                row.update(probe_video(p))
            elif ext in IMG_EXT:
                row.update(probe_image(p))
            else:
                row["kind"] = "other"
        except Exception as e:  # noqa: BLE001
            row["error"] = repr(e)
        row["sha1"] = sha1(p)
        row["duplicate_of"] = seen.setdefault(row["sha1"], row["path"])
        if row["duplicate_of"] == row["path"]:
            row["duplicate_of"] = ""
        rows.append(row)
        print(f"{row['path']}: {row.get('kind')} {row.get('codec')} {row.get('width')}x{row.get('height')}")
    cols = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("path", "kind"), k))
    os.makedirs(os.path.dirname(os.path.abspath(out_csv)), exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows -> {out_csv}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
