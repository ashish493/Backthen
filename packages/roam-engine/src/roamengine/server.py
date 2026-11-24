"""RoamEngine local API for the RoamIndex desktop app (and scripts). Standard library HTTP server only.

Security model (single local user):
- binds 127.0.0.1 only; nothing is reachable from the network, and nothing is ever sent out;
- every request needs the per-launch token (header `X-Roam-Token` or `?token=` for <img>/<video> sources);
- the Host header must be 127.0.0.1/localhost (defeats DNS rebinding); CORS only for the app's own origins.
On start it prints one JSON line {"event": "ready", "port": ..., "token": ...} to stdout.
"""
import argparse
import json
import mimetypes
import os
import re
import secrets
import sys
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from .engine import Engine, SearchFilters
from .evaluation import Labels, evaluate
from .ingest_manager import IngestManager
from .media import PHOTO_MIME, VIDEO_MIME, frame_jpeg, preview_jpeg

ALLOWED_ORIGINS = {"http://tauri.localhost", "https://tauri.localhost", "tauri://localhost",
                   "http://localhost:1420", "http://127.0.0.1:1420"}
ASSET_RE = r"([0-9a-f]{8,64})"


class Api:
    """Routing table and handlers; independent of the HTTP plumbing so tests can call it directly."""

    def __init__(self, library, text_device="cpu", python=None):
        self.library = os.path.abspath(library)
        self.engine = Engine(self.library, text_device=text_device)
        self.labels = Labels(self.library)
        self._gazetteer = None
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

    def points(self, p, b):
        return self.engine.points(SearchFilters.from_params(p))

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
        from .geocode import Gazetteer, search_online
        q, n = p.get("q", ""), _int(p, "n", 8, 20)
        if p.get("online") in ("1", "true"):
            return dict(results=search_online(q, n), source="osm",
                        attribution="© OpenStreetMap contributors (nominatim.openstreetmap.org)")
        if self._gazetteer is None:
            self._gazetteer = Gazetteer()
        return dict(results=self._gazetteer.search(q, n), source="geonames", attribution="GeoNames (CC BY 4.0)")

    def place_suggest(self, p, b, aid):
        return dict(suggestions=self.engine.place_suggestions(aid))

    def eval(self, p, b):
        return evaluate(self.engine, self.labels)

    # --- binary endpoints
    def thumb_path(self, aid):
        c = self.engine.cat
        ref = c.thumb[c.index[aid]]
        if not ref:
            return None
        path = os.path.normpath(os.path.join(self.library, ref))
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


def serve(library, port=0, token=None, text_device="cpu", python=None, ready=print):
    api = Api(library, text_device=text_device, python=python)
    token = token or secrets.token_urlsafe(24)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
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
    ap.add_argument("--exit-with-parent", action="store_true",
                    help="exit when stdin closes (the desktop app holds our stdin open while it runs)")
    a = ap.parse_args(argv)
    os.makedirs(a.library, exist_ok=True)
    httpd = serve(a.library, a.port, a.token, a.text_device)
    if a.exit_with_parent:
        threading.Thread(target=lambda: (sys.stdin.read(), os._exit(0)), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
