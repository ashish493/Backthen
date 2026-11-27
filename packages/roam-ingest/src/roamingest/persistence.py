"""Manifest v1 (SQLite, WAL): open/create, v0 -> v1 migration, per-file transactional writes.

A file is 'done' only in the same transaction as its frames and embeddings (crash-safe, ADR-004).
"""
import datetime as dt
import json
import os
import re
import sqlite3
from importlib import resources

SCHEMA_VERSION = 1
SCHEMA_SQL = resources.files(__package__).joinpath("manifest_v1.sql").read_text(encoding="utf-8")
V0_MODEL = dict(model_id="open_clip/ViT-B-32/laion2b_s34b_b79k/fp16", name="ViT-B-32", pretrained="laion2b_s34b_b79k",
                dim=512, dtype="float16", preprocess="short side 224 bicubic, centre crop 224, CLIP mean/std")

ASSET_COLS = ["asset_id", "content_hash_full", "size", "media_type", "format", "width", "height", "orientation",
              "rotation", "capture_time_utc", "capture_time_local", "tz_offset_min", "capture_time_source", "lat",
              "lon", "alt", "gps_source", "codec", "duration_s", "fps", "sampling_policy", "thumbnail_ref", "n_frames",
              "first_run_id", "last_run_id"]
FILE_COLS = ["source_id", "rel_path", "size", "mtime_ns", "kind", "ext", "asset_id", "status", "error_code", "n_emb",
             "sidecar_rel", "last_run_id"]
STAT_COLS = ["source_id", "rel_path", "run_id", "decoder", "strategy", "frames_decoded", "seeks", "probes",
             "t_read_ms", "t_decode_ms", "t_xform_ms", "t_write_ms"]


