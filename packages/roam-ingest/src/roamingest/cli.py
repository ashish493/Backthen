"""roamingest command line. All console output is aggregate (counts, sizes, timings): never names or paths.

    roamingest inventory SRC... [--headers] [--json OUT]   discovery only (no decoding); --headers opens file headers
    roamingest run SRC... --out-dir DATASET [options]       ingest / resume (see `roamingest run -h`)
    roamingest status DATASET                               counts, coverage, last run
    roamingest verify DATASET [--rehash]                    schema and reference checks; --rehash re-reads all files
    roamingest hash DATASET                                 fill content_hash_full for assets that lack it
    roamingest migrate DATASET [--root R ...]               convert a v0 manifest (files/emb) to v1
"""
import argparse
import collections
import json
import os
import sys
import time

import numpy as np

from . import __version__


def _dump(obj, path=None):
    s = json.dumps(obj, indent=1, default=str)
    print(s)
    if path:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(s)


def _open(dataset, migrate=False):
    from .persistence import Manifest
    from .pipeline import manifest_path
    p = manifest_path(dataset) if os.path.isdir(dataset) else dataset
    if not os.path.exists(p):
        sys.exit("no manifest found")
    return Manifest(p, migrate=migrate)


# ---------------------------------------------------------------------------------------------- inventory
def cmd_inventory(a):
    from .discovery import discover
    t0 = time.perf_counter()
    items, counts = discover(a.roots, sidecars=True)
    t_disc = time.perf_counter() - t0
    by = collections.defaultdict(lambda: dict(files=0, bytes=0))
    for it in items:
        by[(it["kind"], it["ext"])]["files"] += 1
        by[(it["kind"], it["ext"])]["bytes"] += it["size"]
    sizes = {k: [it["size"] for it in items if it["kind"] == k] for k in ("photo", "video")}
    rep = dict(roamingest_version=__version__, discovery_s=round(t_disc, 1), media_files=len(items),
               media_bytes=sum(it["size"] for it in items), sidecar_files=counts["sidecars"],
               media_with_sidecar=sum(1 for it in items if it["sidecar_rel"]),
               other_files=dict(counts["other_ext"].most_common()), unreadable=counts["unreadable"],
               by_type={f"{k}:{e}": v for (k, e), v in sorted(by.items())},
               size_percentiles_mb={k: {p: round(float(np.percentile(v, p)) / 1e6, 2) for p in (50, 95, 99, 100)}
                                    for k, v in sizes.items() if v})
    if a.headers:
        rep["headers"] = _inventory_headers(items, a)
    _dump(rep, a.json)


def _inventory_headers(items, a):
    """Open each file's header (photos: first 256 KiB for EXIF; videos: container) -> metadata coverage."""
    from . import probing as P
    from .videos import container_meta, stream_rotation
    import av
    t0 = time.perf_counter()
    ph = collections.Counter()
    vd = collections.Counter()
    codecs, dur = collections.Counter(), []
    for it in items:
        try:
            if it["kind"] == "photo":
                with open(it["path"], "rb") as f:
                    head = f.read(256 << 10)
                e = P.exif_probe(head)
                ph["files"] += 1
                ph["exif_time"] += bool(e.get("capture_time_local"))
                ph["exif_time_with_offset"] += bool(e.get("capture_time_utc"))
                ph["exif_gps"] += e.get("lat") is not None
            else:
                with av.open(it["path"]) as c:
                    s = c.streams.video[0] if c.streams.video else None
                    vd["files"] += 1
                    if s is None:
                        vd["no_video_stream"] += 1
                        continue
                    codecs[s.codec_context.name] += 1
                    m = P.container_probe(container_meta(c))
                    vd["container_time"] += bool(m.get("capture_time_utc"))
                    vd["container_gps"] += m.get("lat") is not None
                    vd["rotated"] += bool(stream_rotation(s))
                    if c.duration:
                        dur.append(c.duration / 1e6)
        except Exception:  # noqa: BLE001
            (ph if it["kind"] == "photo" else vd)["unreadable"] += 1
    return dict(seconds=round(time.perf_counter() - t0, 1), photos=dict(ph), videos=dict(vd), video_codecs=dict(codecs),
                video_duration_s=dict(total=round(sum(dur)), p50=round(float(np.median(dur)), 1) if dur else None,
                                      max=round(max(dur)) if dur else None))


# ---------------------------------------------------------------------------------------------- run
def cmd_run(a):
    from .config import IngestConfig
    from .pipeline import Ingestor
    a.__dict__.pop("cmd", None)
    a.__dict__.pop("func", None)
    rep = Ingestor(IngestConfig.from_namespace(a)).run()
    return 0 if not rep.get("cancelled") else 2


