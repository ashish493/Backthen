"""Search over a Catalog: natural-language (CLIP text), similar items, time/place/type filters, video moments.

Exact (brute-force) cosine search over every frame embedding: at 74k frames x 512 dims this is one matrix-vector
product of about 10 ms on the CPU, so no approximate index is needed yet (ADR-009).
A video's score is its best frame's score; its `moments` are its best frames, with timestamps.
"""
import dataclasses
import datetime as dt
import re
import threading
import time

import numpy as np

from .catalog import Catalog
from .places import Places

EARTH_KM = 6371.0088


@dataclasses.dataclass
class SearchFilters:
    media_type: str | None = None          # 'photo' | 'video'
    date_from: str | None = None           # ISO date or datetime, inclusive
    date_to: str | None = None             # ISO date or datetime, inclusive (a bare date covers the whole day)
    has_gps: bool | None = None
    has_time: bool | None = None
    near: tuple | None = None              # (lat, lon, radius_km)
    bbox: tuple | None = None              # (south, west, north, east)
    source_id: int | None = None           # not yet: needs per-file source arrays
    manual_place: bool | None = None       # location added by the user in RoamIndex (places.sqlite)

    @classmethod
    def from_params(cls, p):
        def f(k):
            v = p.get(k)
            return None if v in (None, "") else v
        near = None
        if f("lat") is not None and f("lon") is not None:
            near = (float(p["lat"]), float(p["lon"]), float(f("radius_km") or 25))
        bbox = tuple(float(x) for x in p["bbox"].split(",")) if f("bbox") else None
        b = lambda k: None if f(k) is None else str(f(k)).lower() in ("1", "true", "yes")  # noqa: E731
        return cls(media_type=f("type"), date_from=f("from"), date_to=f("to"), has_gps=b("gps"), has_time=b("dated"),
                   near=near, bbox=bbox, manual_place=b("manual"))


def _parse_bound(s, end):
    s = s.strip()
    if re.fullmatch(r"\d{4}", s):
        s = f"{s}-12-31" if end else f"{s}-01-01"
    elif re.fullmatch(r"\d{4}-\d{2}", s):
        y, m = map(int, s.split("-"))
        last = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)).day
        s = f"{s}-{last:02d}" if end else f"{s}-01"
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if len(s) == 10 and end:
        t += dt.timedelta(days=1) - dt.timedelta(microseconds=1)
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


INLINE = re.compile(r"\b(type|before|after|year|in|has):(\S+)", re.I)


def parse_query(q, filters=None):
    """Inline filters: type:photo|video, after:2019-05, before:2020, year:2019, has:gps|time. Returns (text, filters)."""
    f = dataclasses.replace(filters) if filters else SearchFilters()
    for key, val in INLINE.findall(q):
        key, val = key.lower(), val.lower()
        if key == "type" and val in ("photo", "photos", "video", "videos"):
            f.media_type = val.rstrip("s")
        elif key == "after":
            f.date_from = val
        elif key == "before":
            f.date_to = val
        elif key == "year":
            f.date_from = f.date_to = val
        elif key == "has" and val in ("gps", "location"):
            f.has_gps = True
        elif key == "has" and val in ("time", "date"):
            f.has_time = True
    return INLINE.sub("", q).strip(), f