def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def _tables(con):
    return {r[0]: r[1] for r in con.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table','view')")}


def detect_version(con):
    t = _tables(con)
    if "meta" in t:
        row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(row[0]) if row else None
    if t.get("files") == "table":
        return 0
    return None                                   # empty database


def _root_prefix(root):
    return os.path.join(root, "")


class Manifest:
    def __init__(self, path, migrate=True, roots=None):
        self.path = path
        self.dataset_dir = os.path.dirname(os.path.abspath(path))
        self.migration = None
        self.dropped = []                                 # asset_ids deleted as orphans (read by the ingest writer)
        self.con = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")
        v = detect_version(self.con)
        if v == 0:
            if not migrate:
                raise RuntimeError("manifest is v0; run `roamingest migrate` first")
            self.migration = self.migrate_v0(roots)
        elif v is None:
            self._create()
        elif v > SCHEMA_VERSION:
            raise RuntimeError(f"manifest schema v{v} is newer than this RoamIngest (v{SCHEMA_VERSION})")

    # ---------------------------------------------------------------- schema
    def _create(self):
        self.con.executescript(SCHEMA_SQL)
        self.con.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        self.con.execute("INSERT OR IGNORE INTO meta VALUES ('created_at', ?)", (now_iso(),))
        from . import __version__
        self.con.execute("INSERT OR REPLACE INTO meta VALUES ('roamingest_version', ?)", (__version__,))

    def meta(self):
        return dict(self.con.execute("SELECT key, value FROM meta"))

    def close(self):
        self.con.close()

    # ---------------------------------------------------------------- sources, models, runs
    def source_id(self, root, storage=None, label=None):
        root = os.path.abspath(root)
        row = self.con.execute("SELECT source_id FROM sources WHERE root_path=?", (root,)).fetchone()
        if row:
            if storage:
                self.con.execute("UPDATE sources SET storage=? WHERE source_id=?", (storage, row[0]))
            return row[0]
        cur = self.con.execute("INSERT INTO sources(root_path, root_prefix, label, storage) VALUES (?,?,?,?)",
                               (root, _root_prefix(root), label, storage))
        return cur.lastrowid

    def model_id_for(self, name, pretrained):
        """The recorded model_id of an architecture and pretrained tag (any dtype), or None."""
        row = self.con.execute("SELECT model_id FROM models WHERE name=? AND pretrained=? ORDER BY model_id",
                               (name, pretrained)).fetchone()
        return row[0] if row else None

    def ensure_model(self, m):
        self.con.execute("INSERT OR IGNORE INTO models(model_id,name,pretrained,dim,dtype,preprocess) VALUES (?,?,?,?,?,?)",
                         (m["model_id"], m["name"], m.get("pretrained"), m["dim"], m["dtype"], m["preprocess"]))

    def start_run(self, config, code_version):
        cur = self.con.execute("INSERT INTO runs(started_at, code_version, config_json) VALUES (?,?,?)",
                               (now_iso(), code_version, json.dumps(config, sort_keys=True, default=str)))
        return cur.lastrowid

    def finish_run(self, run_id, report):
        self.con.execute("UPDATE runs SET finished_at=?, report_json=? WHERE run_id=?",
                         (now_iso(), json.dumps(report, default=str), run_id))

    # ---------------------------------------------------------------- resume / moves / missing
    def done_map(self, source_ids):
        q = ",".join("?" * len(source_ids))
        return {(s, r): (z, m) for s, r, z, m in self.con.execute(
            f"SELECT source_id, rel_path, size, mtime_ns FROM asset_files WHERE source_id IN ({q}) "
            "AND status NOT IN ('missing')", list(source_ids))}

    def mark_missing(self, source_id, present_rel_paths):
        """Files of a fully scanned source that were not found -> status 'missing'. Returns count."""
        self.con.execute("CREATE TEMP TABLE IF NOT EXISTS _present(rel_path TEXT PRIMARY KEY)")
        self.con.execute("DELETE FROM _present")
        self.con.executemany("INSERT OR IGNORE INTO _present VALUES (?)", ((r,) for r in present_rel_paths))
        cur = self.con.execute("UPDATE asset_files SET status='missing' WHERE source_id=? AND status!='missing' "
                               "AND rel_path NOT IN (SELECT rel_path FROM _present)", (source_id,))
        return cur.rowcount

    def missing_by_size(self, source_ids):
        """{size: [(source_id, rel_path, asset_id)]} of missing files whose asset is still known (move candidates)."""
        q = ",".join("?" * len(source_ids))
        out = {}
        for s, r, z, a in self.con.execute(
                f"SELECT f.source_id, f.rel_path, f.size, f.asset_id FROM asset_files f JOIN assets a ON a.asset_id=f.asset_id "
                f"WHERE f.status='missing' AND f.source_id IN ({q})", list(source_ids)):
            out.setdefault(z, []).append((s, r, a))
        return out

    def relink_moved(self, old, it, run_id):
        """A missing file reappeared at a new path with the same content: copy its row, drop the old one."""
        s, r, asset_id = old
        row = self.con.execute("SELECT n_emb, status, error_code FROM asset_files WHERE source_id=? AND rel_path=?",
                               (s, r)).fetchone()
        self.con.execute("DELETE FROM asset_files WHERE source_id=? AND rel_path=?", (s, r))
        self.con.execute("DELETE FROM file_stats WHERE source_id=? AND rel_path=?", (s, r))
        self.con.execute(f"INSERT OR REPLACE INTO asset_files({','.join(FILE_COLS)}) VALUES ({','.join('?' * len(FILE_COLS))})",
                         (it["source_id"], it["rel"], it["size"], it["mtime_ns"], it["kind"], it["ext"], asset_id,
                          "done", row[2] if row else None, row[0] if row else 0, it.get("sidecar_rel"), run_id))

    # ---------------------------------------------------------------- writes (caller holds the transaction)
    def write_file(self, it, rec, embs, run_id, model_id):
        """it: discovered item; rec: worker result; embs: {frame_index: (timestamp_s, vector_bytes)}.
        Returns the thumbnail_ref of an asset that became orphaned (so the caller can delete the file), or None."""
        con = self.con
        status = rec.get("status", "done")
        asset_id = rec.get("cid") if status != "error" else None
        old = con.execute("SELECT asset_id FROM asset_files WHERE source_id=? AND rel_path=?",
                          (it["source_id"], it["rel"])).fetchone()
        old_asset = old[0] if old else None
        n_emb = len(embs)
        write_asset = bool(asset_id)
        if asset_id and embs:
            # Duplicate content: the file with the smallest (source_id, rel_path) owns the frames, so the stored
            # result does not depend on which copy finished first (copies may be routed to different decoders).
            owner = con.execute("SELECT source_id, rel_path FROM asset_files WHERE asset_id=? AND n_emb>0 "
                                "AND status NOT IN ('error','missing') AND NOT (source_id=? AND rel_path=?) "
                                "ORDER BY source_id, rel_path LIMIT 1", (asset_id, it["source_id"], it["rel"])).fetchone()
            if owner and (owner[0], owner[1]) < (it["source_id"], it["rel"]):
                write_asset = False                        # keep the owner's frames; this file only links to them
                n_emb = con.execute("SELECT n_frames FROM assets WHERE asset_id=?", (asset_id,)).fetchone()[0]
        if write_asset:
            a = dict(asset_id=asset_id, content_hash_full=rec.get("full_hash"), size=it["size"], media_type=it["kind"],
                     format=it["ext"].lstrip("."), width=rec.get("width"), height=rec.get("height"),
                     orientation=rec.get("orientation"), rotation=rec.get("rotation"),
                     capture_time_utc=rec.get("capture_time_utc"), capture_time_local=rec.get("capture_time_local"),
                     tz_offset_min=rec.get("tz_offset_min"), capture_time_source=rec.get("capture_time_source"),
                     lat=rec.get("lat"), lon=rec.get("lon"), alt=rec.get("alt"), gps_source=rec.get("gps_source"),
                     codec=rec.get("codec"), duration_s=rec.get("dur_s"), fps=rec.get("fps"),
                     sampling_policy=rec.get("sampling_policy"), thumbnail_ref=rec.get("thumbnail_ref"),
                     n_frames=len(embs), first_run_id=run_id, last_run_id=run_id)
            upd = ",".join(f"{c}=excluded.{c}" for c in ASSET_COLS if c not in ("asset_id", "first_run_id", "content_hash_full"))
            con.execute(f"INSERT INTO assets({','.join(ASSET_COLS)}) VALUES ({','.join('?' * len(ASSET_COLS))}) "
                        f"ON CONFLICT(asset_id) DO UPDATE SET {upd}, "
                        "content_hash_full=COALESCE(excluded.content_hash_full, assets.content_hash_full)",
                        [a[c] for c in ASSET_COLS])
            con.execute("DELETE FROM embeddings WHERE asset_id=? AND model_id=?", (asset_id, model_id))
            con.execute("DELETE FROM frames WHERE asset_id=?", (asset_id,))
            methods, req = rec.get("methods", {}), rec.get("requested", {})
            for idx in sorted(embs):
                ts, vec = embs[idx]
                photo = it["kind"] == "photo"
                con.execute("INSERT INTO frames(asset_id,frame_index,timestamp_s,requested_time_s,sampling_method,thumbnail_ref) "
                            "VALUES (?,?,?,?,?,NULL)",
                            (asset_id, idx, None if photo else ts, None if photo else req.get(idx),
                             "image" if photo else methods.get(idx, "exact")))
                con.execute("INSERT INTO embeddings(asset_id,frame_index,model_id,vector) VALUES (?,?,?,?)",
                            (asset_id, idx, model_id, vec))
        f = dict(source_id=it["source_id"], rel_path=it["rel"], size=it["size"], mtime_ns=it["mtime_ns"], kind=it["kind"],
                 ext=it["ext"], asset_id=asset_id, status=status, error_code=rec.get("error"), n_emb=n_emb,
                 sidecar_rel=it.get("sidecar_rel"), last_run_id=run_id)
        con.execute(f"INSERT OR REPLACE INTO asset_files({','.join(FILE_COLS)}) VALUES ({','.join('?' * len(FILE_COLS))})",
                    [f[c] for c in FILE_COLS])
        st = dict(source_id=it["source_id"], rel_path=it["rel"], run_id=run_id, decoder=rec.get("decoder"),
                  strategy=rec.get("strategy"), frames_decoded=rec.get("frames_decoded"), seeks=rec.get("seeks"),
                  probes=rec.get("probes"), t_read_ms=rec.get("t_read_ms"), t_decode_ms=rec.get("t_decode_ms"),
                  t_xform_ms=rec.get("t_xform_ms"), t_write_ms=rec.get("t_write_ms"))
        con.execute(f"INSERT OR REPLACE INTO file_stats({','.join(STAT_COLS)}) VALUES ({','.join('?' * len(STAT_COLS))})",
                    [st[c] for c in STAT_COLS])
        if old_asset and old_asset != asset_id:
            return self._drop_if_orphan(old_asset)
        return None

    def link_file(self, it, rec, run_id):
        """Record a byte-identical copy of an existing asset (dedup=skip): no frames or embeddings are written."""
        con, cid = self.con, rec["cid"]
        row = con.execute("SELECT n_frames FROM assets WHERE asset_id=?", (cid,)).fetchone()
        if not row or not row[0]:
            # the asset was removed during this run (rare): record an error with mtime 0 so the next run re-reads it
            return self.write_file(dict(it, mtime_ns=0), dict(rec, status="error", error="dedup_race", cid=None),
                                   {}, run_id, None)
        old = con.execute("SELECT asset_id FROM asset_files WHERE source_id=? AND rel_path=?",
                          (it["source_id"], it["rel"])).fetchone()
        f = dict(source_id=it["source_id"], rel_path=it["rel"], size=it["size"], mtime_ns=it["mtime_ns"],
                 kind=it["kind"], ext=it["ext"], asset_id=cid, status="done", error_code=None, n_emb=row[0],
                 sidecar_rel=it.get("sidecar_rel"), last_run_id=run_id)
        con.execute(f"INSERT OR REPLACE INTO asset_files({','.join(FILE_COLS)}) "
                    f"VALUES ({','.join('?' * len(FILE_COLS))})", [f[c] for c in FILE_COLS])
        st = dict(source_id=it["source_id"], rel_path=it["rel"], run_id=run_id, decoder=None, strategy="dedup_link",
                  frames_decoded=0, seeks=0, probes=0, t_read_ms=rec.get("t_read_ms"), t_decode_ms=0.0,
                  t_xform_ms=0.0, t_write_ms=0.0)
        con.execute(f"INSERT OR REPLACE INTO file_stats({','.join(STAT_COLS)}) "
                    f"VALUES ({','.join('?' * len(STAT_COLS))})", [st[c] for c in STAT_COLS])
        if old and old[0] and old[0] != cid:
            return self._drop_if_orphan(old[0])
        return None

    def _drop_if_orphan(self, asset_id):
        if self.con.execute("SELECT 1 FROM asset_files WHERE asset_id=? LIMIT 1", (asset_id,)).fetchone():
            return None
        self.dropped.append(asset_id)
        row = self.con.execute("SELECT thumbnail_ref FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
        self.con.execute("DELETE FROM embeddings WHERE asset_id=?", (asset_id,))
        self.con.execute("DELETE FROM frames WHERE asset_id=?", (asset_id,))
        self.con.execute("DELETE FROM assets WHERE asset_id=?", (asset_id,))
        return row[0] if row else None

    # ---------------------------------------------------------------- v0 -> v1
    def migrate_v0(self, roots=None):
        """Convert a v0 manifest (tables files, emb) in place. A copy of the original is kept as <db>.v0.bak."""
        con = self.con
        bak = self.path + ".v0.bak"
        if not os.path.exists(bak):
            con.execute("VACUUM INTO ?", (bak,))
        cols = [r[1] for r in con.execute("PRAGMA table_info(files)")]
        rows = [dict(zip(cols, r)) for r in con.execute("SELECT * FROM files")]
        ecols = [r[1] for r in con.execute("PRAGMA table_info(emb)")]
        has_method = "method" in ecols
        con.execute("BEGIN")
        try:
            con.execute("ALTER TABLE files RENAME TO v0_files")
            con.execute("ALTER TABLE emb RENAME TO v0_emb")
            for stmt in _split_sql(SCHEMA_SQL):
                con.execute(stmt)
            con.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            con.execute("INSERT OR IGNORE INTO meta VALUES ('created_at', ?)", (now_iso(),))
            con.execute("INSERT OR REPLACE INTO meta VALUES ('migrated_from', '0')")
            from . import __version__
            con.execute("INSERT OR REPLACE INTO meta VALUES ('roamingest_version', ?)", (__version__,))
            self.ensure_model(V0_MODEL)
            run_id = con.execute("INSERT INTO runs(started_at, finished_at, code_version, config_json) VALUES (?,?,?,?)",
                                 (now_iso(), now_iso(), "v0-migration", json.dumps({"migrated_from": 0}))).lastrowid
            paths = [r["path"] for r in rows]
            if roots:
                roots = [os.path.abspath(x) for x in roots]
            elif paths:
                roots = [os.path.abspath(os.path.commonpath([os.path.dirname(os.path.abspath(p)) for p in paths]))]
            else:
                roots = []
            sid = {r: self.source_id(r) for r in roots}
            by_path = {}
            for r in rows:
                ap = os.path.abspath(r["path"])
                root = max((x for x in roots if ap.startswith(_root_prefix(x))), key=len, default=None)
                if root is None:
                    raise RuntimeError("a v0 path is outside the given roots")
                it = dict(source_id=sid[root], rel=os.path.relpath(ap, root), size=r["size"], mtime_ns=r["mtime_ns"],
                          kind=r["kind"], ext=r["ext"])
                by_path[r["path"]] = (it, r)
            embs = {}
            q = "SELECT path, idx, pts, vec, method FROM v0_emb" if has_method else "SELECT path, idx, pts, vec, NULL FROM v0_emb"
            for p, idx, pts, vec, method in con.execute(q):
                embs.setdefault(p, {})[idx] = (pts, vec, method)
            for p, (it, r) in by_path.items():
                e = embs.get(p, {})
                thumb = None
                if r["kind"] == "photo" and r.get("cid"):
                    t = os.path.join("thumbs", r["cid"][:2], r["cid"] + ".jpg")
                    thumb = t.replace(os.sep, "/") if os.path.exists(os.path.join(self.dataset_dir, t)) else None
                rec = dict(status=r["status"], cid=r.get("cid"), error=r.get("error"), width=r.get("width"),
                           height=r.get("height"), codec=r.get("codec"), dur_s=r.get("dur_s"), thumbnail_ref=thumb,
                           sampling_policy=r.get("strategy") if r["kind"] == "video" else None,
                           decoder=r.get("decoder"), strategy=r.get("strategy"), frames_decoded=r.get("frames_decoded"),
                           seeks=r.get("seeks"), probes=r.get("probes"), t_read_ms=r.get("t_read_ms"),
                           t_decode_ms=r.get("t_decode_ms"), t_xform_ms=r.get("t_xform_ms"), t_write_ms=r.get("t_write_ms"),
                           methods={i: (m or "exact") for i, (_, _, m) in e.items()})
                self.write_file(it, rec, {i: (pts, vec) for i, (pts, vec, _) in e.items()}, run_id, V0_MODEL["model_id"])
            con.execute("DROP TABLE v0_files")
            con.execute("DROP TABLE v0_emb")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return dict(files=len(rows), embeddings=sum(len(v) for v in embs.values()), sources=len(roots), backup=bak)


def _split_sql(sql):
    """Split the schema script into statements (it has no semicolons inside strings or triggers)."""
    body = re.sub(r"--[^\n]*", "", sql)                 # comments may contain ';'
    return [s.strip() for s in body.split(";") if s.strip()]
