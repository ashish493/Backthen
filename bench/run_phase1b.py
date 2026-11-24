"""Phase 1, steps 4-6: codec routing, FFmpeg thread sweep, power configurations (aggregate output only).

    python -m bench.run_phase1b threads [--repeats 3]   # set A, SSD: --video-threads 1/2/4/8 vs auto
    python -m bench.run_phase1b codec   [--repeats 2]   # set B folder, USB drive: hash vs codec routing
    python -m bench.run_phase1b power   [--repeats 3]   # set A: configs A-D (switches the Windows plan!)

Worker configuration is fixed in every group (12 photo workers, 2 CPU + 2 NVDEC video workers, cold cache).
The power group switches Windows to Balanced for configs A-C and ALWAYS restores High Performance afterwards.
Writes results/phase1b_<group>.json and results/ingest_P<group>_<config>_r<i>.json.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

A = os.environ.get("ROAM_SET_A", r"Dataset\set-A")
B = os.environ.get("ROAM_SET_B", r"E:\set-B")
BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"
HIGH_PERF = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
PREFETCH = ["--video-prefetch-mb", "256", "--read-budget-mb", "768"]
SMART = ["--video-strategy", "smart", "--nvdec-seek"]          # new default sampling (exact frames, least decoding)

GROUPS = {
    "threads": [(f"t{t}", A, [*SMART, "--video-threads", str(t)], None) for t in (0, 1, 2, 4, 8)],
    "codec": [("hash", B, ["--storage", "hdd", *PREFETCH, *SMART], None),
              ("codec", B, ["--storage", "hdd", *PREFETCH, *SMART, "--route", "codec"], None),
              ("hash_off", B, ["--storage", "hdd", *PREFETCH, *SMART, "--throttle", "off"], None),
              ("codec_off", B, ["--storage", "hdd", *PREFETCH, *SMART, "--route", "codec", "--throttle", "off"], None)],
    "power": [("A_balanced_default_normal", A, [*SMART], BALANCED),
              ("B_balanced_off_normal", A, [*SMART, "--throttle", "off"], BALANCED),
              ("C_balanced_off_high", A, [*SMART, "--throttle", "off", "--priority", "high"], BALANCED),
              ("D_highperf_default_normal", A, [*SMART], HIGH_PERF),
              ("E_highperf_off_normal", A, [*SMART, "--throttle", "off"], HIGH_PERF)],
}


def set_plan(guid):
    subprocess.run(["powercfg", "/setactive", guid], check=True, capture_output=True)
    time.sleep(5)                                              # let the platform settle


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("group", choices=list(GROUPS))
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--rep-offset", type=int, default=0, help="first repeat index (keeps earlier reports)")
    ap.add_argument("--configs", nargs="+", help="only these config names")
    a = ap.parse_args()
    out = {}
    try:
        for rep in range(a.rep_offset, a.rep_offset + a.repeats):
            for name, root, extra, plan in GROUPS[a.group]:
                if a.configs and name not in a.configs:
                    continue
                if plan:
                    set_plan(plan)
                tag = f"P{a.group}_{name}_r{rep}"
                d = os.path.join("out", "phase1b", tag)
                shutil.rmtree(d, ignore_errors=True)
                rpt = os.path.join("results", f"ingest_{tag}.json")
                p = subprocess.run([sys.executable, "-m", "bench.ingest", root, "--out-dir", d, "--tag", tag,
                                    "--report", rpt, "--cold", *extra], capture_output=True, text=True)
                shutil.rmtree(d, ignore_errors=True)           # never keep derived outputs from these runs
                r = json.load(open(rpt)) if p.returncode == 0 and os.path.exists(rpt) else {}
                v = r.get("kinds", {}).get("video", {})
                row = dict(rc=p.returncode, wall_s=r.get("wall_s"), cpu=r.get("cpu_mean"), nvdec=r.get("nvdec_util_mean"),
                           video_decode_s=v.get("stage_s", {}).get("t_decode_ms"), decoders=v.get("by_decoder"),
                           plan=r.get("config", {}).get("power_plan"), throttle_off_ok=r.get("throttle_off_ok"),
                           priority=r.get("priority_set"), peak_rss_mib=r.get("peak_rss_mib"), errors=r.get("errors"),
                           sm_clock_max=r.get("gpu_sm_clock_mhz_max"))
                out.setdefault(name, []).append(row)
                print(f"{tag}: rc={p.returncode} wall={row['wall_s']} cpu={row['cpu']} plan={row['plan']}", flush=True)
    finally:
        if a.group == "power":
            set_plan(HIGH_PERF)
            print("power plan restored:", subprocess.run(["powercfg", "/getactivescheme"], capture_output=True, text=True).stdout.strip())
    json.dump(out, open(os.path.join("results", f"phase1b_{a.group}_from{a.rep_offset}.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
