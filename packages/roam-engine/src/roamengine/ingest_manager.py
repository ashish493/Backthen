"""Starts, watches and stops one RoamIngest subprocess per library. Progress is aggregate counts only."""
import json
import os
import subprocess
import sys
import threading
import time


class IngestManager:
    def __init__(self, library, python=None):
        self.library = library
        self.python = python or sys.executable
        self.proc = None
        self.state = dict(running=False)
        self.lock = threading.Lock()

    def start(self, roots, storage="hdd", extra=None):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                raise RuntimeError("an ingest is already running")
            roots = [os.path.abspath(r) for r in roots]
            for r in roots:
                if not os.path.isdir(r):
                    raise FileNotFoundError(f"not a folder: {r}")
                if os.path.commonpath([os.path.abspath(self.library), r]) == r:
                    raise ValueError("the library folder must not be inside a source folder")
            if storage not in ("ssd", "hdd"):
                raise ValueError("storage must be 'ssd' or 'hdd'")
            cfg = dict(roots=roots, out_dir=self.library, storage=storage, **(extra or {}))
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            self.proc = subprocess.Popen([self.python, "-m", "roamengine.ingest_worker", json.dumps(cfg)],
                                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
                                         creationflags=flags)
            self.state = dict(running=True, started=time.time(), roots=len(roots), storage=storage, committed=0,
                              total=None, phase="starting")
            threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()
            return self.status()

    def _pump(self, proc):
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            kind = ev.get("event")
            with self.lock:
                s = self.state
                if kind == "discovered":
                    s.update(phase="ingesting", media=ev.get("media"), total=ev.get("to_process"),
                             skipped=ev.get("media", 0) - (ev.get("to_process") or 0))
                elif kind == "progress":
                    s.update(committed=ev.get("committed"), total=ev.get("total", s.get("total")))
                elif kind == "report":
                    s.update(phase="done", report={k: v for k, v in ev.items() if k not in ("event", "t")})
                elif kind == "failed":
                    s.update(phase="failed", error=ev.get("error"))
        rc = proc.wait()
        with self.lock:
            if self.proc is proc:
                self.state.update(running=False, rc=rc, finished=time.time())
                if self.state.get("phase") not in ("done", "failed"):
                    self.state["phase"] = "stopped" if self.state.get("cancelled") else "failed"

    def cancel(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                self.state["cancelled"] = True
                self.proc.terminate()                    # kill-safe: per-file commits, the next run resumes
        return self.status()

    def status(self):
        s = dict(self.state)
        if s.get("running") and s.get("total"):
            el = time.time() - s["started"]
            done = s.get("committed") or 0
            s["elapsed_s"] = round(el, 1)
            s["eta_s"] = round(el / done * (s["total"] - done), 0) if done else None
        return s