class Engine:
    def __init__(self, library, model_id=None, text_device="cpu", reload_check_s=5.0):
        t0 = time.perf_counter()
        self.places = Places(library)
        self.cat = self._overlay(Catalog.open(library, model_id))
        self.load_s = time.perf_counter() - t0
        self.text_device = text_device
        self._text = None
        self._lock = threading.RLock()
        self._text_lock = threading.Lock()
        self._last_check = time.monotonic()
        self.reload_check_s = reload_check_s

    # ------------------------------------------------------------------ lifecycle
    @property
    def text(self):
        t = self._text
        if t is None or t.model_id != self.cat.model_id:
            with self._text_lock:                      # the warm-up thread and a first query must not both load it
                t = self._text
                if t is None or t.model_id != self.cat.model_id:
                    from .text import TextEncoder
                    t = self._text = TextEncoder(self.cat.model_id, device=self.text_device)
        return t

    def maybe_reload(self, force=False):
        """Reload the catalogue when the manifest changed (an ingest committed). Cheap stat() check."""
        now = time.monotonic()
        # a reload blocks searches for load_s; while an ingest commits continuously, reload at most every
        # max(reload_check_s, 20 x load_s) (about 36 s for the 48k-item archive) so searching stays responsive
        if not force and now - self._last_check < max(self.reload_check_s, 20 * self.load_s):
            return False
        self._last_check = now
        if force or self.cat.changed():
            t0 = time.perf_counter()
            cat = self._overlay(Catalog.open(self.cat.library, self.cat.model_id_req))   # build outside the lock
            with self._lock:
                self.cat = cat
            self.load_s = time.perf_counter() - t0
            return True
        return False

    # ------------------------------------------------------------------ places added by the user
    def _overlay(self, cat):
        """Apply places.sqlite on top of the manifest's locations (in memory only; the manifest is not touched)."""
        if not hasattr(cat, "lat0"):
            cat.lat0, cat.lon0, cat.gps_source0 = cat.lat.copy(), cat.lon.copy(), list(cat.gps_source)
        cat.lat, cat.lon, cat.gps_source = cat.lat0.copy(), cat.lon0.copy(), list(cat.gps_source0)
        cat.manual = np.zeros(len(cat.lat), dtype=bool)
        cat.place_name = {}
        for aid, (la, lo, name) in self.places.all().items():
            i = cat.index.get(aid)
            if i is None:
                continue
            cat.lat[i], cat.lon[i], cat.gps_source[i], cat.manual[i] = la, lo, "manual", True
            if name:
                cat.place_name[aid] = name
        return cat

    def set_place(self, asset_ids, lat, lon, name=None):
        with self._lock:
            ids = [a for a in dict.fromkeys(asset_ids) if a in self.cat.index]
            n = self.places.set(ids, lat, lon, name)
            self._overlay(self.cat)
            return n

    def clear_place(self, asset_ids):
        with self._lock:
            n = self.places.clear(list(dict.fromkeys(asset_ids)))
            self._overlay(self.cat)
            return n

    def place_suggestions(self, asset_id, window_h=6.0, n=3):
        """Locations of items taken close in time to this one (before and after), nearest first. A trip's photos
        without GPS were usually taken near the ones with it. Times without a zone are compared as if UTC."""
        self.maybe_reload()
        with self._lock:
            c = self.cat
            i = c.index[asset_id]
            if not np.isfinite(c.t[i]):
                return []
            ok = np.isfinite(c.lat) & np.isfinite(c.t)
            ok[i] = False
            idx = np.flatnonzero(ok)
            d = c.t[idx] - c.t[i]
            near = idx[np.abs(d) <= window_h * 3600]
            near = near[np.argsort(np.abs(c.t[near] - c.t[i]))]
            out, seen = [], []
            for j in near:
                la, lo = float(c.lat[j]), float(c.lon[j])
                if any(haversine_km(la, lo, a, b) < 0.2 for a, b in seen):     # one suggestion per spot
                    continue
                seen.append((la, lo))
                out.append(dict(asset_id=c.asset_ids[j], lat=round(la, 6), lon=round(lo, 6),
                                dt_s=round(float(c.t[j] - c.t[i])), source=c.gps_source[j],
                                name=c.place_name.get(c.asset_ids[j])))
                if len(out) >= n:
                    break
            return out

    # ------------------------------------------------------------------ filtering
    def mask(self, f: SearchFilters | None):
        c = self.cat
        m = c.has_emb.copy()
        if not f:
            return m
        if f.media_type == "photo":
            m &= ~c.is_video
        elif f.media_type == "video":
            m &= c.is_video
        if f.date_from or f.date_to:
            lo = _parse_bound(f.date_from, False) if f.date_from else -np.inf
            hi = _parse_bound(f.date_to, True) if f.date_to else np.inf
            with np.errstate(invalid="ignore"):
                m &= (c.t >= lo) & (c.t <= hi)
        if f.has_time is not None:
            m &= np.isfinite(c.t) == f.has_time
        if f.has_gps is not None:
            m &= np.isfinite(c.lat) == f.has_gps
        if f.manual_place is not None:
            m &= c.manual == f.manual_place
        if f.near:
            lat, lon, r = f.near
            m &= haversine_km(c.lat, c.lon, lat, lon) <= r
        if f.bbox:
            s, w, n, e = f.bbox
            with np.errstate(invalid="ignore"):
                inlat = (c.lat >= s) & (c.lat <= n)
                inlon = (c.lon >= w) & (c.lon <= e) if w <= e else (c.lon >= w) | (c.lon <= e)
            m &= inlat & inlon
        return m

    # ------------------------------------------------------------------ ranking
    def _rank(self, qv, mask, k, offset, moments, exclude=None):
        c = self.cat
        if exclude is not None:
            mask = mask.copy()
            mask[exclude] = False
        idx = np.flatnonzero(mask)
        if not len(idx) or not len(c.vectors):
            return [], 0
        fscore = c.vectors @ qv                                           # every frame
        # per-asset max over ALL assets with frames (their segments are contiguous), then select: reducing over the
        # filtered starts alone would let a segment run on into the frames of excluded assets
        best_all = np.full(len(c.asset_ids), -np.inf, dtype=np.float32)
        best_all[c.emb_idx] = np.maximum.reduceat(fscore, c.f_start[c.emb_idx])
        best = best_all[idx]
        starts, counts = c.f_start[idx], c.f_count[idx]
        n = min(len(idx), offset + k)
        top = np.argpartition(-best, n - 1)[:n] if n < len(idx) else np.arange(len(idx))
        top = top[np.lexsort((c.asset_ids[idx[top]].astype(str), -best[top]))][offset:offset + k]
        out = []
        for j in top:
            i = idx[j]
            s, cnt = starts[j], counts[j]
            seg = fscore[s:s + cnt]
            item = c.summary(i)
            item["score"] = round(float(best[j]), 4)
            if c.is_video[i]:
                order = np.argsort(-seg, kind="stable")[:moments]
                item["moments"] = [dict(timestamp_s=_ts(c.f_ts[s + o]), frame_index=int(c.f_index[s + o]),
                                        score=round(float(seg[o]), 4)) for o in order]
            out.append(item)
        return out, int(len(idx))

    def search(self, query, filters=None, k=50, offset=0, moments=3):
        t0 = time.perf_counter()
        self.maybe_reload()
        text, f = parse_query(query, filters)
        if not text or self.cat.model_id is None:                  # filters only: newest first
            return self.browse(f, k, offset) | dict(query=query, text="")
        qv = self.text.encode([text])[0]                           # outside the catalogue lock (first call loads)
        t1 = time.perf_counter()
        with self._lock:
            items, n = self._rank(qv, self.mask(f), k, offset, moments)
        return dict(query=query, text=text, total=n, offset=offset, items=items,
                    timing_ms=dict(encode=round((t1 - t0) * 1e3, 1), rank=round((time.perf_counter() - t1) * 1e3, 1)))

    def similar(self, asset_id, filters=None, k=50, offset=0, frame_index=None, moments=3):
        self.maybe_reload()
        with self._lock:
            c = self.cat
            i = c.index[asset_id]
            s, cnt = c.f_start[i], c.f_count[i]
            if not cnt:
                return dict(items=[], total=0)
            if frame_index is not None:
                hit = np.flatnonzero(c.f_index[s:s + cnt] == int(frame_index))
                qv = c.vectors[s + (hit[0] if len(hit) else 0)]
            else:
                qv = c.vectors[s:s + cnt].mean(0)
                qv = qv / max(np.linalg.norm(qv), 1e-6)
            items, n = self._rank(qv, self.mask(filters), k, offset, moments, exclude=i)
        return dict(asset_id=asset_id, total=n, offset=offset, items=items)

    def text_vector(self, text):
        return self.text.encode([text])[0]

    def suggestions(self, n=12):
        """Queries from SUGGESTION_POOL that match this library best: ranked by the mean score of each query's 10 best
        assets, so every suggested chip leads to strong results. Cached until the catalogue reloads."""
        self.maybe_reload()
        c = self.cat
        if c.model_id is None or not len(c.emb_idx):
            return dict(items=[])
        key = (id(c), n)
        if getattr(self, "_sugg_key", None) == key:
            return self._sugg
        Q = self.text.encode(SUGGESTION_POOL)                               # (q, dim), one batch
        with self._lock:
            fs = c.vectors @ Q.T                                            # (frames, q)
            best = np.maximum.reduceat(fs, c.f_start[c.emb_idx], axis=0)    # (assets with frames, q)
        k = min(10, best.shape[0])
        top = -np.sort(-best, axis=0)[:k]                                   # k best assets per query
        strength = top.mean(0)
        order = np.argsort(-strength, kind="stable")[:n]
        out = dict(items=[dict(text=SUGGESTION_POOL[i], strength=round(float(strength[i]), 4)) for i in order])
        self._sugg_key, self._sugg = key, out
        return out


    # ------------------------------------------------------------------ browsing
    def browse(self, filters=None, k=200, offset=0, order="desc"):
        self.maybe_reload()
        with self._lock:
            c = self.cat
            idx = np.flatnonzero(self.mask(filters))
            t = c.t[idx]
            key = np.where(np.isfinite(t), t, -np.inf if order == "desc" else np.inf)
            o = np.lexsort((c.asset_ids[idx].astype(str), -key if order == "desc" else key))
            sel = idx[o][offset:offset + k]
            return dict(total=int(len(idx)), offset=offset, items=[c.summary(i) for i in sel])

    def timeline(self, filters=None):
        """Asset counts per month (UTC or camera-local time), plus the undated count."""
        self.maybe_reload()
        with self._lock:
            c = self.cat
            idx = np.flatnonzero(self.mask(filters))
            t = c.t[idx]
            ok = np.isfinite(t)
            months = {}
            for sec in t[ok]:
                d = dt.datetime.fromtimestamp(float(sec), dt.timezone.utc)
                key = f"{d.year:04d}-{d.month:02d}"
                months[key] = months.get(key, 0) + 1
            return dict(months=[dict(month=k, count=v) for k, v in sorted(months.items())],
                        undated=int((~ok).sum()))

    def points(self, filters=None, limit=20000):
        """Assets with GPS: compact arrays for a map layer."""
        self.maybe_reload()
        with self._lock:
            c = self.cat
            m = self.mask(filters) & np.isfinite(c.lat)
            idx = np.flatnonzero(m)[:limit]
            return dict(total=int(m.sum()), asset_ids=c.asset_ids[idx].tolist(),
                        lat=np.round(c.lat[idx], 5).tolist(), lon=np.round(c.lon[idx], 5).tolist(),
                        video=c.is_video[idx].astype(int).tolist())

    def asset(self, asset_id):
        self.maybe_reload()
        with self._lock:
            c = self.cat
            i = c.index[asset_id]
            out = c.summary(i)
            manual = bool(c.manual[i])
            out.update(codec=c.codec[i], size=int(c.size[i]), gps_source=c.gps_source[i],
                       place_name=c.place_name.get(asset_id),
                       original_lat=None if not manual or np.isnan(c.lat0[i]) else round(float(c.lat0[i]), 6),
                       original_lon=None if not manual or np.isnan(c.lon0[i]) else round(float(c.lon0[i]), 6),
                       capture_time_utc=c.time_utc[i], capture_time_local=c.time_local[i],
                       frames=c.frames_of(asset_id), files=c.files_of(asset_id))
            return out


