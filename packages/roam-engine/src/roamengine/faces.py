"""Faces -> recurring people, for people-aware cover photos (all local).

Detector YuNet (MIT) and recogniser SFace (Apache-2.0) from the OpenCV model zoo, run by OpenCV's own DNN module on the
library's 512 px thumbnails (never the originals, so an external archive drive is not touched). Each face gets a
128-d vector; vectors stay in the library folder (faces.sqlite) and never leave the machine.

Clustering follows the shape Apple describes for Photos: a strict greedy pass, then agglomerative merging of the
clusters. A cluster is a *recurring person* when it appears on many distinct days across several months; everyone else
counts as a stranger. Per photo that gives:
  known      prominence-weighted importance of recurring people in it
  strangers  how prominent the unknown faces are (tiny background faces ignored)
  crowd      many unknown faces
"""
import collections
import datetime as dt
import hashlib
import math
import os
import sqlite3
import threading
import time
import urllib.request

import numpy as np

MODELS = {
    "yunet": ("face_detection_yunet_2023mar.onnx",
              "https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
              "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "sface": ("face_recognition_sface_2021dec.onnx",
              "https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx",
              "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
}
DET_SCORE = 0.75          # YuNet under OpenCV 5 scores real faces ~0.75-0.95; 0.9 (the zoo demo default) drops many
MIN_FACE_PX = 20          # smaller faces in a 512 px thumbnail are background
SEED_AREA = 0.004         # faces smaller than 0.4% of the image, or less certain, never start a person
SEED_SCORE = 0.85
GREEDY_SIM = 0.45         # SFace same-person cosine on LFW is 0.363; start stricter so clusters stay pure
MERGE_SIM = 0.40
RECUR_DAYS, RECUR_MONTHS, TOP_PEOPLE = 6, 3, 30
STRANGER_AREA = 0.01      # unknown faces below 1% of the image are ignored
CROWD = 6


NO_VECTOR = np.zeros(128, np.float32)                # stored for faces not worth recognising; matches nobody


def models_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "Backthen", "models")


