"""RoamEngine local API for the RoamIndex desktop app (and scripts). Standard library HTTP server only.

Security model (single local user):
- binds 127.0.0.1 only; nothing is reachable from the network, and nothing is ever sent out;
- every request needs the per-launch token (header `X-Roam-Token` or `?token=` for <img>/<video> sources);
- the Host header must be 127.0.0.1/localhost (defeats DNS rebinding); CORS only for the app's own origins.
On start it prints one JSON line {"event": "ready", "port": ..., "token": ...} to stdout.
"""
import argparse
import datetime as dt
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from . import __version__, journeys, memories
from .engine import Engine, SearchFilters
from .evaluation import Labels, evaluate
from .ingest_manager import IngestManager
from .media import PHOTO_MIME, VIDEO_MIME, frame_jpeg, preview_jpeg

ALLOWED_ORIGINS = {"http://tauri.localhost", "https://tauri.localhost", "tauri://localhost",
                   "http://localhost:1420", "http://127.0.0.1:1420"}
ASSET_RE = r"([0-9a-f]{8,64})"
HOME_RULES = 2        # bump when the rules for trips or memory cards change: an older disk copy is then not shown


def guess_storage(path):
    """'ssd' or 'hdd' for the drive holding `path` (Windows: bus type and media type of the physical disk).
    USB drives count as 'hdd': one sequential reader is right for both USB hard drives and most USB sticks.
    Anything unknown also gives 'hdd', the safe choice (slower on an SSD, never thrashes a hard drive)."""
    drive = os.path.splitdrive(os.path.abspath(path))[0].rstrip(":") if path else ""
    if os.name != "nt" or len(drive) != 1 or not drive.isalpha():
        return dict(storage="hdd", reason="unknown drive")
    ps = (f"$d = Get-Partition -DriveLetter {drive} -ErrorAction Stop | Get-Disk; "
          "$p = Get-PhysicalDisk | Where-Object DeviceId -eq $d.Number; \"$($d.BusType)|$($p.MediaType)\"")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                           text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        bus, media = (r.stdout.strip().split("|") + ["", ""])[:2]
    except (OSError, subprocess.TimeoutExpired):
        return dict(storage="hdd", reason="unknown drive")
    if bus.upper() == "USB":
        return dict(storage="hdd", reason="external USB drive")
    if media.upper() == "SSD" or bus.upper() == "NVME":
        return dict(storage="ssd", reason="internal SSD")
    if media.upper() == "HDD":
        return dict(storage="hdd", reason="hard drive")
    return dict(storage="hdd", reason="unknown drive type")


