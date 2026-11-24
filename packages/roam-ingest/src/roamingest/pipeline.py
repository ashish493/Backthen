"""The ingestion pipeline (results/EXP_UNIFIED.md, EXP_HETERO.md, PHASE1_REPORT.md).

Threads and queues:
  dispatch -> readers (read each file once, bounded byte budget; 1 reader on hard drives) -> photo workers
           -> video workers (CPU pool, NVDEC pool; decoder chosen by a pure function of the file)
  photo/video workers -> GPU embedder (fixed-shape padded batches) -> manifest writer (one transaction per file group)
Determinism: sorted discovery, deterministic routing and batching, no work stealing between decoder pools.
Privacy: progress events and the report contain aggregate numbers only (no names or paths).
"""
import collections
import io
import json
import os
import queue
import shutil
import sys
import threading
import time

import numpy as np
import psutil

from . import __version__
from . import probing as P
from .config import IngestConfig
from .discovery import discover
from .identity import HEADTAIL, cid_from, cid_from_bytes, cid_from_file, full_hash_bytes
from .images import decode_photo, model_input_and_thumb, photo_strategy
from .persistence import Manifest
from .system import Monitor, evict, first_lcn, power_plan_name, set_power
from .videos import probe_codec, video_emits

MANIFEST_NAME = "manifest.sqlite"
THUMB_DIR = "thumbnails"


class ByteBudget:
    def __init__(self, limit):
        self.limit, self.used, self.cv, self.blocked_s = limit, 0, threading.Condition(), 0.0

    def acquire(self, n):
        n = min(n, self.limit)
        t = time.perf_counter()
        with self.cv:
            while self.used + n > self.limit:
                self.cv.wait()
            self.used += n
        self.blocked_s += time.perf_counter() - t
        return n

    def release(self, n):
        with self.cv:
            self.used -= n
            self.cv.notify_all()


def manifest_path(out_dir):
    """manifest.sqlite, or a legacy manifest.db (v0 benchmark outputs) if that is what the directory holds."""
    new, old = os.path.join(out_dir, MANIFEST_NAME), os.path.join(out_dir, "manifest.db")
    return old if (os.path.exists(old) and not os.path.exists(new)) else new


