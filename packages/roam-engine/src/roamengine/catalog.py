"""Read-only, in-memory view of a manifest v1 library (Contract A, shared/schemas/manifest_v1.sql).

Loads every searchable asset (one with at least one file in status 'done') and all of its frame embeddings for one
model into numpy arrays. The manifest is opened read-only, so an ingest may run at the same time; `changed()` tells
the caller when to reload.
"""
import datetime as dt
import os
import sqlite3

import numpy as np

SUPPORTED_SCHEMA = 1


def manifest_file(library):
    for name in ("manifest.sqlite", "manifest.db"):
        p = os.path.join(library, name)
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"no manifest.sqlite in {library}")


def _epoch(iso):
    """'2023-05-06T01:38:09Z' or a local '2023-05-06T07:08:09' -> seconds (local times are treated as UTC)."""
    if not iso:
        return np.nan
    try:
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return np.nan
    return (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).timestamp()


def connect_ro(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    con.execute("PRAGMA query_only=1")
    return con


class Catalog:
    def __init__(self, library, model_id=None):
        self.library = os.path.abspath(library)
        self.path = manifest_file(self.library)
        self.model_id_req = model_id
        self.load()

    @classmethod
    def empty(cls, library, model_id=None):
        """A library with no manifest yet (before its first ingest): searchable, with zero assets."""
        self = cls.__new__(cls)
        self.library = os.path.abspath(library)
        self.path = os.path.join(self.library, "manifest.sqlite")
        self.model_id_req, self.model_id, self.model_name, self.model_pretrained = model_id, None, None, None
        self.dim, self.models = 0, []
        self.asset_ids = np.array([], dtype=object)
        self.index = {}
        z = lambda dt_: np.zeros(0, dtype=dt_)  # noqa: E731
        self.is_video, self.has_emb = z(bool), z(bool)
        self.width, self.height, self.rotation = z(np.int32), z(np.int32), z(np.int16)
        self.t, self.lat, self.lon, self.duration, self.size = z(np.float64), z(np.float64), z(np.float64),             z(np.float32), z(np.int64)
        self.fmt, self.time_utc, self.time_local, self.time_source, self.gps_source, self.thumb, self.codec =             [], [], [], [], [], [], []
        self.f_asset, self.f_index, self.f_ts = z(np.int32), z(np.int32), z(np.float32)
        self.vectors = np.zeros((0, 1), np.float32)
        self.f_start, self.f_count, self.emb_idx = z(np.int64), z(np.int64), z(np.int64)
        self.n_files, self.sources, self.last_run, self.status_counts = 0, [], None, {}
        self.stamp = self._stamp()
        return self

    @staticmethod
    def open(library, model_id=None):
        try:
            return Catalog(library, model_id)
        except FileNotFoundError:
            return Catalog.empty(library, model_id)

    # ------------------------------------------------------------------ change detection
    def _stamp(self):
        st = []
        for suf in ("", "-wal"):
            try:
                s = os.stat(self.path + suf)
                st.append((s.st_size, s.st_mtime_ns))
            except OSError:
                st.append(None)
        return tuple(st)

    def changed(self):
        return self._stamp() != self.stamp

    # ------------------------------------------------------------------ load
    def load(self):
        stamp = self._stamp()
        con = connect_ro(self.path)
        try:
            meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
            ver = int(meta.get("schema_version", 0))
            if ver != SUPPORTED_SCHEMA:
                raise ValueError(f"manifest schema {ver} unsupported (need {SUPPORTED_SCHEMA}); run `roamingest migrate`")
            models = con.execute(
                "SELECT m.model_id, m.name, m.pretrained, m.dim, COUNT(e.asset_id) FROM models m "
                "LEFT JOIN embeddings e ON e.model_id = m.model_id GROUP BY m.model_id ORDER BY 5 DESC").fetchall()
            if not models:
                raise ValueError("manifest has no embedding model")
            if self.model_id_req:
                models = [m for m in models if m[0] == self.model_id_req]
                if not models:
                    raise ValueError(f"model {self.model_id_req} not in manifest")
            self.model_id, self.model_name, self.model_pretrained, self.dim, _ = models[0]
            self.models = [m[0] for m in models]

            live = ("WITH live AS (SELECT DISTINCT asset_id FROM asset_files "
                    "WHERE status='done' AND asset_id IS NOT NULL) ")
            rows = con.execute(
                live + "SELECT a.asset_id, a.media_type, a.format, a.width, a.height, a.capture_time_utc, a.capture_time_local,"
                " a.capture_time_source, a.lat, a.lon, a.gps_source, a.duration_s, a.thumbnail_ref, a.size, a.codec,"
                " a.rotation FROM assets a JOIN live USING(asset_id) ORDER BY a.asset_id").fetchall()
            ids = [r[0] for r in rows]
            self.asset_ids = np.array(ids, dtype=object)
            self.index = {a: i for i, a in enumerate(ids)}
            n = len(ids)
            self.is_video = np.array([r[1] == "video" for r in rows], dtype=bool)
            self.fmt = [r[2] for r in rows]
            self.width = np.array([r[3] or 0 for r in rows], dtype=np.int32)
            self.height = np.array([r[4] or 0 for r in rows], dtype=np.int32)
            self.rotation = np.array([r[15] or 0 for r in rows], dtype=np.int16)
            self.time_utc = [r[5] for r in rows]
            self.time_local = [r[6] for r in rows]
            self.time_source = [r[7] for r in rows]
            self.t = np.array([_epoch(r[5] or r[6]) for r in rows], dtype=np.float64)   # sort/filter key
            self.lat = np.array([np.nan if r[8] is None else r[8] for r in rows], dtype=np.float64)
            self.lon = np.array([np.nan if r[9] is None else r[9] for r in rows], dtype=np.float64)
            self.gps_source = [r[10] for r in rows]
            self.duration = np.array([r[11] or 0.0 for r in rows], dtype=np.float32)
            self.thumb = [r[12] for r in rows]
            self.size = np.array([r[13] for r in rows], dtype=np.int64)
            self.codec = [r[14] for r in rows]

            # frames + embeddings, grouped by asset (ORDER BY asset_id, frame_index matches self.asset_ids order)
            fr = con.execute(
                live + "SELECT e.asset_id, e.frame_index, f.timestamp_s, e.vector FROM embeddings e JOIN live USING(asset_id) "
                "JOIN frames f ON f.asset_id = e.asset_id AND f.frame_index = e.frame_index "
                "WHERE e.model_id = ? ORDER BY e.asset_id, e.frame_index", (self.model_id,)).fetchall()
            m = len(fr)
            self.f_asset = np.fromiter((self.index[r[0]] for r in fr), dtype=np.int32, count=m)
            self.f_index = np.fromiter((r[1] for r in fr), dtype=np.int32, count=m)
            self.f_ts = np.fromiter((np.nan if r[2] is None else r[2] for r in fr), dtype=np.float32, count=m)
            vec = np.frombuffer(b"".join(r[3] for r in fr), dtype=np.float16).reshape(m, -1) if m else \
                np.zeros((0, self.dim), np.float16)
            v = vec.astype(np.float32)
            v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-6)
            self.vectors = np.ascontiguousarray(v)
            # segment starts per asset (assets without embeddings get an empty segment)
            counts = np.bincount(self.f_asset, minlength=n)
            self.f_start = np.concatenate([[0], np.cumsum(counts)[:-1]]).astype(np.int64)
            self.f_count = counts.astype(np.int64)
            self.has_emb = counts > 0
            self.emb_idx = np.flatnonzero(self.has_emb)

            self.n_files = con.execute("SELECT COUNT(*) FROM asset_files WHERE status='done'").fetchone()[0]
            self.sources = [dict(source_id=s, root=r, label=l, storage=st) for s, r, l, st in
                            con.execute("SELECT source_id, root_path, label, storage FROM sources ORDER BY source_id")]
            last = con.execute("SELECT run_id, started_at, finished_at, code_version FROM runs "
                               "ORDER BY run_id DESC LIMIT 1").fetchone()
            self.last_run = dict(zip(("run_id", "started_at", "finished_at", "code_version"), last)) if last else None
            self.status_counts = {f"{k}:{s}": c for k, s, c in con.execute(
                "SELECT kind, status, COUNT(*) FROM asset_files GROUP BY kind, status")}
        finally:
            con.close()
        self.stamp = stamp
        return self

    # ------------------------------------------------------------------ per-asset detail (queried on demand)
    def files_of(self, asset_id):
        con = connect_ro(self.path)
        try:
            rows = con.execute(
                "SELECT s.root_prefix || f.rel_path, f.source_id, f.rel_path, f.size, f.status, f.ext "
                "FROM asset_files f JOIN sources s ON s.source_id = f.source_id WHERE f.asset_id = ? "
                "ORDER BY (f.status = 'done') DESC, f.source_id, f.rel_path", (asset_id,)).fetchall()
        finally:
            con.close()
        return [dict(path=p, source_id=s, rel_path=r, size=z, status=st, ext=e) for p, s, r, z, st, e in rows]

    def primary_path(self, asset_id):
        for f in self.files_of(asset_id):
            if f["status"] == "done" and os.path.exists(f["path"]):
                return f["path"]
        return None

    def frames_of(self, asset_id):
        i = self.index[asset_id]
        s, c = self.f_start[i], self.f_count[i]
        return [dict(frame_index=int(self.f_index[j]),
                     timestamp_s=None if np.isnan(self.f_ts[j]) else round(float(self.f_ts[j]), 3))
                for j in range(s, s + c)]

    def summary(self, i):
        """Compact JSON-able description of asset row i."""
        return dict(
            asset_id=self.asset_ids[i], media_type="video" if self.is_video[i] else "photo", format=self.fmt[i],
            width=int(self.width[i]), height=int(self.height[i]), rotation=int(self.rotation[i]),
            capture_time=self.time_utc[i] or self.time_local[i], capture_time_is_utc=bool(self.time_utc[i]),
            capture_time_source=self.time_source[i],
            lat=None if np.isnan(self.lat[i]) else round(float(self.lat[i]), 6),
            lon=None if np.isnan(self.lon[i]) else round(float(self.lon[i]), 6),
            duration_s=round(float(self.duration[i]), 2) if self.is_video[i] else None,
            has_thumb=bool(self.thumb[i]))

    def stats(self):
        if self.model_id is None:
            return dict(library=self.library, empty=True, assets=0, photos=0, videos=0, files=0, frames=0,
                        with_time=0, with_gps=0, sources=[], last_run=None, status={}, time_range=None,
                        model_id=None, models=[])
        n_gps = int(np.isfinite(self.lat).sum())
        return dict(library=self.library, model_id=self.model_id, models=self.models,
                    assets=len(self.asset_ids), photos=int((~self.is_video).sum()), videos=int(self.is_video.sum()),
                    files=self.n_files, frames=len(self.f_asset), with_time=int(np.isfinite(self.t).sum()),
                    with_gps=n_gps, sources=self.sources, last_run=self.last_run, status=self.status_counts,
                    time_range=[_iso(np.nanmin(self.t)), _iso(np.nanmax(self.t))] if np.isfinite(self.t).any()
                    else None)


def _iso(sec):
    return dt.datetime.fromtimestamp(float(sec), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