def ensure_models(folder=None, log=lambda *_: None):
    """Download the two model files once (official OpenCV repos, checksum-verified). Returns their paths."""
    folder = folder or models_dir()
    os.makedirs(folder, exist_ok=True)
    out = {}
    for key, (name, url, sha) in MODELS.items():
        path = os.path.join(folder, name)
        if not (os.path.exists(path) and _sha256(path) == sha):
            log(f"downloading {name}")
            tmp = path + ".part"
            req = urllib.request.Request(url, headers={"User-Agent": "Backthen (local photo search)"})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            if _sha256(tmp) != sha:
                os.remove(tmp)
                raise RuntimeError(f"checksum mismatch for {name}")
            os.replace(tmp, path)
        out[key] = path
    return out


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------------------------- storage
class FaceStore:
    def __init__(self, library):
        self.path = os.path.join(library, "faces.sqlite")
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS scanned(asset_id TEXT PRIMARY KEY, n INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS faces(asset_id TEXT NOT NULL, x REAL, y REAL, w REAL, h REAL,
                                                 area REAL NOT NULL, score REAL NOT NULL, vec BLOB NOT NULL);
                CREATE INDEX IF NOT EXISTS faces_asset ON faces(asset_id);""")

    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def scanned(self):
        with self._db() as db:
            return {r[0] for r in db.execute("SELECT asset_id FROM scanned")}

    def add(self, rows, scanned):
        """Store one batch; assets another process already stored are skipped (two engines on one library)."""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            ids = [a for a, _ in scanned]
            have = set()
            for k in range(0, len(ids), 500):
                q = ids[k:k + 500]
                have |= {r[0] for r in db.execute(f"SELECT asset_id FROM scanned WHERE asset_id IN ({','.join('?' * len(q))})", q)}
            db.executemany("INSERT INTO faces VALUES (?,?,?,?,?,?,?,?)", [r for r in rows if r[0] not in have])
            db.executemany("INSERT OR IGNORE INTO scanned VALUES (?,?)", [x for x in scanned if x[0] not in have])

    def load(self):
        with self._db() as db:
            rows = db.execute("SELECT asset_id, area, score, vec FROM faces").fetchall()
        if not rows:
            return [], np.zeros(0), np.zeros(0), np.zeros((0, 128), np.float32)
        ids = [r[0] for r in rows]
        area = np.array([r[1] for r in rows], float)
        score = np.array([r[2] for r in rows], float)
        vec = np.stack([np.frombuffer(r[3], np.float32) for r in rows])
        return ids, area, score, vec


# ---------------------------------------------------------------------------------------------- detection
class FaceModel:
    def __init__(self, paths):
        import cv2
        cv2.setNumThreads(2)                             # a background pass: ~2x faster than 1 thread, CPU left for search
        self.cv2 = cv2
        self.det = cv2.FaceDetectorYN.create(paths["yunet"], "", (320, 320), DET_SCORE, 0.3, 5000)
        self.rec = cv2.FaceRecognizerSF.create(paths["sface"], "")

    def faces(self, path):
        cv2 = self.cv2
        im = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
        if im is None:
            return []
        h, w = im.shape[:2]
        self.det.setInputSize((w, h))
        _, found = self.det.detect(im)
        out = []
        for f in ([] if found is None else found):
            if min(f[2], f[3]) < MIN_FACE_PX:
                continue
            area = float(f[2] * f[3] / (w * h))
            if area < SEED_AREA or f[14] < SEED_SCORE:
                v = NO_VECTOR                            # tiny or unsure: only its size counts (strangers), skip SFace
            else:
                v = self.rec.feature(self.rec.alignCrop(im, f)).ravel().astype(np.float32)
                v /= np.linalg.norm(v) + 1e-9
            out.append((float(f[0] / w), float(f[1] / h), float(f[2] / w), float(f[3] / h), area, float(f[14]), v))
        return out


# ---------------------------------------------------------------------------------------------- clustering
def cluster(vec, seed):
    """Labels per face (-1 = unassigned). Greedy pass: faces in the order given; a seed-capable face that matches no
    cluster starts one, others join only an existing cluster. Then clusters with close centroids merge."""
    n = len(vec)
    labels = np.full(n, -1, int)
    cents, counts = [], []
    C = np.zeros((0, vec.shape[1] if n else 128), np.float32)
    for i in range(n):
        if len(cents):
            s = C @ vec[i]
            j = int(np.argmax(s))
            if s[j] >= GREEDY_SIM:
                labels[i] = j
                counts[j] += 1
                c = cents[j] + (vec[i] - cents[j]) / counts[j]
                cents[j] = c
                C[j] = c / (np.linalg.norm(c) + 1e-9)
                continue
        if seed[i]:
            labels[i] = len(cents)
            cents.append(vec[i].copy())
            counts.append(1)
            C = np.vstack([C, vec[i][None]])
    # agglomerative merge of centroids (average linkage approximated by size-weighted centroids)
    # only clusters with 2+ faces take part: one-off faces cannot make anyone recurring, and this keeps the
    # similarity matrix small (a few thousand clusters at most on a large library)
    multi = [j for j in range(len(cents)) if counts[j] >= 2]
    k = len(multi)
    if k > 1:
        P = (np.array([cents[j] for j in multi]) * np.array([counts[j] for j in multi])[:, None]).astype(np.float32)
        alive = np.ones(k, bool)
        U = P / np.linalg.norm(P, axis=1, keepdims=True)
        S = U @ U.T
        np.fill_diagonal(S, -1)
        parent = np.arange(k)
        while True:
            a, b = np.unravel_index(np.argmax(S), S.shape)
            if S[a, b] < MERGE_SIM:
                break
            P[a] += P[b]; alive[b] = False; parent[b] = a
            U[a] = P[a] / np.linalg.norm(P[a])
            S[a] = np.where(alive, U @ U[a], -1); S[:, a] = S[a]; S[a, a] = -1
            S[b] = -1; S[:, b] = -1

        def root(x):
            while parent[x] != x:
                x = parent[x]
            return x
        remap = {multi[j]: multi[root(j)] for j in range(k)}
        labels = np.array([remap.get(l, l) for l in labels])
    return labels


def recurring(labels, face_t):
    """{cluster: importance} for people seen on >= RECUR_DAYS distinct days over >= RECUR_MONTHS distinct months."""
    days, months, years = collections.defaultdict(set), collections.defaultdict(set), collections.defaultdict(set)
    for l, t in zip(labels, face_t):
        if l < 0 or not math.isfinite(t):
            continue
        d = dt.datetime.fromtimestamp(float(t), dt.timezone.utc)
        days[l].add(d.date()); months[l].add((d.year, d.month)); years[l].add(d.year)
    imp = {l: math.log1p(len(days[l])) * (1 + 0.3 * (len(years[l]) - 1))
           for l in days if len(days[l]) >= RECUR_DAYS and len(months[l]) >= RECUR_MONTHS}
    return dict(sorted(imp.items(), key=lambda kv: -kv[1])[:TOP_PEOPLE])


def features(n_assets, face_asset, area, labels, important):
    """Per asset arrays (known, strangers, crowd) from faces mapped to asset indices (face_asset)."""
    known = np.zeros(n_assets, np.float32)
    strangers = np.zeros(n_assets, np.float32)
    unknown_n = np.zeros(n_assets, np.int32)
    top = max(important.values()) if important else 1.0
    for a, ar, l in zip(face_asset, area, labels):
        if a < 0:
            continue
        if l in important:
            known[a] += important[l] / top * min(1.0, ar / 0.02)
        else:
            unknown_n[a] += 1
            if ar >= STRANGER_AREA:
                strangers[a] += min(1.5, ar / 0.02)
    return known, strangers, (unknown_n >= CROWD).astype(np.float32)


# ---------------------------------------------------------------------------------------------- background pass
class FaceWorker:
    """Looks at thumbnails in priority order and keeps people features up to date. Status is aggregate counts only.

    Priorities (lower first): 0 the newest memory cards' top candidates, 1 a few people photos per month (enough to
    tell who recurs), 2 the other cards' and the towns' candidates, 3 the rest of the library -- but only photos
    CLIP thinks have people in them (for the People page). Faces are re-grouped in this thread only, after each
    priority level or at most once a minute, never while answering a request."""

    REGROUP_S = 60.0

    def __init__(self, engine, library, start_delay=2.0, interval=120.0, model_factory=None):
        self.engine, self.library = engine, library
        self.store = FaceStore(library)
        self.status = dict(state="waiting", done=0, total=0, faces=0, people=0)
        self.version = 0                                 # bumps when people features change
        self._by_id = {}                                 # asset_id -> (known, strangers, crowd), scanned assets only
        self._aligned = (None, None)
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._pending = {}                               # asset_id -> priority
        self._scanned = set()
        self._stop = threading.Event()
        self._model_factory = model_factory or (lambda: FaceModel(ensure_models()))
        self.start_delay, self.interval = start_delay, interval
        self.calls = 0                                   # face-model runs (thumbnails looked at), for tests and stats

    # -------------------------------------------------------------- queue
    def request(self, asset_ids, priority):
        """Ask for these assets to be looked at (ids already looked at are ignored)."""
        with self._wake:
            for a in asset_ids:
                a = str(a)
                if a not in self._scanned and self._pending.get(a, 9) > priority:
                    self._pending[a] = priority
            self._wake.notify()

    def busy(self, below=3):
        """True while assets of a priority under `below` are waiting (memory cards still being refined)."""
        with self._lock:
            return any(p < below for p in self._pending.values())

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="faces").start()
        return self

    def stop(self):
        self._stop.set()
        with self._wake:
            self._wake.notify()

    # -------------------------------------------------------------- loop
    def _loop(self):
        if self._stop.wait(self.start_delay):
            return
        self._scanned = self.store.scanned()
        if self._scanned:                                # features from an earlier run, before anything new
            self._safe(self._regroup)
        model = None
        background_for = None
        while not self._stop.is_set():
            with self._wake:
                if not self._pending:
                    cat = self.engine.cat
                    if background_for != id(cat):        # once per catalogue: the rest, people photos only
                        background_for = id(cat)
                        self._wake.release()
                        try:
                            self._queue_background()
                        finally:
                            self._wake.acquire()
                    if not self._pending:
                        if self.status["state"] not in ("error", "idle"):
                            self.status["state"] = "idle"
                        self._wake.wait(self.interval)
                        continue
            if model is None:
                self.status.update(state="downloading")
                try:
                    model = self._model_factory()
                except Exception as e:                   # network or model trouble: report, retry later
                    self.status.update(state="error", error=type(e).__name__)
                    self._stop.wait(1800)
                    continue
            self._safe(lambda: self._scan_some(model))

    def _safe(self, fn):
        try:
            fn()
        except Exception as e:
            self.status.update(state="error", error=type(e).__name__)

    def _queue_background(self):
        s1 = self.engine.stage1()
        cat = self.engine.cat
        ids = [str(cat.asset_ids[i]) for i in np.flatnonzero(s1["has_people"]) if cat.thumb[i]]
        self.request(ids, 3)

    def _scan_some(self, model, batch=40):
        with self._lock:
            if not self._pending:
                return
            level = min(self._pending.values())
            todo = [a for a, p in self._pending.items() if p == level][:batch]
            total = len(self._scanned) + len(self._pending)
        cat = self.engine.cat
        self.status.update(state="scanning", total=total)
        rows, scanned = [], []
        for k, aid in enumerate(todo):
            if self._stop.is_set():
                return
            i = cat.index.get(aid)
            found = []
            if i is not None and cat.thumb[i]:
                try:
                    found = model.faces(os.path.normpath(os.path.join(self.library, cat.thumb[i])))
                except Exception:
                    found = []
                self.calls += 1
            rows += [(aid, x, y, w, h, ar, sc, v.tobytes()) for x, y, w, h, ar, sc, v in found]
            scanned.append((aid, len(found)))
            if k % 10 == 9:
                time.sleep(0.01)                         # breathe: keep searches snappy while scanning
        self.store.add(rows, scanned)
        with self._lock:
            for a, _ in scanned:
                self._scanned.add(a)
                self._pending.pop(a, None)
            level_done = not any(p == level for p in self._pending.values())
            self.status.update(done=len(self._scanned))
        if level_done or time.monotonic() - getattr(self, "_last_regroup", 0) > self.REGROUP_S:
            self._regroup()

    def _regroup(self):
        self.status["state"] = "clustering"
        ids, area, score, vec = self.store.load()
        cat = self.engine.cat
        fa = np.array([cat.index.get(a, -1) for a in ids], int)
        order = np.argsort(-area) if len(area) else np.zeros(0, int)   # big, clear faces seed people first
        seed = (area >= SEED_AREA) & (score >= SEED_SCORE) & (np.abs(vec).sum(1) > 0 if len(ids) else np.zeros(0, bool))
        lab = np.full(len(ids), -1, int)
        if len(ids):
            lab[order] = cluster(vec[order], seed[order])
        face_t = np.array([cat.t[a] if a >= 0 else np.nan for a in fa])
        imp = recurring(lab, face_t)
        known, strangers, crowd = features(len(cat.asset_ids), fa, area, lab, imp)
        scanned = self._scanned or self.store.scanned()
        by_id = {}
        for aid in scanned:
            i = cat.index.get(aid)
            if i is not None:
                by_id[aid] = (float(known[i]), float(strangers[i]), float(crowd[i]))
        with self._lock:
            self._by_id = by_id
            self.version += 1
            self._last_regroup = time.monotonic()
        self.status.update(faces=len(ids), people=len(imp), state="scanning" if self._pending else "idle")

    # -------------------------------------------------------------- results
    def features(self):
        """(known, strangers, crowd) aligned with the current catalogue, NaN where not looked at yet.
        Built from results keyed by asset id: a catalogue reload costs a dictionary pass, never a re-grouping."""
        cat = self.engine.cat
        with self._lock:
            key = (id(cat), self.version)
            if self._aligned[0] == key:
                return self._aligned[1]
            by_id = self._by_id
        n = len(cat.asset_ids)
        arr = np.full((3, n), np.nan, np.float32)
        for aid, v in by_id.items():
            i = cat.index.get(aid)
            if i is not None:
                arr[:, i] = v
        out = (arr[0], arr[1], arr[2])
        with self._lock:
            self._aligned = (key, out)
        return out
