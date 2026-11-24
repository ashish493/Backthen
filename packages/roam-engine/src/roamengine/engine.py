"""Search over a Catalog: natural-language (CLIP text), similar items, time/place/type filters, video moments.

Exact (brute-force) cosine search over every frame embedding: at 74k frames x 512 dims this is one matrix-vector
product of about 10 ms on the CPU, so no approximate index is needed yet (ADR-009).
A video's score is its best frame's score; its `moments` are its best frames, with timestamps.
"""
import collections
import dataclasses
import datetime as dt
import os
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


def _year(sec):
    return dt.datetime.fromtimestamp(float(sec), dt.timezone.utc).year if np.isfinite(sec) else 0


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

    def fixed(self, texts):
        """Vectors for the app's fixed prompts, from the on-disk prompt cache (encoded once per model, ever)."""
        c = self.cat
        pc = getattr(self, "_prompts", None)
        if pc is None or pc[0] != c.model_id:
            from .text import PromptCache
            pc = self._prompts = (c.model_id, PromptCache(os.path.join(c.library, "cache"), c.model_id))
        return pc[1].get(list(texts), lambda t: self.text.encode(t))

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
        Q = self.fixed(SUGGESTION_POOL)                                     # (q, dim), cached on disk
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

    # ------------------------------------------------------------------ stage 1: CLIPScores with the app's own text
    def stage1(self):
        """Per asset, from its CLIP vector alone (videos: best frame), cached per catalogue:
        quality     similarity to keeper-like prompts minus clutter prompts (screenshots, documents, blur)
        people      'friends posing together / a family photo' minus 'a crowd of strangers / tourists'
        has_people  a posed group or a crowd scores above 'no people' (decides who the face model looks at)
        NaN / False for assets without a vector. Image vectors exist since indexing, so this is a few dot products;
        the prompt vectors come from the on-disk cache."""
        self.maybe_reload()
        c = self.cat
        if getattr(self, "_s1_key", None) == id(c):
            return self._s1
        n = len(c.asset_ids)
        out = dict(quality=np.full(n, np.nan, np.float32), people=np.full(n, np.nan, np.float32),
                   has_people=np.zeros(n, bool), presence=np.full(n, np.nan, np.float32),
                   place=np.full(n, np.nan, np.float32))
        if c.model_id is not None and len(c.emb_idx):
            sets = [COVER_GOOD, COVER_BAD, PEOPLE_POSED, PEOPLE_CROWD, PEOPLE_NONE, PLACE_GOOD, PLACE_BAD]
            try:
                V = self.fixed([t for st in sets for t in st])
            except Exception:                            # no text model available: no preference
                V = None
            if V is not None:
                mean, k = [], 0
                for st in sets:
                    mean.append(V[k:k + len(st)].mean(0)); k += len(st)
                unit = lambda x: x / (np.linalg.norm(x) + 1e-9)
                D = np.stack([mean[0] - mean[1], unit(mean[2]), unit(mean[3]), unit(mean[4]),
                              mean[5] - mean[6]]).astype(np.float32)
                with self._lock:
                    best = np.maximum.reduceat(c.vectors @ D.T, c.f_start[c.emb_idx], axis=0)
                out["quality"][c.emb_idx] = best[:, 0]
                out["people"][c.emb_idx] = best[:, 1] - best[:, 2]
                presence = np.maximum(best[:, 1], best[:, 2]) - best[:, 3]
                out["has_people"][c.emb_idx] = presence > 0
                out["presence"][c.emb_idx] = presence
                out["place"][c.emb_idx] = best[:, 4]
        self._s1_key, self._s1 = id(c), out
        return out

    def cover_scores(self):
        """Cover quality per asset (stage 1)."""
        return self.stage1()["quality"]

    def people_features(self):
        """(known, strangers, crowd) per asset from the face pass; NaN for photos not looked at yet. None when off."""
        w = getattr(self, "faces", None)
        return w.features() if w is not None else None

    def memory_scores(self, idx):
        """Stage 1 (quality + posed people) everywhere; where the face model has looked at a photo, also recurring
        people (up) and prominent strangers and crowds (down)."""
        idx = np.asarray(idx, dtype=np.int64)
        s1 = self.stage1()
        out = _z(s1["quality"][idx].astype(np.float64)) + 0.7 * _z(s1["people"][idx].astype(np.float64))
        f = self.people_features()
        if f is not None and len(idx):
            known, strangers, crowd = (a[idx].astype(np.float64) for a in f)
            seen = np.isfinite(known)
            if seen.any():
                face = np.zeros(len(idx))
                face[seen] = 1.0 * _z(known[seen]) - 0.8 * _z(strangers[seen]) - 0.5 * crowd[seen]
                out = out + face
        return out

    def _candidates(self, idx, prefer_photos=True):
        c = self.cat
        idx = np.array([i for i in idx if c.thumb[i]], dtype=np.int64)
        if prefer_photos and len(idx) and (~c.is_video[idx]).any():
            idx = idx[~c.is_video[idx]]
        sc = self.cover_scores()[idx] if len(idx) else np.zeros(0)
        return idx[np.isfinite(sc) & (c.f_count[idx] > 0)] if np.isfinite(sc).any() else idx

    def face_candidates(self, idx, k=20):
        """The photos of a group worth showing the face model: the top k by stage-1 memory score among those CLIP
        thinks have people in them."""
        idx = self._candidates(idx)
        if not len(idx):
            return idx
        idx = idx[self.stage1()["has_people"][idx]]
        return idx[np.argsort(-self.memory_scores(idx))[:k]] if len(idx) else idx

    def pick_covers(self, idx, k=3, prefer_photos=True, avoid=()):
        """Up to k cover assets from these asset indices: memory score, typical of the group (close to its mean
        vector), and not near-duplicates of each other or of `avoid`."""
        c = self.cat
        idx = self._candidates(idx, prefer_photos)
        if not len(idx) or k <= 0:
            return []
        if not np.isfinite(self.cover_scores()[idx]).any():
            return idx[:k].tolist()
        v = c.vectors[c.f_start[idx]]
        m = v.mean(0)
        m /= np.linalg.norm(m) + 1e-9
        typical = v @ m
        rank = self.memory_scores(idx) + 0.5 * _z(typical)
        taken = [c.vectors[c.f_start[a]] for a in avoid]
        out = []
        for j in np.argsort(-rank):
            if idx[j] in avoid:
                continue
            if all(float(v[j] @ u) < 0.92 for u in taken):
                out.append(j)
                taken.append(v[j])
            if len(out) == k:
                break
        return idx[out].tolist()

    def place_best(self, idx, town_vec, direction=None, k=100):
        """The photo that best defines a place -- the place, not the people in it. Pool: the top k by a place score
        (landmarks, scenery, streets and buildings, minus portraits, selfies and clutter). Rank: CLIPScore with the
        place's name, what sets the place apart from the rest of the library, place score; people count against a
        photo (CLIP's people signal everywhere, and every face found once the face model has looked)."""
        c = self.cat
        idx = self._candidates(idx)
        if not len(idx):
            return None
        s1 = self.stage1()
        pq = np.nan_to_num(s1["place"][idx], nan=-9)
        top = idx[np.argsort(-pq)[:k]]
        v = c.vectors[c.f_start[top]]
        presence = np.nan_to_num(s1["presence"][top])
        sd = np.nanstd(s1["presence"]) or 1.0
        # people count against a photo only when CLIP actually sees them (presence > 0); photos without anyone are
        # not ranked by how strongly they lack people
        score = _z(v @ town_vec) + 0.4 * _z(np.nan_to_num(s1["place"][top])) - 1.0 * np.maximum(presence, 0) / sd
        if direction is not None:
            score += 0.5 * _z(v @ direction)
        f = self.people_features()
        if f is not None:
            faces = np.nan_to_num(f[0][top]) + np.nan_to_num(f[1][top])      # anyone the face model saw, known or not
            score -= 1.0 * (faces > 0) + 0.5 * np.minimum(faces, 2)
        return int(top[int(np.argmax(score))])

    def town_vectors(self, keys):
        """CLIP text vectors describing each place itself: 'a famous landmark in {town}, {country}' and 'the scenery
        of {town}, {country}', averaged (cached on disk, so ~0 ms after the first time)."""
        if not keys:
            return None
        V = self.fixed([p for t, ctry in keys for p in (f"a famous landmark in {t}, {ctry}", f"the scenery of {t}, {ctry}")])
        T = V.reshape(len(keys), 2, -1).mean(1)
        return (T / (np.linalg.norm(T, axis=1, keepdims=True) + 1e-9)).astype(np.float32)

    def town_covers(self, towns):
        """towns: {(town, country): asset indices} -> {(town, country): cover asset index or None}."""
        c = self.cat
        keys = list(towns)
        if not keys or c.model_id is None or not len(c.emb_idx):
            return {k: (self.pick_covers(towns[k], 1) or [None])[0] for k in keys}
        T = self.town_vectors(keys)
        lib_mean = c.vectors[c.f_start[c.emb_idx]].mean(0)
        out = {}
        for j, key in enumerate(keys):
            ids = np.asarray(towns[key])
            ids = ids[c.f_count[ids] > 0]
            d = c.vectors[c.f_start[ids]].mean(0) - lib_mean if len(ids) > 1 else None
            out[key] = self.place_best(ids, T[j], None if d is None else d / (np.linalg.norm(d) + 1e-9))
        return out

    def trip_covers(self, idx, town_vec=None):
        """Three postcard slots: the best photo with one of your people (once the face model has looked), the photo
        that best defines the trip's main place, then the best of the rest; no near-duplicates between them."""
        idx = np.asarray(idx, dtype=np.int64)
        chosen = []
        f = self.people_features()
        if f is not None and len(idx):
            known = f[0][idx]
            chosen += self.pick_covers(idx[np.nan_to_num(known) > 0], 1)
        if town_vec is not None and len(idx):
            located = idx[np.isfinite(self.cat.lat[idx])]
            best = self.place_best([i for i in located if i not in chosen], town_vec) if len(located) else None
            if best is not None:
                chosen += self.pick_covers([best], 1, avoid=chosen)
        chosen += self.pick_covers(idx, 3 - len(chosen), avoid=chosen)
        return chosen[:3]

    # ------------------------------------------------------------------ browsing
    def browse(self, filters=None, k=200, offset=0, order="desc"):
        self.maybe_reload()
        if order == "best":                              # nicest covers first (Memories, Rediscover)
            score = self.cover_scores()
        with self._lock:
            c = self.cat
            idx = np.flatnonzero(self.mask(filters))
            if order == "best":
                sc = self.memory_scores(idx) if len(idx) else score[idx]
                sc = sc - np.where(c.is_video[idx], 0.3, 0.0)
                o = np.lexsort((c.asset_ids[idx].astype(str), -np.where(np.isfinite(sc), sc, -np.inf)))
                sel = idx[o][offset:offset + k]
                return dict(total=int(len(idx)), offset=offset, items=[c.summary(i) for i in sel])
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
        sc = self.cover_scores()                         # before taking the lock (it may encode text)
        with self._lock:
            c = self.cat
            m = self.mask(filters) & np.isfinite(c.lat)
            idx = np.flatnonzero(m)[:limit]
            return dict(total=int(m.sum()), asset_ids=c.asset_ids[idx].tolist(),
                        lat=np.round(c.lat[idx], 5).tolist(), lon=np.round(c.lon[idx], 5).tolist(),
                        video=c.is_video[idx].astype(int).tolist(),
                        # capture year (0 = undated) for the map's time slider; 1 = has a thumbnail for photo pins
                        year=[_year(x) for x in c.t[idx]], thumb=[1 if c.thumb[i] else 0 for i in idx],
                        cover=self._cover_rank(idx, sc))

    def _cover_rank(self, idx, sc):
        """Cover score as a 0-100 percentile within the library (photos above videos), for picking map pin photos."""
        if not np.isfinite(sc).any():
            return [0] * len(idx)
        ok = np.isfinite(sc)
        ranks = np.zeros(len(sc))
        ranks[ok] = np.argsort(np.argsort(sc[ok])) / max(1, ok.sum() - 1) * 100
        r = ranks[idx] - np.where(self.cat.is_video[idx], 30, 0)
        return np.clip(np.round(r), 0, 100).astype(int).tolist()

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


