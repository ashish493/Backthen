"""Hypothesis D decomposition: pure decode throughput with and without device->host download.

Usage: python -m bench.exp_d_ffmpeg <dataset_root> <inventory.csv> [--per-class 4] [--repeats 3]
                                    [--out results/exp_d_ffmpeg.jsonl]

Uses the FFmpeg CLI from imageio-ffmpeg, decoding whole clips to the null muxer (no encode, no
colour conversion, no Python in the loop). Modes:
  cpu            : software decode, -threads 0
  gpu_resident   : -hwaccel cuda -hwaccel_output_format cuda  (frames stay in VRAM, never downloaded)
  gpu_download   : as above + hwdownload,format=nv12          (explicit device->host copy of every frame)
fps = frames (from inventory) / wall time; wall time includes ~process start-up (same for all modes).
"""
import argparse
import csv
import json
import os
import statistics
import subprocess
import time

import imageio_ffmpeg

# (input options placed before -i, filter options placed after -i)
MODES = {
    "cpu": (["-threads", "0"], []),
    "gpu_resident": (["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"], []),
    "gpu_download": (["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"], ["-vf", "hwdownload,format=nv12"]),
}


def run(ff, mode, path):
    pre, post = MODES[mode]
    cmd = [ff, "-hide_banner", "-loglevel", "error", "-nostdin", *pre, "-i", path, *post,
           "-an", "-f", "null", "-"]
    t = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, text=True)
    return time.perf_counter() - t, p.returncode, p.stderr.strip()[:200]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("inventory")
    ap.add_argument("--per-class", type=int, default=4)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", default="results/exp_d_ffmpeg.jsonl")
    a = ap.parse_args()
    ff = imageio_ffmpeg.get_ffmpeg_exe()

    rows = [r for r in csv.DictReader(open(a.inventory, encoding="utf-8"))
            if r["kind"] == "video" and r["codec"] == "h264" and int(r["frames"]) >= 300 and not r["duplicate_of"]]
    classes = {"4K": [r for r in rows if max(int(r["width"]), int(r["height"])) >= 3840],
               "1080p": [r for r in rows if max(int(r["width"]), int(r["height"])) == 1920]}
    out = open(a.out, "w", encoding="utf-8")
    run(ff, "gpu_resident", os.path.join(a.root, rows[0]["path"]))       # warm-up (driver/NVDEC load)
    for cls, rs in classes.items():
        rs = sorted(rs, key=lambda r: int(r["size_bytes"]))
        step = max(1, len(rs) // a.per_class)
        for r in rs[::step][: a.per_class]:
            path, frames = os.path.join(a.root, r["path"]), int(r["frames"])
            for mode in MODES:
                ts = []
                for _ in range(a.repeats):
                    dt, rc, err = run(ff, mode, path)
                    if rc:
                        print("FAIL", mode, r["path"], err)
                        break
                    ts.append(dt)
                if ts:
                    m = statistics.median(ts)
                    out.write(json.dumps(dict(cls=cls, file=r["path"], w=r["width"], h=r["height"], frames=frames,
                                              mode=mode, wall_s=m, fps=frames / m)) + "\n")
                    out.flush()
                    print(f"{cls:5s} {r['path'][:40]:40s} {mode:13s} {frames:5d}f {m:6.2f}s {frames/m:6.0f} fps")
    out.close()


if __name__ == "__main__":
    main()
