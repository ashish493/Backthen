"""Unattended benchmark driver for Steps 6-8 (aggregate output only).

    python -m bench.overnight [--only name,name] [--skip-existing]

Runs bench.ingest / bench.exp_gpu_jpeg sequentially (never concurrently, so timings are clean).
Every run writes an aggregate JSON to results/ingest_<name>.json. Runs on personal archives (E:) delete their
derived outputs (thumbnails, manifest with paths) right after the aggregate report is saved.
Console/log text is redacted: any drive-letter path is replaced by <path>; only the last lines of stderr are kept.
A failing run is logged and the driver continues. Progress: results/overnight_progress.json
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

PY = sys.executable
A = os.environ.get("ROAM_SET_A", r"Dataset\set-A")
B = os.environ.get("ROAM_SET_B", r"E:\set-B")
OUT = os.path.join("out", "overnight")
PROGRESS = os.path.join("results", "overnight_progress.json")
SERIAL = ["--photo-decode", "full", "--photo-workers", "1", "--readers", "1", "--video-cpu", "1",
          "--video-nvdec", "0", "--video-strategy", "seq"]
HDD = ["--storage", "hdd"]


def redact(t):
    return re.sub(r"[A-Za-z]:\\[^\s'\"]*", "<path>", t)


def ingest(name, roots, extra, cleanup, timeout=4 * 3600):
    d = os.path.join(OUT, name)
    return dict(name=name, kind="ingest", cleanup=d if cleanup else None, timeout=timeout,
                cmd=[PY, "-m", "bench.ingest", *roots, "--out-dir", d, "--tag", name,
                     "--report", os.path.join("results", f"ingest_{name}.json"), *extra],
                report=os.path.join("results", f"ingest_{name}.json"))


def plan():
    p = []
    # ---- Step 6a: set A (NVMe), minimal-parallel baseline vs optimised, cold and warm
    p.append(ingest("A_serial_cold", [A], SERIAL + ["--cold"], False))
    p.append(ingest("A_serial_warm", [A], SERIAL, False))
    for i in range(3):
        p.append(ingest(f"A_opt_cold_r{i}", [A], ["--cold"], False))
    for i in range(3):
        p.append(ingest(f"A_opt_warm_r{i}", [A], [], False))
    # ---- Step 7: GPU JPEG
    p.append(dict(name="S7_gpu_jpeg", kind="exp", cleanup=None, timeout=3600, report="results/exp_gpu_jpeg.jsonl",
                  cmd=[PY, "-m", "bench.exp_gpu_jpeg", A, "--repeat", "8", "--repeats", "3"]))
    # ---- Step 6b: set B archive on the USB hard drive, cold
    p.append(ingest("B_hdd_mixed_r0", [B], HDD + ["--cold"], True))
    p.append(ingest("B_hdd_phased_r0", [B], HDD + ["--cold", "--schedule", "phased"], True))
    p.append(ingest("B_hdd_readers4", [B], HDD + ["--cold", "--readers", "4"], True))
    p.append(ingest("B_serial", [B], HDD + SERIAL + ["--cold"], True, timeout=3 * 3600))
    p.append(ingest("B_hdd_mixed_r1", [B], HDD + ["--cold"], True))
    p.append(ingest("B_hdd_phased_r1", [B], HDD + ["--cold", "--schedule", "phased"], True))
    # ---- Step 6c: both sets together
    p.append(ingest("AB_hdd_mixed", [A, B], HDD + ["--cold"], True))
    # ---- follow-up: single sequential reader also feeds small videos (decode from memory)
    PF = HDD + ["--cold", "--video-prefetch-mb", "256", "--read-budget-mb", "768"]
    p.append(ingest("B_hdd_prefetch_r0", [B], PF, True))
    p.append(ingest("B_hdd_mixed_r2", [B], HDD + ["--cold"], True))
    p.append(ingest("B_hdd_prefetch_r1", [B], PF, True))
    p.append(ingest("B_hdd_prefetch_r2", [B], PF, True))
    return p


def load_progress():
    return json.load(open(PROGRESS)) if os.path.exists(PROGRESS) else {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--skip-existing", action="store_true")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else None
    prog = load_progress()
    os.makedirs("results", exist_ok=True)
    for r in plan():
        if only and r["name"] not in only:
            continue
        if a.skip_existing and prog.get(r["name"], {}).get("status") == "ok":
            continue
        if r["cleanup"]:
            shutil.rmtree(r["cleanup"], ignore_errors=True)
        free_gb = shutil.disk_usage(".").free / 2**30
        if free_gb < 16:
            prog[r["name"]] = dict(status="skipped_low_disk", free_gb=round(free_gb, 1))
            json.dump(prog, open(PROGRESS, "w"), indent=1)
            continue
        t0 = time.time()
        try:
            p = subprocess.run(r["cmd"], capture_output=True, text=True, timeout=r["timeout"])
            status = "ok" if p.returncode == 0 else f"rc={p.returncode}"
            tail = redact("\n".join((p.stderr or "").strip().splitlines()[-8:]))
            if r["kind"] == "exp" and p.returncode == 0:
                open(os.path.join("results", f"{r['name']}_console.txt"), "w", encoding="utf-8").write(redact(p.stdout))
        except subprocess.TimeoutExpired:
            status, tail = "timeout", ""
        prog[r["name"]] = dict(status=status, seconds=round(time.time() - t0), stderr_tail=tail if status != "ok" else "",
                               finished=time.strftime("%Y-%m-%d %H:%M:%S"))
        json.dump(prog, open(PROGRESS, "w"), indent=1)
        print(f"{r['name']}: {status} in {time.time() - t0:.0f}s", flush=True)
        if r["cleanup"]:
            shutil.rmtree(r["cleanup"], ignore_errors=True)


if __name__ == "__main__":
    main()