class Api:
    """Routing table and handlers; independent of the HTTP plumbing so tests can call it directly."""

    def __init__(self, library, text_device="cpu", python=None):
        self.library = os.path.abspath(library)
        self.engine = Engine(self.library, text_device=text_device)
        self.labels = Labels(self.library)
        self._gazetteer = None
        self._home = None                                # see home(): trips, memory cards, town covers
        self._home_lock = threading.Lock()
        self._disk = None                                # the last home screen from disk, until home() is ready
        self.memories_solo_ok = False                    # True: keep memories without 2+ people (demo screenshots)
        self.ingest = IngestManager(self.library, python=python)
        self.routes = [
            ("GET", r"/api/health$", self.health),
            ("GET", r"/api/library$", self.library_info),
            ("GET", r"/api/search$", self.search),
            ("GET", rf"/api/similar/{ASSET_RE}$", self.similar),
            ("GET", r"/api/browse$", self.browse),
            ("GET", r"/api/timeline$", self.timeline),
            ("GET", r"/api/points$", self.points),
            ("GET", r"/api/suggestions$", lambda p, b: self.engine.suggestions(_int(p, "n", 12, 50))),
            ("GET", rf"/api/asset/{ASSET_RE}$", self.asset),
            ("POST", r"/api/reload$", self.reload),
            ("GET", r"/api/ingest$", lambda p, b: self.ingest.status()),
            ("POST", r"/api/ingest$", self.ingest_start),
            ("POST", r"/api/ingest/cancel$", lambda p, b: self.ingest.cancel()),
            ("GET", r"/api/journeys$", lambda p, b: self.journeys()),
            ("GET", r"/api/memories$", lambda p, b: self.memories()),
            ("GET", r"/api/faces/status$", lambda p, b: self.faces_status()),
            ("GET", r"/api/storage-guess$", lambda p, b: guess_storage(p.get("path", ""))),
            ("GET", r"/api/ingest/glimpses$", lambda p, b: dict(items=self.glimpses(_int(p, "n", 24, 100)))),
            ("GET", r"/api/labels/queries$", lambda p, b: dict(queries=self.labels.queries())),
            ("POST", r"/api/labels/queries$", self.add_query),
            ("DELETE", r"/api/labels/queries/(\d+)$", self.delete_query),
            ("GET", r"/api/labels/queries/(\d+)/judgments$", self.judgments),
            ("POST", r"/api/labels/judge$", self.judge),
            ("GET", r"/api/places$", lambda p, b: dict(recent=self.engine.places.recent(_int(p, "n", 12, 50)))),
            ("POST", r"/api/places/set$", self.set_place),
            ("GET", r"/api/geocode$", self.geocode),
            ("POST", r"/api/places/clear$", self.clear_place),
            ("GET", rf"/api/places/suggest/{ASSET_RE}$", self.place_suggest),
            ("GET", r"/api/eval$", self.eval),
        ]

    # --- handlers: (params, body, *groups) -> JSON-able
    def health(self, p, b):
        return dict(ok=True, version=__version__, library=self.library)

    def library_info(self, p, b):
        self.engine.maybe_reload()
        return self.engine.cat.stats()

    def search(self, p, b):
        return self.engine.search(p.get("q", ""), SearchFilters.from_params(p), k=_int(p, "k", 60, 500),
                                  offset=_int(p, "offset", 0, 10**7))

    def similar(self, p, b, aid):
        fi = p.get("frame")
        return self.engine.similar(aid, SearchFilters.from_params(p), k=_int(p, "k", 60, 500),
                                   offset=_int(p, "offset", 0, 10**7), frame_index=int(fi) if fi else None)

    def browse(self, p, b):
        return self.engine.browse(SearchFilters.from_params(p), k=_int(p, "k", 200, 1000),
                                  offset=_int(p, "offset", 0, 10**7), order=p.get("order", "desc"))

    def timeline(self, p, b):
        return self.engine.timeline(SearchFilters.from_params(p))

    def asset(self, p, b, aid):
        return self.engine.asset(aid)

    def reload(self, p, b):
        self.engine.maybe_reload(force=True)
        return self.engine.cat.stats()

    def ingest_start(self, p, b):
        roots = b.get("roots") or [s["root"] for s in self.engine.cat.sources]
        return self.ingest.start(roots, storage=b.get("storage", "hdd"))

    def add_query(self, p, b):
        return dict(query_id=self.labels.add_query(b["text"], b.get("note")))

    def delete_query(self, p, b, qid):
        self.labels.delete_query(int(qid))
        return dict(ok=True)

    def judgments(self, p, b, qid):
        return dict(judgments=self.labels.judgments(int(qid)))

    def judge(self, p, b):
        self.labels.judge(int(b["query_id"]), b["asset_id"], b.get("grade"))
        return dict(ok=True)

    def set_place(self, p, b):
        ids = b.get("asset_ids") or []
        if not isinstance(ids, list) or not ids or len(ids) > 100000:
            raise ValueError("asset_ids must be a non-empty list")
        return dict(updated=self.engine.set_place([str(a) for a in ids], b["lat"], b["lon"], b.get("name")))

    def clear_place(self, p, b):
        ids = b.get("asset_ids") or []
        if not isinstance(ids, list):
            raise ValueError("asset_ids must be a list")
        return dict(cleared=self.engine.clear_place([str(a) for a in ids]))

    def geocode(self, p, b):
        from .geocode import search_online
        q, n = p.get("q", ""), _int(p, "n", 8, 20)
        if p.get("online") in ("1", "true"):
            return dict(results=search_online(q, n), source="osm",
                        attribution="© OpenStreetMap contributors (nominatim.openstreetmap.org)")
        return dict(results=self.gazetteer().search(q, n), source="geonames", attribution="GeoNames (CC BY 4.0)")

    def place_suggest(self, p, b, aid):
        return dict(suggestions=self.engine.place_suggestions(aid))

    def eval(self, p, b):
        return evaluate(self.engine, self.labels)

    # --- binary endpoints
    def gazetteer(self):
        if self._gazetteer is None:
            from .geocode import Gazetteer
            self._gazetteer = Gazetteer()
        return self._gazetteer

    # --- home screen and map: trips, memory cards, town covers
    def _home_key(self):
        cat = self.engine.cat
        return (id(cat), len(cat.asset_ids), float(np.nansum(cat.lat)), float(np.nansum(cat.lon)),
                dt.date.today().strftime("%Y-%m"))

    def _faces(self):
        return getattr(self.engine, "faces", None)

    def home(self, wait=True):
        """Everything the home screen and the map need. Built once per library state (normally by the warm-up
        thread at start); when the face pass learns something, only the covers are re-chosen (milliseconds).
        wait=False returns None instead of blocking while it is first built (callers then serve the disk copy)."""
        self.engine.maybe_reload()
        key = self._home_key()
        h = self._home
        if h is None or h["key"] != key:
            if not wait and self._disk is not None:
                return None
            with self._home_lock:
                h = self._home
                if h is None or h["key"] != key:
                    h = self._home = self._build_home(key)
        w = self._faces()
        fv = w.version if w is not None else 0
        if h["fv"] != fv:
            with self._home_lock:
                if h["fv"] != fv:
                    self._fill_covers(h)
                    h["fv"] = fv
                    if w is None or (w.ready and not w.busy()):
                        self._save_disk(h)
        return h

    def _build_home(self, key):
        e, cat = self.engine, self.engine.cat
        st = journeys.structure(cat, self.gazetteer())
        h = dict(key=key, st=st, cands=memories.candidates(cat, st["trips"]), cards=[], fv=None,
                 day_of=np.where(np.isfinite(cat.t), cat.t // 86400, -1).astype(np.int64),
                 town_vec=dict(zip(st["town_keys"], e.town_vectors(st["town_keys"])))
                 if st["town_keys"] and cat.model_id else {})
        w = self._faces()
        self._fill_covers(h)
        h["fv"] = w.version if w is not None else 0
        self._request_faces(h)
        if w is None or w.ready:                         # never replace a good disk copy with CLIP-only covers
            self._save_disk(h)
        return h

    def _fill_covers(self, h):
        e, cat, st = self.engine, self.engine.cat, h["st"]
        town_covers = e.town_covers(st["towns"])
        trip_cov = [e.trip_covers(t["_members"], h["town_vec"].get(t["_town"])) for t in st["trips"]]
        # the row: shared memories only (unless --memories-solo-ok), and never the same day on two cards
        shared = (lambda m: True) if self.memories_solo_ok else e.shared
        h["cards"] = memories.select(h["cands"], h["day_of"], shared)
        for c in h["cards"]:
            ids = (e.trip_covers(c["_members"], h["town_vec"].get(c["_town"])) if c["kind"] == "trip"
                   else e.memory_covers(c["_members"], 3))
            c["cover"] = [str(cat.asset_ids[i]) for i in ids or []]
        h["journeys"] = journeys.public(st, cat, trip_cov, town_covers)
        h["towns"] = [dict(name=k[0], country=k[1],
                           cover=str(cat.asset_ids[town_covers[k]]) if town_covers.get(k) is not None else None)
                      for k in st["town_keys"]]

    def _request_faces(self, h):
        """Queue the face model on candidates only: the first cards' top 20 now, a few people photos per month next
        (who recurs), then the other cards' and the towns' candidates."""
        w = self._faces()
        if w is None:
            return
        e, cat = self.engine, self.engine.cat
        ids = lambda arr: [str(cat.asset_ids[i]) for i in arr]
        for n, c in enumerate(h["cands"]):                # every candidate card: the shared check needs them
            w.request(ids(e.face_candidates(c["_members"], 20)), 0 if n < memories.FIRST else 2)
        for c in h["cands"][:memories.MAX_CARDS + 4]:   # more people photos per card: find the whole-group shot
            w.request(ids(e.face_candidates(c["_members"], 60)), 2)
        for members in memories.months(cat).values():
            w.request(ids(e.face_candidates(members, 5)), 1)
        q, has = e.cover_scores(), e.stage1()["has_people"]
        for members in h["st"]["towns"].values():
            top = members[np.argsort(-np.nan_to_num(q[members], nan=-9))[:20]]
            w.request(ids(top[has[top]]), 2)

    def _disk_path(self):
        return os.path.join(self.library, "cache", "home.json")

    def _disk_key(self):
        cat, pl = self.engine.cat, os.path.join(self.library, "places.sqlite")
        return json.loads(json.dumps([cat.stamp, os.path.getmtime(pl) if os.path.exists(pl) else 0, cat.model_id,
                                      dt.date.today().strftime("%Y-%m"), HOME_RULES, self.memories_solo_ok]))

    def _save_disk(self, h):
        try:
            os.makedirs(os.path.dirname(self._disk_path()), exist_ok=True)
            tmp = self._disk_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(dict(key=self._disk_key(), journeys=h["journeys"], cards=memories.public(h["cards"]),
                               towns=h["towns"]), f)
            os.replace(tmp, self._disk_path())
        except OSError:
            pass

    def load_disk_home(self):
        """The home screen as last computed, if it still belongs to this library state: shown at once on launch."""
        try:
            with open(self._disk_path(), encoding="utf-8") as f:
                d = json.load(f)
            if d.get("key") == self._disk_key():
                self._disk = d
        except (OSError, ValueError):
            pass

    def journeys(self):
        h = self.home(wait=False)
        return self._disk["journeys"] if h is None else h["journeys"]

    def memories(self):
        h = self.home(wait=False)
        w = self._faces()
        if h is None:                                    # the saved row (only ever saved when final): show it, check again
            n = len(self._disk["cards"])
            return dict(cards=self._disk["cards"], version=-1, checked=n, total=n, refining=False, provisional=True)
        checked = sum(1 for c in h["cards"] if c["_checked"])
        return dict(cards=memories.public(h["cards"]), version=h["fv"], checked=checked, total=len(h["cards"]), provisional=False,
                    refining=bool(w is not None and (w.busy() or checked < len(h["cards"]) or not w.ready)))

    def points(self, p, b):
        h = self.home()
        out = self.engine.points(SearchFilters.from_params(p))
        cat, town_of = self.engine.cat, h["st"]["town_of"]
        out["town"] = [int(town_of[cat.index[a]]) for a in out["asset_ids"]]
        out["towns"] = h["towns"]
        return out

    def faces_status(self):
        w = getattr(self.engine, "faces", None)
        return dict(w.status) if w is not None else dict(state="off")

    def _manifest(self):
        """Read-only view of the manifest, which an ingest run may be writing right now (WAL: readers never block it)."""
        path = os.path.join(self.library, "manifest.sqlite")
        if not os.path.exists(path):
            return None
        return sqlite3.connect(f"file:{urllib.parse.quote(path.replace(os.sep, '/'))}?mode=ro", uri=True, timeout=2)

    def glimpses(self, n):
        """Random items that already have a thumbnail, including ones indexed moments ago by a running ingest
        (not yet in the search catalogue). For the slideshow shown while indexing; ids and dates only."""
        db = self._manifest()
        if db is None:
            return []
        try:
            rows = db.execute("SELECT asset_id, media_type, capture_time_utc, capture_time_local FROM assets "
                              "WHERE thumbnail_ref IS NOT NULL ORDER BY random() LIMIT ?", (n,)).fetchall()
        except sqlite3.Error:                            # schema not created yet by a first run
            return []
        finally:
            db.close()
        return [dict(asset_id=a, media_type=t, capture_time=u or l, capture_time_is_utc=bool(u)) for a, t, u, l in rows]

    def thumb_path(self, aid):
        c = self.engine.cat
        i = c.index.get(aid)
        if i is None:                                    # indexed after the catalogue was loaded (ingest running)
            return self._fresh_thumb(aid)
        ref = c.thumb[i]
        if not ref:
            return None
        path = os.path.normpath(os.path.join(self.library, ref))
        return path if path.startswith(self.library + os.sep) and os.path.exists(path) else None

    def _fresh_thumb(self, aid):
        db = self._manifest()
        if db is None:
            raise KeyError(aid)
        try:
            row = db.execute("SELECT thumbnail_ref FROM assets WHERE asset_id = ?", (aid,)).fetchone()
        except sqlite3.Error:
            row = None
        finally:
            db.close()
        if not row:
            raise KeyError(aid)
        if not row[0]:
            return None
        path = os.path.normpath(os.path.join(self.library, row[0]))
        return path if path.startswith(self.library + os.sep) and os.path.exists(path) else None

    def original(self, aid):
        self.engine.cat.index[aid]                       # KeyError -> 404
        return self.engine.cat.primary_path(aid)


def _int(p, k, default, hi):
    try:
        return max(0, min(hi, int(p.get(k, default))))
    except ValueError:
        return default


class Handler(BaseHTTPRequestHandler):
    api: Api = None
    token: str = None
    port: int = 0
    protocol_version = "HTTP/1.1"
    server_version = "RoamEngine"

    def log_message(self, *a):                           # no request logging: URLs can carry search text
        pass

    # --- plumbing
    def _origin_headers(self):
        o = self.headers.get("Origin")
        if o in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", o)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "X-Roam-Token, Content-Type, Range")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Expose-Headers", "Content-Range, Accept-Ranges, Content-Length")

    def _send(self, code, body=b"", ctype="application/json", extra=None):
        self.send_response(code)
        self._origin_headers()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if ctype == "application/json" else "private, max-age=3600")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, default=str).encode())

    def _authorised(self, params):
        host = (self.headers.get("Host") or "").lower()
        if host not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            return False
        tok = self.headers.get("X-Roam-Token") or params.get("token")
        return bool(tok) and secrets.compare_digest(tok, self.token)

    def do_OPTIONS(self):
        self.send_response(204)
        self._origin_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        u = urllib.parse.urlsplit(self.path)
        params = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        if not self._authorised(params):
            return self._json(403, dict(error="forbidden"))
        try:
            m = re.fullmatch(rf"/(thumb|preview|frame|media)/{ASSET_RE}", u.path)
            if m and method == "GET":
                return self._binary(m.group(1), m.group(2), params)
            body = {}
            if method == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            for meth, pat, fn in self.api.routes:
                if meth == method:
                    mm = re.match(pat, u.path)
                    if mm:
                        return self._json(200, fn(params, body, *mm.groups()))
            return self._json(404, dict(error="not found"))
        except KeyError as e:
            return self._json(404, dict(error=f"unknown: {e}"))
        except (ValueError, FileNotFoundError, RuntimeError) as e:
            return self._json(400, dict(error=str(e)))
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            return self._json(500, dict(error=type(e).__name__))

    def _binary(self, kind, aid, p):
        api = self.api
        if kind == "thumb":
            path = api.thumb_path(aid)
            if not path:
                return self._json(404, dict(error="no thumbnail"))
            with open(path, "rb") as f:
                return self._send(200, f.read(), "image/jpeg")
        path = api.original(aid)
        if not path:
            return self._json(404, dict(error="original not available (missing or drive disconnected)"))
        ext = os.path.splitext(path)[1].lower()
        if kind == "preview":
            return self._send(200, preview_jpeg(path, _int(p, "max", 2048, 8192)), "image/jpeg")
        if kind == "frame":
            return self._send(200, frame_jpeg(path, float(p.get("t", 0)), _int(p, "max", 1280, 4096)), "image/jpeg")
        ctype = VIDEO_MIME.get(ext) or PHOTO_MIME.get(ext) or mimetypes.guess_type(path)[0] or \
            "application/octet-stream"
        return self._stream(path, ctype)

    def _stream(self, path, ctype):
        size = os.path.getsize(path)
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng or "")
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        length = end - start + 1
        self.send_response(206 if m else 200)
        self._origin_headers()
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if m:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:                      # read-only
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # The home screen asks for ~40 thumbnails at once. Python's default listen backlog is 5: while start-up work keeps
    # the accept loop busy, further connections were refused and the app showed broken images.
    request_queue_size = 128