# ---------------------------------------------------------------------------------------------- status
def status(m):
    con = m.con
    one = lambda q, *p: con.execute(q, p).fetchone()[0]  # noqa: E731
    files = {f"{k}:{s}": n for k, s, n in con.execute(
        "SELECT kind, status, COUNT(*) FROM asset_files GROUP BY kind, status ORDER BY kind, status")}
    assets = {k: n for k, n in con.execute("SELECT media_type, COUNT(*) FROM assets GROUP BY media_type")}
    cov = {}
    for col in ("capture_time_source", "gps_source"):
        cov[col] = {f"{k}:{v or 'none'}": n for k, v, n in con.execute(
            f"SELECT media_type, {col}, COUNT(*) FROM assets GROUP BY media_type, {col} ORDER BY 1, 2")}
    last = con.execute("SELECT run_id, started_at, finished_at, code_version, report_json FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
    lr = None
    if last:
        r = json.loads(last[4]) if last[4] else {}
        lr = dict(run_id=last[0], started_at=last[1], finished_at=last[2], code_version=last[3],
                  wall_s=r.get("wall_s"), files_processed=r.get("files_processed"), errors=r.get("errors"))
    return dict(meta=m.meta(), sources=one("SELECT COUNT(*) FROM sources"), files=files, assets=assets,
                duplicate_files=one("SELECT COUNT(*) - COUNT(DISTINCT asset_id) FROM asset_files WHERE asset_id IS NOT NULL"),
                frames=one("SELECT COUNT(*) FROM frames"), embeddings=one("SELECT COUNT(*) FROM embeddings"),
                models=[r[0] for r in con.execute("SELECT model_id FROM models")],
                full_hash_known=one("SELECT COUNT(*) FROM assets WHERE content_hash_full IS NOT NULL"),
                coverage=cov, runs=one("SELECT COUNT(*) FROM runs"), last_run=lr)


def cmd_status(a):
    m = _open(a.dataset)
    _dump(status(m))
    m.close()


# ---------------------------------------------------------------------------------------------- verify
def verify(m, rehash=False):
    """Consistency checks. Returns dict(checks={name: problems}, ok=bool)."""
    con = m.con
    q = lambda s: con.execute(s).fetchone()[0]  # noqa: E731
    from .persistence import SCHEMA_VERSION
    chk = collections.OrderedDict()
    chk["schema_version"] = 0 if m.meta().get("schema_version") == str(SCHEMA_VERSION) else 1
    chk["files_without_asset"] = q("SELECT COUNT(*) FROM asset_files f LEFT JOIN assets a ON a.asset_id=f.asset_id "
                                   "WHERE f.status NOT IN ('error') AND a.asset_id IS NULL")
    chk["assets_without_file"] = q("SELECT COUNT(*) FROM assets a WHERE NOT EXISTS "
                                   "(SELECT 1 FROM asset_files f WHERE f.asset_id=a.asset_id)")
    chk["frame_count_mismatch"] = q("SELECT COUNT(*) FROM assets a WHERE n_frames != "
                                    "(SELECT COUNT(*) FROM frames fr WHERE fr.asset_id=a.asset_id)")
    chk["frames_without_embedding"] = q("SELECT COUNT(*) FROM frames fr JOIN models mo WHERE NOT EXISTS (SELECT 1 FROM "
                                        "embeddings e WHERE e.asset_id=fr.asset_id AND e.frame_index=fr.frame_index "
                                        "AND e.model_id=mo.model_id)")
    chk["embeddings_without_frame"] = q("SELECT COUNT(*) FROM embeddings e WHERE NOT EXISTS (SELECT 1 FROM frames fr "
                                        "WHERE fr.asset_id=e.asset_id AND fr.frame_index=e.frame_index)")
    chk["done_files_without_embeddings"] = q("SELECT COUNT(*) FROM asset_files WHERE status='done' AND n_emb=0")
    dims = dict(con.execute("SELECT model_id, dim FROM models"))
    bad_vec = 0
    for mid, vec in con.execute("SELECT model_id, vector FROM embeddings"):
        v = np.frombuffer(vec, np.float16)
        if v.size != dims.get(mid) or abs(float(np.linalg.norm(v.astype(np.float32))) - 1) > 1e-2:
            bad_vec += 1
    chk["bad_vectors"] = bad_vec
    missing_thumbs = 0
    for (ref,) in con.execute("SELECT thumbnail_ref FROM assets WHERE thumbnail_ref IS NOT NULL"):
        missing_thumbs += not os.path.exists(os.path.join(m.dataset_dir, ref))
    chk["missing_thumbnails"] = missing_thumbs
    info = None
    if rehash:
        info = _rehash(m, all_assets=True)
        for k in ("hash_changed_files", "hash_unreadable", "partial_id_collisions"):
            chk[k] = info.pop(k)
    out = dict(checks=dict(chk), ok=all(v == 0 for v in chk.values()))
    if info:
        out["rehash"] = info
    return out


def cmd_verify(a):
    m = _open(a.dataset)
    r = verify(m, a.rehash)
    _dump(r)
    m.close()
    return 0 if r["ok"] else 1


# ---------------------------------------------------------------------------------------------- hash
def _rehash(m, all_assets=False):
    """Read files (one sequential reader) and fill or check content_hash_full. -> aggregate counts."""
    from .identity import cid_from_file, full_hash_file
    con = m.con
    where = "" if all_assets else "WHERE a.content_hash_full IS NULL"
    rows = con.execute(f"SELECT a.asset_id, a.content_hash_full, s.root_prefix || f.rel_path, f.size FROM assets a "
                       f"JOIN asset_files f ON f.asset_id=a.asset_id AND f.status!='missing' "
                       f"JOIN sources s ON s.source_id=f.source_id {where} ORDER BY f.source_id, f.rel_path").fetchall()
    seen, filled, changed, unreadable, collisions = {}, 0, 0, 0, 0
    t0, nbytes = time.perf_counter(), 0
    con.execute("BEGIN")
    for asset_id, known, path, size in rows:
        try:
            if cid_from_file(path, size) != asset_id:
                changed += 1                              # file changed since ingest: rerun `run` to update it
                continue
            h = full_hash_file(path)
            nbytes += size
        except OSError:
            unreadable += 1
            continue
        if asset_id in seen and seen[asset_id] != h:
            collisions += 1                               # two different contents share a partial ID
            continue
        seen[asset_id] = h
        if known is None:
            con.execute("UPDATE assets SET content_hash_full=? WHERE asset_id=?", (h, asset_id))
            filled += 1
        elif known != h:
            changed += 1
    con.execute("COMMIT")
    dt = time.perf_counter() - t0
    return dict(hash_files_read=len(rows) - unreadable - changed, hash_filled=filled, hash_changed_files=changed,
                hash_unreadable=unreadable, partial_id_collisions=collisions, hash_seconds=round(dt, 1),
                hash_mb_s=round(nbytes / 1e6 / dt, 1) if dt else None)


def cmd_hash(a):
    from .system import set_power
    set_power("off" if a.fast else "default", "normal")
    m = _open(a.dataset)
    r = _rehash(m)
    _dump(r)
    m.close()


# ---------------------------------------------------------------------------------------------- migrate
def cmd_migrate(a):
    from .persistence import Manifest, detect_version
    import sqlite3
    p = os.path.join(a.dataset, "manifest.db") if os.path.isdir(a.dataset) else a.dataset
    con = sqlite3.connect(p)
    v = detect_version(con)
    con.close()
    if v != 0:
        sys.exit(f"not a v0 manifest (detected: {v})")
    m = Manifest(p, migrate=True, roots=a.root or None)
    r = dict(m.migration or {})
    r.pop("backup", None)
    _dump(dict(migrated=r, verify=verify(m)))
    m.close()


def main(argv=None):
    from .config import add_run_arguments
    ap = argparse.ArgumentParser(prog="roamingest", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inventory")
    p.add_argument("roots", nargs="+")
    p.add_argument("--headers", action="store_true")
    p.add_argument("--json")
    p.set_defaults(func=cmd_inventory)
    p = add_run_arguments(sub.add_parser("run"))
    p.set_defaults(func=cmd_run)
    for name, fn in (("status", cmd_status), ("verify", cmd_verify), ("hash", cmd_hash), ("migrate", cmd_migrate)):
        p = sub.add_parser(name)
        p.add_argument("dataset")
        if name == "verify":
            p.add_argument("--rehash", action="store_true")
        if name == "hash":
            p.add_argument("--fast", action="store_true", help="opt out of power throttling (default: background-friendly)")
        if name == "migrate":
            p.add_argument("--root", action="append", help="source root(s) of the v0 paths (default: common parent)")
        p.set_defaults(func=fn)
    a = ap.parse_args(argv)
    return a.func(a) or 0


if __name__ == "__main__":
    sys.exit(main())