def _z(x):
    """Standard score, NaN-safe; all-equal or empty input gives zeros."""
    x = np.asarray(x, dtype=np.float64)
    if not len(x):
        return x
    sd = np.nanstd(x)
    return np.nan_to_num((x - np.nanmean(x)) / sd) if sd > 0 else np.zeros(len(x))


def haversine_km(lat1, lon1, lat2, lon2):
    with np.errstate(invalid="ignore"):
        p1, p2 = np.radians(lat1), np.radians(lat2)
        dp, dl = p2 - p1, np.radians(lon2) - np.radians(lon1)
        a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
        d = 2 * EARTH_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    return np.where(np.isfinite(d), d, np.inf)


def _ts(x):
    return None if np.isnan(x) else round(float(x), 3)


# "Good cover" score: how much a photo looks like a keeper rather than clutter, from its CLIP vector alone
COVER_GOOD = [
    "a beautiful photograph", "a scenic travel photo of a landscape", "a happy photo of friends and family",
    "a photo of a famous landmark", "a colourful photo of a city street", "a photo of delicious food on a table",
    "a cute photo of a pet", "a portrait photo of a person", "a birthday party or celebration",
]
COVER_BAD = [
    "a screenshot of a phone screen", "a photo of a document, receipt or printed text", "a blurry, dark, out of focus photo",
    "a meme with text", "a photo of a computer screen or whiteboard", "a photo of a QR code or barcode",
]

# stage-1 people prompts: posed photos of your group vs crowds of strangers vs no people (checked on CC0 photos)
PEOPLE_POSED = ["friends posing together for a photo", "a selfie of a group of friends smiling at the camera",
                "a family photo"]
PEOPLE_CROWD = ["a crowd of strangers", "many tourists in the background", "people walking in a busy street"]
PEOPLE_NONE = ["a landscape with no people", "a photo of food", "a photo of a building with no people"]

# stage-1 place prompts: what makes a photo show a place rather than the people in it (map pins, place slots)
PLACE_GOOD = ["a famous landmark", "a scenic view of a landscape", "a city skyline", "a historic building",
              "a street in a town", "a view of a beach, mountain or lake"]
PLACE_BAD = ["a portrait photo of a person", "a selfie", "people posing for a photo", "a close-up of a face",
             "a screenshot of a phone screen", "a photo of a document, receipt or printed text"]

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
