"""Stage-1 acceptance on a large archive (aggregate output only; sources are only read).

    python -m bench.validate_archive <root> --out out/archive355 [--kill-after 34000] [--reference] [--skip-inventory]

Steps (each recorded in results/archive_validation.json):
  inventory  roamingest inventory --headers (probe only)
  run1       full ingest, hard-killed after --kill-after committed files
  resume     same command again: must finish the rest without redoing committed files
  rerun      unchanged archive: must process 0 files
  verify     manifest consistency checks
  reference  (--reference) an uninterrupted run into <out>_ref; compared asset by asset with the resumed manifest
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

RES = os.path.join("results", "archive_validation.json")


def cli(*args, log=None):
    t = time.perf_counter()
    with open(log, "w", encoding="utf-8") if log else open(os.devnull, "w") as f:
        p = subprocess.run([sys.executable, "-m", "roamingest.cli", *args], stdout=subprocess.PIPE, stderr=f, text=True)
    return p.returncode, p.stdout, round(time.perf_counter() - t, 1)


def free_gb(path):
    return round(shutil.disk_usage(path).free / 2**30, 1)


def dir_gb(path):
    n = 0
    for dp, _, fs in os.walk(path):
        for f in fs:
            try:
                n += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return round(n / 2**30, 2)


def summarise_report(out):
    r = json.load(open(os.path.join(out, "report.json"), encoding="utf-8"))
    keep = ("wall_s", "files_processed", "files_resumed_skipped", "files_moved", "files_missing", "unaccounted_files",
            "files_per_s", "mb_per_s", "errors", "cpu_mean", "gpu_util_mean", "nvdec_util_mean", "peak_rss_mib",
            "peak_vram_mib", "gpu_max_temp_c", "disk_mb_s", "process_read_bytes", "reader_blocked_on_byte_budget_s",
            "thumbnails_bytes", "gpu", "throttle_off_ok")
    s = {k: r.get(k) for k in keep}
    for kind in ("photo", "video"):
        k = r["kinds"][kind]
        s[kind] = {x: k.get(x) for x in ("files", "bytes", "ok", "errors", "truncated", "timeouts", "embeddings", "by_ext",
                                         "by_decoder", "by_strategy", "by_codec", "capture_time_source", "gps_source",
                                         "stage_s", "latency_ms", "seeks_total", "frames_decoded_total", "rotated")
                   if x in k}
    s["config"] = {k: r["config"].get(k) for k in ("storage", "readers", "video_strategy", "route", "video_prefetch_mb",
                                                   "read_budget_mb", "throttle", "power_plan", "model_id", "device")}
    return s


def compare(a_db, b_db):
    con = sqlite3.connect(a_db)
    con.execute("ATTACH ? AS b", (b_db,))
    q = lambda s: con.execute(s).fetchone()[0]  # noqa: E731
    r = dict(
        files_a=q("SELECT COUNT(*) FROM main.asset_files"), files_b=q("SELECT COUNT(*) FROM b.asset_files"),
        file_rows_differ=q("SELECT COUNT(*) FROM main.asset_files x LEFT JOIN b.asset_files y USING(source_id, rel_path) "
                           "WHERE y.rel_path IS NULL OR x.asset_id IS NOT y.asset_id OR x.status != y.status "
                           "OR x.n_emb != y.n_emb"),
        frames_a=q("SELECT COUNT(*) FROM main.frames"), frames_b=q("SELECT COUNT(*) FROM b.frames"),
        frames_differ=q("SELECT COUNT(*) FROM main.frames x LEFT JOIN b.frames y USING(asset_id, frame_index) "
                        "WHERE y.asset_id IS NULL OR x.timestamp_s IS NOT y.timestamp_s OR x.sampling_method != y.sampling_method"),
        embeddings_differ=q("SELECT COUNT(*) FROM main.embeddings x LEFT JOIN b.embeddings y "
                            "USING(asset_id, frame_index, model_id) WHERE y.vector IS NULL OR x.vector != y.vector"),
        assets_metadata_differ=q("SELECT COUNT(*) FROM main.assets x LEFT JOIN b.assets y USING(asset_id) WHERE "
                                 "y.asset_id IS NULL OR x.capture_time_utc IS NOT y.capture_time_utc OR x.lat IS NOT y.lat "
                                 "OR x.width IS NOT y.width OR x.n_frames != y.n_frames"))
    # differing embeddings by decoder (timing-dependent NVDEC fallback would show up here)
    r["embeddings_differ_by_media"] = dict(con.execute(
        "SELECT a.media_type, COUNT(*) FROM main.embeddings x JOIN main.assets a USING(asset_id) LEFT JOIN b.embeddings y "
        "USING(asset_id, frame_index, model_id) WHERE y.vector IS NULL OR x.vector != y.vector GROUP BY 1").fetchall())
    con.close()
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--out", required=True)
    ap.add_argument("--kill-after", type=int, default=34000)
    ap.add_argument("--reference", action="store_true")
    ap.add_argument("--skip-inventory", action="store_true")
    ap.add_argument("--only-reference", action="store_true")
    a = ap.parse_args()
    res = json.load(open(RES, encoding="utf-8")) if os.path.exists(RES) else {}

    def save():
        with open(RES, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)

    out, logs = a.out, a.out + "_logs"
    os.makedirs(logs, exist_ok=True)
    run_args = ["run", a.root, "--storage", "hdd", "--quiet", "--no-timeline"]
    res["disk_free_gb_start"] = free_gb(os.path.dirname(os.path.abspath(out)) or ".")
    if not a.only_reference:
        if not a.skip_inventory:
            rc, txt, dt = cli("inventory", a.root, "--headers", log=os.path.join(logs, "inventory.log"))
            res["inventory"] = dict(rc=rc, seconds=dt, **(json.loads(txt) if rc == 0 else {}))
            save()
        rc, _, dt = cli(*run_args, "--out-dir", out, "--kill-after-files", str(a.kill_after), log=os.path.join(logs, "run1.log"))
        con = sqlite3.connect(os.path.join(out, "manifest.sqlite"))
        committed = con.execute("SELECT COUNT(*) FROM asset_files").fetchone()[0]
        con.close()
        res["run1_killed"] = dict(rc=rc, seconds=dt, committed_files=committed)
        save()
        rc, _, dt = cli(*run_args, "--out-dir", out, log=os.path.join(logs, "resume.log"))
        res["resume"] = dict(rc=rc, seconds=dt, **summarise_report(out))
        save()
        rc, _, dt = cli(*run_args, "--out-dir", out, log=os.path.join(logs, "rerun.log"))
        r = json.load(open(os.path.join(out, "report.json"), encoding="utf-8"))
        res["rerun_unchanged"] = dict(rc=rc, seconds=dt, files_processed=r["files_processed"],
                                      files_resumed_skipped=r["files_resumed_skipped"], files_missing=r["files_missing"])
        save()
        rc, txt, dt = cli("verify", out)
        res["verify"] = dict(rc=rc, seconds=dt, **json.loads(txt))
        rc, txt, _ = cli("status", out)
        st = json.loads(txt)
        res["status"] = {k: st[k] for k in ("files", "assets", "duplicate_files", "frames", "embeddings", "full_hash_known",
                                            "coverage")}
        res["output_gb"] = dir_gb(out)
        res["disk_free_gb_after_run"] = free_gb(out)
        save()
    if a.reference or a.only_reference:
        ref = out + "_ref"
        rc, _, dt = cli(*run_args, "--out-dir", ref, log=os.path.join(logs, "reference.log"))
        res["reference"] = dict(rc=rc, seconds=dt, **summarise_report(ref))
        res["resumed_vs_reference"] = compare(os.path.join(out, "manifest.sqlite"), os.path.join(ref, "manifest.sqlite"))
        save()
    print(json.dumps({k: v for k, v in res.items() if k != "inventory"}, indent=1, default=str))


if __name__ == "__main__":
    main()