class Ingestor:
    """Run one ingest. Rerunning on the same out_dir resumes: unchanged files (size, mtime) are skipped."""

    def __init__(self, cfg: IngestConfig, on_progress=None, cancel=None, out=None, embedder=None):
        self.a = cfg
        self._embedder = embedder                         # reuse a loaded model (tests, long-lived apps)
        self.on_progress = on_progress
        self.cancel = cancel or threading.Event()
        self.out = out if out is not None else (open(os.devnull, "w") if cfg.quiet else sys.stdout)

    def _log(self, msg):
        print(msg, file=self.out, flush=True)

    def _event(self, kind, **kw):
        if self.on_progress:
            try:
                self.on_progress(dict(event=kind, **kw))
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ setup
    def run(self):
        a = self.a
        if a.no_throttle:
            a.throttle, a.priority = "off", "above"
        if a.video_prefetch_mb is None:
            a.video_prefetch_mb = 256 if a.storage == "hdd" else 0
        if a.read_budget_mb is None:
            a.read_budget_mb = 768 if a.video_prefetch_mb else 384
        os.makedirs(a.out_dir, exist_ok=True)
        self.m = Manifest(manifest_path(a.out_dir), roots=a.roots)
        con = self.m.con
        sids = [self.m.source_id(r, a.storage) for r in a.roots]

        items, counts = discover(a.roots, a.stride, a.limit, a.sidecars)
        for it in items:
            it["source_id"] = sids[it["root_index"]]
        self.items = items
        done = self.m.done_map(sids)
        todo = [it for it in items if done.get((it["source_id"], it["rel"])) != (it["size"], it["mtime_ns"])]
        n_resumed = len(items) - len(todo)

        self.run_id = self.m.start_run(a.public(), __version__)
        n_missing = n_moved = 0
        if a.limit is None and a.stride == 1:            # full scan: detect vanished and moved files
            present = collections.defaultdict(list)
            for it in items:
                present[it["source_id"]].append(it["rel"])
            con.execute("BEGIN")
            for s in sids:
                n_missing += self.m.mark_missing(s, present[s])
            cand = self.m.missing_by_size(sids) if n_missing else {}
            keep = []
            for it in todo:
                moved = None
                if it["size"] in cand and (it["source_id"], it["rel"]) not in done:
                    try:
                        cid = cid_from_file(it["path"], it["size"])
                    except OSError:
                        cid = None
                    moved = next((c for c in cand[it["size"]] if c[2] == cid), None)
                if moved:
                    self.m.relink_moved(moved, it, self.run_id)
                    cand[it["size"]].remove(moved)
                    n_moved += 1
                else:
                    keep.append(it)
            con.execute("COMMIT")
            todo = keep
            n_missing -= n_moved
        if a.schedule == "phased":
            todo.sort(key=lambda x: (x["kind"] != "photo",))
        for i, it in enumerate(todo):
            it["key"] = i
        # dedup: committed assets -> (source_id, rel_path) of the file whose decode wrote them (the owner so far).
        # A copy is linked only if its path is greater than that writer's, so the stored asset is always the one the
        # smallest path would produce (the duplicate-owner rule of write_file, ADR-008): output identical to no dedup.
        self.known, self.known_sizes = {}, set()
        if a.dedup == "skip":
            for aid, sid, rel, sz in con.execute(
                    "SELECT f.asset_id, f.source_id, f.rel_path, a.size FROM asset_files f JOIN assets a USING(asset_id) "
                    "LEFT JOIN file_stats st ON st.source_id = f.source_id AND st.rel_path = f.rel_path "
                    "WHERE f.status = 'done' AND f.n_emb > 0 AND a.n_frames > 0 "
                    "AND COALESCE(st.strategy, '') != 'dedup_link'"):
                k = (sid, rel)
                if aid not in self.known or k < self.known[aid]:
                    self.known[aid] = k
                self.known_sizes.add(sz)
        self.order_s = 0.0
        if a.read_order == "auto":
            a.read_order = "disk" if a.storage == "hdd" else "path"
        if a.read_order == "disk" and todo:
            t = time.perf_counter()
            for it in todo:
                it["lcn"] = first_lcn(it["path"])
            # owner first: byte-identical copies have the same size, so within each size group hand the earliest disk
            # positions to the smallest paths. The owner of a duplicate set is then read before its copies.
            groups = collections.defaultdict(list)
            for it in todo:
                groups[it["size"]].append(it)
            for g in groups.values():
                if len(g) > 1:
                    pos = sorted(x["lcn"] for x in g)
                    for x, l in zip(sorted(g, key=lambda x: (x["source_id"], x["rel"])), pos):
                        x["lcn"] = l
            todo.sort(key=lambda x: (x["kind"] != "photo", x["lcn"]) if a.schedule == "phased" else x["lcn"])
            for i, it in enumerate(todo):
                it["key"] = i
            self.order_s = time.perf_counter() - t
        self.todo = todo
        self.photos = [it for it in todo if it["kind"] == "photo"]
        self.videos = [it for it in todo if it["kind"] == "video"]
        n_ph = sum(1 for i in items if i["kind"] == "photo")
        self._log(f"discovered {len(items)} media files ({n_ph} photos, {len(items) - n_ph} videos), "
                  f"{counts['sidecars']} sidecar files, {sum(counts['other_ext'].values())} other files, "
                  f"{n_resumed} already done, {n_moved} moved, {n_missing} missing, {len(todo)} to process")
        self._event("discovered", media=len(items), photos=n_ph, videos=len(items) - n_ph, to_process=len(todo),
                    resumed=n_resumed, moved=n_moved, missing=n_missing)

        if a.cold:
            evict([it["path"] for it in todo])
        throttle_off, priority_set = set_power(a.throttle, a.priority)
        io0 = psutil.Process().io_counters()

        from .embedder import ClipEmbedder
        arch, pre = a.model.split("/", 1)
        reuse = self._embedder and self._embedder.batch == a.batch and (self._embedder.info["name"], self._embedder.info["pretrained"]) == (arch, pre)
        self.emb = self._embedder if reuse else ClipEmbedder(a.batch, a.device, arch, pre)
        self.m.ensure_model(self.emb.info)
        self.model_id = self.emb.info["model_id"]

        wall, mon = self._execute()
        rep = self._report(wall, mon, counts, n_resumed, n_moved, n_missing)
        rep["throttle_off_ok"], rep["priority_set"] = throttle_off, priority_set
        io1 = psutil.Process().io_counters()
        rep["process_read_bytes"] = io1.read_bytes - io0.read_bytes
        rep["process_read_ops"] = io1.read_count - io0.read_count
        if a.timeline:
            rep["timeline"] = mon.timeline
        self.m.finish_run(self.run_id, {k: v for k, v in rep.items() if k != "timeline"})
        with open(os.path.join(a.out_dir, "report.json"), "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)
        if a.report:
            os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
            with open(a.report, "w", encoding="utf-8") as f:
                json.dump(rep, f, indent=1)
        self._log(json.dumps(rep, indent=1))
        self._event("finished", wall_s=rep["wall_s"], files=rep["files_processed"], errors=sum(rep["errors"].values()))
        self.m.close()
        return rep

    # ------------------------------------------------------------------ execution
    def _execute(self):
        a = self.a
        self.readers_n = a.readers if a.readers else (1 if a.storage == "hdd" else 4)
        self.rq = queue.Queue(maxsize=max(4, 4 * self.readers_n))
        self.dq = queue.Queue()
        self.gq = queue.Queue(maxsize=a.batch * 6)
        self.wq = queue.Queue()
        self.vq_cpu = queue.Queue(maxsize=max(2, 2 * a.video_cpu))
        self.vq_nv = queue.Queue(maxsize=max(2, 2 * a.video_nvdec))
        self.budget = ByteBudget(a.read_budget_mb * 2**20)
        self.stop = threading.Event()
        self.photos_complete = threading.Event()
        if not self.photos:
            self.photos_complete.set()
        self.errors = collections.Counter()
        self.done_recs = []                               # (item, record, n_embeddings), appended by the writer
        self.written = dict(bytes=0, thumbs_ok=True)
        self.thumbs_written = set()
        self.gstat = dict(busy=0.0, wait=0.0, batches=0, rows=0)
        self.committed = dict(files=0)
        self.lat = collections.defaultdict(list)
        self.wlock = threading.Lock()
        self.prefetch_bytes = a.video_prefetch_mb * 2**20
        self.thumb_root = os.path.join(a.out_dir, THUMB_DIR)
        if a.video_cpu == 0 and self.videos:
            a.nvdec_pct = 100
        if a.video_nvdec == 0:
            a.nvdec_pct = 0

        threads = {
            "readers": [threading.Thread(target=self._reader) for _ in range(self.readers_n)],
            "photo": [threading.Thread(target=self._photo_worker) for _ in range(a.photo_workers)],
            "vcpu": [threading.Thread(target=self._video_worker, args=(self.vq_cpu, False)) for _ in range(a.video_cpu)],
            "vnv": [threading.Thread(target=self._video_worker, args=(self.vq_nv, True)) for _ in range(a.video_nvdec)],
        }
        t_gpu, t_wr = threading.Thread(target=self._gpu), threading.Thread(target=self._writer)
        t_guard = threading.Thread(target=self._disk_guard, daemon=True)
        d_ph, d_vd = threading.Thread(target=self._dispatch_photos), threading.Thread(target=self._dispatch_videos)

        def probe():
            return dict(q_read=self.rq.qsize(), q_photo=self.dq.qsize(), q_gpu=self.gq.qsize(),
                        q_vcpu=self.vq_cpu.qsize(), q_vnv=self.vq_nv.qsize(),
                        budget_mb=round(self.budget.used / 2**20), committed=self.committed["files"])

        with Monitor(probe=probe, keep_timeline=a.timeline) as mon:
            t0 = time.perf_counter()
            for grp in threads.values():
                for t in grp:
                    t.start()
            for t in (t_gpu, t_wr, t_guard, d_ph, d_vd):
                t.start()
            d_ph.join()                                   # shutdown in dependency order
            for _ in threads["readers"]:
                self.rq.put(None)
            for t in threads["readers"]:
                t.join()
            for _ in threads["photo"]:
                self.dq.put(None)
            for t in threads["photo"]:
                t.join()
            d_vd.join()
            for _ in threads["vcpu"]:
                self.vq_cpu.put(None)
            for _ in threads["vnv"]:
                self.vq_nv.put(None)
            for t in threads["vcpu"] + threads["vnv"]:
                t.join()
            self.gq.put(None)
            t_gpu.join()
            t_wr.join()
            wall = time.perf_counter() - t0
        self.stop.set()
        return wall, mon

    def _halted(self):
        return self.stop.is_set() or self.cancel.is_set()

    # ------------------------------------------------------------------ dispatch and routing
    def _route_value(self, it, data=None, cid=None):
        """0-99 key for the H.264 NVDEC/CPU split: from the path (default) or from the content ID (route_key=content),
        so byte-identical copies always get the same decoder and therefore identical embeddings."""
        if self.a.route_key == "content":
            if cid is None and data is not None:
                cid = cid_from_bytes(data)
            if cid is None:
                try:
                    cid = cid_from_file(it["path"], it["size"])
                except OSError:
                    cid = None
            if cid:
                return int(cid[:8], 16) % 100
        return it["route"]

    def _video_queue(self, it, data=None, cid=None):
        """Deterministic decoder choice: a pure function of the file (path or content hash; codec with route=codec)."""
        a = self.a
        if a.video_nvdec == 0:
            return self.vq_cpu
        if a.video_cpu == 0:                              # NVDEC workers decode unsupported codecs on the CPU
            return self.vq_nv
        if a.route == "codec":
            codec = probe_codec(io.BytesIO(data) if data is not None else it["path"])
            if codec == "hevc":
                return self.vq_nv
            if codec != "h264":
                return self.vq_cpu
        return self.vq_nv if self._route_value(it, data, cid) < a.nvdec_pct else self.vq_cpu

    def _dispatch_photos(self):
        a = self.a
        for it in (self.todo if self.prefetch_bytes and a.schedule == "mixed" else self.photos):
            if self._halted():
                break
            if it["kind"] == "photo" or it["size"] <= self.prefetch_bytes:
                self.rq.put(it)

    def _dispatch_videos(self):
        a = self.a
        if a.schedule == "phased":
            self.photos_complete.wait()
        for it in self.videos:
            if self._halted():
                break
            if self.prefetch_bytes and a.schedule == "mixed" and it["size"] <= self.prefetch_bytes:
                continue                                  # already sent through the reader
            cid = None
            if a.dedup == "skip" or a.route_key == "content":
                t = time.perf_counter()
                try:
                    cid = cid_from_file(it["path"], it["size"])   # 2 x 64 KiB
                except OSError:
                    cid = None
                if cid and a.dedup == "skip" and self._linkable(it, cid):
                    self._link(it, cid, (time.perf_counter() - t) * 1000)
                    continue
            self._video_queue(it, cid=cid).put((it, None, 0.0, 0, None))

    # ------------------------------------------------------------------ workers
    def _read_sidecar(self, it):
        if not (self.a.probe and it.get("sidecar_rel")):
            return None
        try:
            with open(os.path.join(self.a.roots[it["root_index"]], it["sidecar_rel"]), "rb") as f:
                return f.read(1 << 20)
        except OSError:
            return None

    def _reader(self):
        while True:
            it = self.rq.get()
            if it is None:
                return
            n = 0
            try:
                n = self.budget.acquire(it["size"])
                t = time.perf_counter()
                with open(it["path"], "rb") as f:
                    if self.a.dedup == "skip" and it["size"] in self.known_sizes:
                        head = f.read(HEADTAIL)           # content ID first: a known copy needs only 2 x 64 KiB
                        tail = b""
                        if it["size"] > HEADTAIL:
                            f.seek(it["size"] - HEADTAIL)
                            tail = f.read(HEADTAIL)
                        cid = cid_from(it["size"], head, tail)
                        if self._linkable(it, cid):
                            self.budget.release(n)
                            n = 0
                            self._link(it, cid, (time.perf_counter() - t) * 1000)
                            continue
                        f.seek(len(head))
                        data = head + f.read()
                    else:
                        data = f.read()
                side = self._read_sidecar(it)
                msg = (it, data, (time.perf_counter() - t) * 1000, n, side)
                if it["kind"] == "photo":
                    self.dq.put(msg)
                else:                                     # prefetched video: same single sequential reader
                    self._video_queue(it, data).put(msg)
            except Exception as e:  # noqa: BLE001
                if n:
                    self.budget.release(n)
                self.wq.put(("meta", it["key"], dict(status="error", error=type(e).__name__, n_emb=0), 0))

    def _linkable(self, it, cid):
        w = self.known.get(cid)
        return w is not None and (it["source_id"], it["rel"]) > w

    def _link(self, it, cid, t_read_ms):
        """A byte-identical copy of an asset already committed: record the file without reading or decoding it."""
        self.wq.put(("meta", it["key"], dict(status="done", cid=cid, link=True, decoder=None, strategy="dedup_link",
                                            t_read_ms=t_read_ms), 0))

    def _thumb_ref(self, asset_id):
        return f"{THUMB_DIR}/{asset_id[:2]}/{asset_id}.jpg"

    def _save_thumb(self, img, asset_id):
        if not self.written["thumbs_ok"]:
            return None
        ref = self._thumb_ref(asset_id)
        with self.wlock:                                  # duplicate content: write each thumbnail once per run
            if ref in self.thumbs_written:
                return ref
            self.thumbs_written.add(ref)
        p = os.path.join(self.a.out_dir, ref)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = f"{p}.{threading.get_ident()}.tmp"
        img.save(tmp, "JPEG", quality=85)
        os.replace(tmp, p)
        with self.wlock:
            self.written["bytes"] += os.path.getsize(p)
            if self.written["bytes"] > self.a.max_out_gb * 2**30:
                self.written["thumbs_ok"] = False
        return ref

    def _photo_worker(self):
        a = self.a
        while True:
            m = self.dq.get()
            if m is None:
                return
            it, data, t_read, held, side = m
            rec = dict(cid=None, t_read_ms=t_read, status="done", decoder="cpu", strategy=photo_strategy(it["ext"], a.photo_decode, a.raw_decode))
            try:
                rec["cid"] = cid_from_bytes(data)
                rec["full_hash"] = full_hash_bytes(data)
                t = time.perf_counter()
                im, orig, sc, rec["strategy"] = decode_photo(data, it["ext"], a.photo_decode, a.raw_decode)
                t2 = time.perf_counter()
                thumb, arr = model_input_and_thumb(im, orig, sc)
                if a.probe:
                    rec.update(P.merge(exif=P.exif_probe(data), sidecar=P.sidecar_probe(side) if side else None))
                t3 = time.perf_counter()
                rec.update(width=(orig or im.size)[0], height=(orig or im.size)[1],
                           t_decode_ms=(t2 - t) * 1000, t_xform_ms=(t3 - t2) * 1000)
                rec["thumbnail_ref"] = self._save_thumb(thumb, rec["cid"])
                rec["t_write_ms"] = (time.perf_counter() - t3) * 1000
                rec["n_emb"] = 1
                self.gq.put(("f", it["key"], 0, 0.0, arr))
            except Exception as e:  # noqa: BLE001
                rec.update(status="error", error=type(e).__name__, n_emb=0)
            finally:
                self.budget.release(held)
                del data
            self.wq.put(("meta", it["key"], rec, rec["n_emb"]))

    def _video_worker(self, q, use_nvdec):
        a = self.a
        while True:
            m = q.get()
            if m is None:
                return
            it, data, t_read, held, side = m             # data: whole file (prefetched) or None
            info = dict(status="done", decoder="cpu", strategy=None)
            t0 = time.perf_counter()
            try:
                if data is not None:
                    info["cid"], info["t_read_ms"] = cid_from_bytes(data), t_read
                    info["full_hash"] = full_hash_bytes(data)
                else:
                    info["cid"] = cid_from_file(it["path"], it["size"])
                    side = self._read_sidecar(it)
                    info["t_read_ms"] = (time.perf_counter() - t0) * 1000
                src = (lambda: io.BytesIO(data)) if data is not None else (lambda: it["path"])
                base = {k: info[k] for k in ("cid", "t_read_ms", "full_hash") if k in info}
                try:
                    emits = video_emits(src(), use_nvdec, a, info, want_thumb=a.video_thumbs)
                except Exception:  # noqa: BLE001
                    if not (use_nvdec and info.get("decoder") == "nvdec"):
                        raise
                    info = dict(status="done", decoder="cpu_fallback", strategy="seq", **base)
                    emits = video_emits(src(), False, a, info, want_thumb=a.video_thumbs)
                    info["decoder"] = "cpu_fallback"
                if not emits:
                    info.update(status="error", error="no_frames")
                info["sampling_policy"] = (f"adaptive:{a.adaptive_tau:g}" if info.get("strategy") == "adaptive"
                                           else info.get("strategy"))
                if a.probe:
                    info.update(P.merge(sidecar=P.sidecar_probe(side) if side else None,
                                        container=P.container_probe(info.get("meta"))))
                thumb = info.pop("thumb", None)
                tw = time.perf_counter()
                if thumb is not None and emits:
                    info["thumbnail_ref"] = self._save_thumb(thumb, info["cid"])
                info["t_write_ms"] = (time.perf_counter() - tw) * 1000
                for idx, pts, arr in emits:
                    self.gq.put(("f", it["key"], idx, pts, arr))
                info["n_emb"] = len(emits)
            except Exception as e:  # noqa: BLE001
                info.update(status="error", error=type(e).__name__, n_emb=0)
            finally:
                if held:
                    self.budget.release(held)
                data = None
            info.pop("meta", None)
            self.wq.put(("meta", it["key"], info, info["n_emb"]))

    def _gpu(self):
        a, buf = self.a, []

        def flush():
            if not buf:
                return
            t = time.perf_counter()
            e = self.emb.encode([b[3] for b in buf])
            self.gstat["busy"] += time.perf_counter() - t
            self.gstat["batches"] += 1
            self.gstat["rows"] += len(buf)
            for i, b in enumerate(buf):
                self.wq.put(("emb", b[0], b[1], b[2], e[i].tobytes()))
            buf.clear()

        while True:
            t = time.perf_counter()
            try:
                m = self.gq.get(timeout=0.3)
            except queue.Empty:
                self.gstat["wait"] += time.perf_counter() - t
                flush()
                continue
            self.gstat["wait"] += time.perf_counter() - t
            if m is None:
                flush()
                self.wq.put(("end",))
                return
            buf.append((m[1], m[2], m[3], m[4]))
            if len(buf) == a.batch:
                flush()

    def _writer(self):
        a, con = self.a, self.m.con
        state, by_key = {}, {it["key"]: it for it in self.todo}
        pending, last_commit, n_photo_done = 0, time.time(), 0
        orphans = []
        con.execute("BEGIN")

        def finish(key):
            nonlocal pending, n_photo_done
            st = state.pop(key)
            it, rec = by_key[key], st["rec"]
            if rec.get("link"):
                orphan = self.m.link_file(it, rec, self.run_id)
            else:
                orphan = self.m.write_file(it, rec, st["embs"], self.run_id, self.model_id)
                if a.dedup == "skip" and rec.get("status", "done") == "done" and st["embs"] and rec.get("cid"):
                    k, w = (it["source_id"], it["rel"]), self.known.get(rec["cid"])
                    self.known[rec["cid"]] = k if w is None or k < w else w
                    self.known_sizes.add(it["size"])
            for aid in self.m.dropped:                    # assets deleted as orphans can no longer be linked to
                self.known.pop(aid, None)
            self.m.dropped.clear()
            if orphan:
                orphans.append(orphan)
            pending += 1
            status = rec.get("status", "done")
            if status == "error":
                self.errors[(it["kind"], rec.get("error"))] += 1
            elif not rec.get("link"):                     # linked copies did no work: keep them out of latency
                self.lat[it["kind"]].append(sum((rec.get(k) or 0) for k in ("t_read_ms", "t_decode_ms", "t_xform_ms", "t_write_ms")))
            self.done_recs.append((it, rec, len(st["embs"])))
            if it["kind"] == "photo":
                n_photo_done += 1
                if n_photo_done == len(self.photos):
                    self.photos_complete.set()

        def commit(force=False):
            nonlocal pending, last_commit
            if pending and (force or pending >= a.commit_every or time.time() - last_commit > 2):
                con.execute("COMMIT")
                self.committed["files"] += pending
                pending, last_commit = 0, time.time()
                for ref in orphans:                       # thumbnails of assets no file references any more
                    try:
                        os.remove(os.path.join(a.out_dir, ref))
                    except OSError:
                        pass
                orphans.clear()
                self._event("progress", committed=self.committed["files"], total=len(self.todo))
                con.execute("BEGIN")
                if a.kill_after_files and self.committed["files"] >= a.kill_after_files:
                    self._log(f"[test] simulated crash after {self.committed['files']} committed files")
                    os._exit(137)

        while True:
            try:
                m = self.wq.get(timeout=1.0)
            except queue.Empty:
                commit()
                continue
            if m[0] == "end":
                break
            if m[0] == "emb":
                _, key, idx, pts, vec = m
                st = state.setdefault(key, dict(rec=None, expected=None, embs={}))
                st["embs"][idx] = (pts, vec)
            else:
                _, key, rec, expected = m
                st = state.setdefault(key, dict(rec=None, expected=None, embs={}))
                st["rec"], st["expected"] = rec, expected
            if st["rec"] is not None and len(st["embs"]) == st["expected"]:
                finish(key)
                commit()
        commit(force=True)
        con.execute("COMMIT")

    def _disk_guard(self):
        while not self.stop.wait(5):
            if self.cancel.is_set():
                self.stop.set()
            if shutil.disk_usage(self.a.out_dir).free < self.a.min_free_gb * 2**30:
                self.written["thumbs_ok"] = False
                self._log(f"[guard] output disk below {self.a.min_free_gb:g} GB free: stopping dispatch")
                self.stop.set()

    # ------------------------------------------------------------------ report (aggregate only)
    def _report(self, wall, mon, counts, n_resumed, n_moved, n_missing):
        a = self.a
        recs = [{**rec, "kind": it["kind"], "ext": it["ext"], "size": it["size"], "n_emb": n} for it, rec, n in self.done_recs]

        def pct(v, p):
            return round(float(np.percentile(v, p)), 1) if v else None

        kinds = {}
        for kind in ("photo", "video"):
            rk = [r for r in recs if r["kind"] == kind]
            ok = [r for r in rk if r.get("status") != "error"]
            kinds[kind] = dict(
                files=len(rk), bytes=sum(r["size"] for r in rk), ok=len(ok), errors=len(rk) - len(ok),
                truncated=sum(r.get("status") == "truncated" for r in rk),
                timeouts=sum(r.get("status") == "timeout" for r in rk),
                embeddings=sum(r["n_emb"] for r in rk),
                by_ext=dict(collections.Counter(r["ext"] for r in rk)),
                by_decoder=dict(collections.Counter(r.get("decoder") for r in ok)),
                by_strategy=dict(collections.Counter(r.get("strategy") for r in ok)),
                by_codec=dict(collections.Counter(r.get("codec") for r in ok if r.get("codec"))),
                capture_time_source=dict(collections.Counter(r.get("capture_time_source") or "none" for r in ok)),
                gps_source=dict(collections.Counter(r.get("gps_source") or "none" for r in ok)),
                stage_s={k: round(sum((r.get(k) or 0) for r in rk) / 1000, 1)
                         for k in ("t_read_ms", "t_decode_ms", "t_xform_ms", "t_write_ms")},
                latency_ms=dict(p50=pct(self.lat[kind], 50), p95=pct(self.lat[kind], 95), p99=pct(self.lat[kind], 99)))
        v = [r for r in recs if r["kind"] == "video"]
        kinds["video"].update(seeks_total=sum(r.get("seeks") or 0 for r in v),
                              probes_total=sum(r.get("probes") or 0 for r in v),
                              frames_decoded_total=sum(r.get("frames_decoded") or 0 for r in v),
                              rotated=sum(1 for r in v if r.get("rotation")))
        tot_bytes = sum(k["bytes"] for k in kinds.values())
        cfg = a.public()
        cfg.update(readers=self.readers_n, power_plan=power_plan_name(), model_id=self.model_id, device=self.emb.device)
        rep = dict(
            tag=a.tag, roamingest_version=__version__, run_id=self.run_id, wall_s=round(wall, 2),
            files_processed=len(recs), files_resumed_skipped=n_resumed, files_moved=n_moved, files_missing=n_missing,
            unaccounted_files=len(self.todo) - len(recs),  # dispatched but never written (must be 0 unless cancelled)
            files_dedup_linked=sum(1 for _, r, _ in self.done_recs if r.get("link")),
            bytes_dedup_skipped=sum(it["size"] for it, r, _ in self.done_recs if r.get("link")),
            read_order=a.read_order, read_order_s=round(self.order_s, 2),
            files_per_s=round(len(recs) / wall, 2) if wall else None,
            mb_per_s=round(tot_bytes / 1e6 / wall, 1) if wall else None,
            photos_per_s=round(kinds["photo"]["files"] / wall, 2) if wall else None,
            videos_per_s=round(kinds["video"]["files"] / wall, 3) if wall else None,
            sidecar_files=counts["sidecars"], other_files_skipped=dict(counts["other_ext"]),
            unreadable=counts["unreadable"], kinds=kinds,
            errors={f"{k}:{e}": n for (k, e), n in self.errors.items()}, config=cfg,
            gpu=dict(busy_s=round(self.gstat["busy"], 2), wait_s=round(self.gstat["wait"], 2),
                     batches=self.gstat["batches"], rows=self.gstat["rows"],
                     pad_waste_pct=round(100 * (1 - self.gstat["rows"] / max(1, self.gstat["batches"] * a.batch)), 1)),
            reader_blocked_on_byte_budget_s=round(self.budget.blocked_s, 2),
            thumbnails_bytes=self.written["bytes"], cancelled=self.cancel.is_set(), **mon.summary())
        rep["disk_mb_s"] = round(rep["disk_read_bytes"] / 1e6 / wall, 1) if wall else None
        return rep