def haversine_km(lat1, lon1, lat2, lon2):
    with np.errstate(invalid="ignore"):
        p1, p2 = np.radians(lat1), np.radians(lat2)
        dp, dl = p2 - p1, np.radians(lon2) - np.radians(lon1)
        a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
        d = 2 * EARTH_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    return np.where(np.isfinite(d), d, np.inf)


def _ts(x):
    return None if np.isnan(x) else round(float(x), 3)


SUGGESTION_POOL = [
    "a beach at sunset", "waves on a sandy beach", "mountains in the mist", "a waterfall in a forest", "a lake",
    "a river", "snow", "a desert", "tea plantations on hills", "rice fields", "a forest path", "flowers",
    "a temple", "a church", "a mosque", "a fort or castle", "old ruins", "a city skyline at night", "a busy street",
    "a market", "a train", "a train station", "a boat", "an airplane window view", "a road trip", "a bridge",
    "street food", "a restaurant meal", "coffee", "a birthday cake", "a party", "people dancing", "a group photo",
    "a selfie", "a family dinner", "children playing", "a dog", "a cat", "an elephant", "monkeys", "birds",
    "a wedding", "a concert", "fireworks", "a swimming pool", "a hotel room", "a sunrise", "the night sky",
    "a screenshot", "a document or receipt",
]
