"""Phase 1 sampling matrix: speed, I/O and quality of each video sampling policy (aggregate output only).

    python -m bench.run_sampling --set A [--repeats 3]          # set A, internal SSD
    python -m bench.run_sampling --set B [--repeats 1]          # set B folder, USB hard drive (E:, read-only)

Every policy gets the SAME decoder access (--nvdec-seek, hash routing, 2 CPU + 2 NVDEC workers), so only the
sampling policy changes. All runs are cold. The first repeat of each policy keeps its manifest so quality can
be scored against the exact reference with bench.compare_sampling; for set B (personal archive) those
manifests are deleted as soon as the aggregate scores are written.
Output: results/sampling_<set>.json (aggregates per policy), results/ingest_S<set>_<policy>_r<i>.json
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

from .compare_sampling import compare, load, summarise

ROOTS = {"A": os.environ.get("ROAM_SET_A", r"Dataset\set-A"), "B": os.environ.get("ROAM_SET_B", r"E:\set-B")}
POLICIES = {
    "exact": [],                                              # current default (seq / auto)
    "exact_prefetch": ["--video-prefetch-mb", "256", "--read-budget-mb", "768"],   # best so far on the USB drive
    "smart": ["--video-strategy", "smart"],
    "smart_prefetch": ["--video-strategy", "smart", "--video-prefetch-mb", "256", "--read-budget-mb", "768"],
    "adaptive0.5_prefetch": ["--video-sampling", "adaptive", "--adaptive-tau", "0.5",
                             "--video-prefetch-mb", "256", "--read-budget-mb", "768"],
    "keyframe": ["--video-sampling", "keyframe"],
    "adaptive0.5": ["--video-sampling", "adaptive", "--adaptive-tau", "0.5"],
    "adaptive1": ["--video-sampling", "adaptive", "--adaptive-tau", "1.0"],
    "adaptive2": ["--video-sampling", "adaptive", "--adaptive-tau", "2.0"],
}
COMMON = ["--cold", "--nvdec-seek"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["A", "B"], required=True)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--policies", nargs="+", default=list(POLICIES))
    ap.add_argument("--rep-offset", type=int, default=0, help="first repeat index (keeps earlier reports)")
    ap.add_argument("--tag", default="", help="suffix for results/sampling_<set><tag>.json")
    a = ap.parse_args()
    root = ROOTS[a.set]
    extra = ["--storage", "hdd"] if a.set == "B" else []
    out = {}
    keep = {}
    for rep in range(a.rep_offset, a.rep_offset + a.repeats):
        for pol in a.policies:
            name = f"S{a.set}_{pol}_r{rep}"
            d = os.path.join("out", "sampling", name)
            shutil.rmtree(d, ignore_errors=True)
            report = os.path.join("results", f"ingest_{name}.json")
            cmd = [sys.executable, "-m", "bench.ingest", root, "--out-dir", d, "--tag", name, "--report", report,
                   *COMMON, *extra, *POLICIES[pol]]
            p = subprocess.run(cmd, capture_output=True, text=True)
            ok = p.returncode == 0 and os.path.exists(report)
            r = json.load(open(report)) if ok else {}
            v = r.get("kinds", {}).get("video", {})
            row = dict(rc=p.returncode, wall_s=r.get("wall_s"), video_embeddings=v.get("embeddings"),
                       frames_decoded=v.get("frames_decoded_total"), seeks=v.get("seeks_total"), probes=v.get("probes_total"),
                       video_decode_s=v.get("stage_s", {}).get("t_decode_ms"), process_read_gb=round(r.get("process_read_bytes", 0) / 1e9, 3),
                       disk_mb_s=r.get("disk_mb_s"), cpu=r.get("cpu_mean"), nvdec=r.get("nvdec_util_mean"),
                       peak_rss_mib=r.get("peak_rss_mib"), errors=r.get("errors"))
            out.setdefault(pol, {"runs": []})["runs"].append(row)
            print(f"{name}: rc={p.returncode} wall={row['wall_s']} decoded={row['frames_decoded']} seeks={row['seeks']} "
                  f"read={row['process_read_gb']}GB", flush=True)
            if rep == a.rep_offset and ok:
                keep[pol] = d
            else:
                shutil.rmtree(d, ignore_errors=True)
    # quality vs exact reference (first repeat)
    if "exact" in keep:
        er, meta = load(os.path.join(keep["exact"], "manifest.sqlite"))
        for pol, d in keep.items():
            if pol == "exact":
                continue
            et, _ = load(os.path.join(d, "manifest.sqlite"))
            out[pol]["quality"] = summarise(compare(er, et, meta), f"{a.set}_{pol}_vs_exact")
            print(pol, "gate_pass", out[pol]["quality"]["gate_pass"], flush=True)
    if a.set == "B":                                          # personal archive: delete derived outputs now
        for d in keep.values():
            shutil.rmtree(d, ignore_errors=True)
    json.dump(out, open(os.path.join("results", f"sampling_{a.set}{a.tag}.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