def serve(library, port=0, token=None, text_device="cpu", python=None, ready=print, faces=False,
          memories_solo_ok=False):
    api = Api(library, text_device=text_device, python=python)
    api.memories_solo_ok = memories_solo_ok
    if faces:                                            # background face pass for people-aware covers
        from .faces import FaceWorker
        api.engine.faces = FaceWorker(api.engine, api.library).start()

    def warm_home():                                     # home screen ready before the user gets there
        api.load_disk_home()
        try:
            api.home()
        except Exception:                                # an empty library or no model yet: built on demand
            pass
    threading.Thread(target=warm_home, daemon=True, name="warm-home").start()
    token = token or secrets.token_urlsafe(24)
    httpd = _Server(("127.0.0.1", port), Handler)
    Handler.api, Handler.token, Handler.port = api, token, httpd.server_address[1]
    if api.engine.cat.model_id:
        threading.Thread(target=lambda: api.engine.text, daemon=True).start()  # warm the text model
    ready(json.dumps(dict(event="ready", port=Handler.port, token=token, library=api.library)))
    sys.stdout.flush()
    return httpd


def main(argv=None):
    ap = argparse.ArgumentParser(prog="roamengine serve")
    ap.add_argument("--library", required=True)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--token", default=os.environ.get("ROAM_TOKEN"))
    ap.add_argument("--text-device", default="cpu")
    ap.add_argument("--no-faces", action="store_true",
                    help="do not look for faces (people-aware covers); faces are found on thumbnails and stay local")
    ap.add_argument("--memories-solo-ok", action="store_true",
                    help="keep memories without a photo of two or more people (the CC0 demo library has almost none)")
    ap.add_argument("--exit-with-parent", action="store_true",
                    help="exit when stdin closes (the desktop app holds our stdin open while it runs)")
    a = ap.parse_args(argv)
    os.makedirs(a.library, exist_ok=True)
    httpd = serve(a.library, a.port, a.token, a.text_device, faces=not a.no_faces,
                  memories_solo_ok=a.memories_solo_ok)
    if a.exit_with_parent:
        threading.Thread(target=lambda: (sys.stdin.read(), os._exit(0)), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
