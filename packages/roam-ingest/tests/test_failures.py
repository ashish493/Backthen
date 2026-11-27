"""A failing stage must stop the run quickly and report the failure; it must never hang or claim success
(docs/private/BOTTLENECKS.md #24, #25, #26). Faults are injected; the data is the synthetic set."""
import json
import os
import sqlite3
import threading

import pytest

from conftest import connect
from roamingest.config import IngestConfig
from roamingest.persistence import Manifest
from roamingest.pipeline import IngestError, Ingestor

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

HANG_S = 90                                               # far above a normal synthetic run; a hang trips this


class FlakyEmbedder:
    """Wraps the real embedder; raises on the given encode call(s), like a CUDA out-of-memory."""

    def __init__(self, inner, fail_on):
        self.inner, self.fail_on, self.calls = inner, set(fail_on), 0
        self.batch, self.info, self.device = inner.batch, inner.info, inner.device

    def encode(self, rows):
        self.calls += 1
        if self.calls in self.fail_on or (-1 in self.fail_on and len(rows) < self.batch):
            raise RuntimeError("CUDA out of memory (simulated)")
        return self.inner.encode(rows)


def run_bounded(cfg, embedder, cancel=None):
    """Run in a thread; fail the test (not hang it) if the run does not end in HANG_S. -> (report, exception)."""
    box = {}

    def go():
        try:
            box["rep"] = Ingestor(cfg, embedder=embedder, cancel=cancel).run()
        except BaseException as e:  # noqa: BLE001
            box["exc"] = e

    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(HANG_S)
    assert not t.is_alive(), f"ingest hung for more than {HANG_S} s"
    return box.get("rep"), box.get("exc")


def cfg_for(root, out, **kw):
    kw.setdefault("batch", 16)
    kw.setdefault("photo_workers", 4)
    return IngestConfig(roots=[root], out_dir=out, quiet=True, timeline=False, min_free_gb=1.0, **kw)


def test_unchanged_rerun_skips_the_model(dataset, embedder, tmp_path, monkeypatch):
    """Nothing to process: no torch, CUDA or model load (BOTTLENECKS.md #9); the report still names the model."""
    root, _ = dataset
    out = str(tmp_path / "noop")
    first = Ingestor(cfg_for(root, out), embedder=embedder).run()
    import roamingest.embedder as E
    monkeypatch.setattr(E, "ClipEmbedder", lambda *a, **k: pytest.fail("model loaded for an unchanged rerun"))
    rep = Ingestor(cfg_for(root, out)).run()
    assert rep["files_processed"] == 0 and rep["files_resumed_skipped"] == first["files_processed"] > 0
    assert not rep["failed"] and rep["config"]["model_id"] == first["config"]["model_id"]


def saved(out):
    con = connect(out)
    n = con.execute("SELECT COUNT(*) FROM asset_files").fetchone()[0]
    con.close()
    return n


@pytest.mark.parametrize("fail_on", [[1], [-1]], ids=["first_batch", "last_batch"])
def test_gpu_failure_stops_and_reports(dataset, embedder, tmp_path, fail_on):
    root, exp = dataset
    out = str(tmp_path / "gpu")
    rep, exc = run_bounded(cfg_for(root, out), FlakyEmbedder(embedder, fail_on))
    assert rep is None and isinstance(exc, IngestError)
    assert "embedding stage" in str(exc) and "out of memory" in str(exc)
    assert exc.report["failed"] and exc.report["failure"] == dict(stage="embedding", error="RuntimeError")
    on_disk = json.load(open(os.path.join(out, "report.json"), encoding="utf-8"))
    assert on_disk["failed"] and "message" not in on_disk["failure"]           # aggregate only, no free text
    m = Manifest(os.path.join(out, "manifest.sqlite"))                        # the database is consistent
    assert m.con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    m.close()
    # what was saved is kept and a rerun completes the library
    first = saved(out)
    rep2, exc2 = run_bounded(cfg_for(root, out), embedder)
    assert exc2 is None and not rep2["failed"] and rep2["unaccounted_files"] == 0
    assert rep2["files_resumed_skipped"] == first
    assert saved(out) == exp["photos"] + exp["videos"]


def test_database_write_failure_is_not_success(dataset, embedder, tmp_path, monkeypatch):
    root, _ = dataset
    out = str(tmp_path / "db")
    real, calls = Manifest.write_file, {"n": 0}

    def failing(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise sqlite3.OperationalError("disk I/O error (simulated)")
        return real(self, *a, **kw)

    monkeypatch.setattr(Manifest, "write_file", failing)
    rep, exc = run_bounded(cfg_for(root, out, commit_every=1), embedder)
    assert rep is None and isinstance(exc, IngestError)
    assert "database write stage" in str(exc) and "OperationalError" in str(exc)
    assert exc.report["failed"] and exc.report["unaccounted_files"] > 0
    monkeypatch.setattr(Manifest, "write_file", real)
    rep2, exc2 = run_bounded(cfg_for(root, out), embedder)                   # resumes after the failure
    assert exc2 is None and rep2["unaccounted_files"] == 0 and not rep2["failed"]


def test_unaccounted_files_fail_the_run(dataset, embedder, tmp_path, monkeypatch):
    """Files dispatched but never written, without any stage raising: still a failure, never 'done'."""
    root, _ = dataset
    real = Ingestor._photo_worker

    def lossy(self):                                      # drops every photo without a record
        while True:
            m = self._get(self.dq)
            if m is None:
                return
            self.budget.release(m[3])

    monkeypatch.setattr(Ingestor, "_photo_worker", lossy)
    rep, exc = run_bounded(cfg_for(root, str(tmp_path / "lost")), embedder)
    monkeypatch.setattr(Ingestor, "_photo_worker", real)
    assert rep is None and isinstance(exc, IngestError) and "never written" in str(exc)
    assert exc.report["failure"]["error"] == "Unaccounted"


@pytest.mark.parametrize("schedule", ["phased", "mixed"])
def test_cancel_does_not_hang(dataset, embedder, tmp_path, schedule):
    """#26: with the phased schedule the video dispatcher waited for every photo, which a cancel never delivers."""
    root, _ = dataset
    cancel = threading.Event()
    cancel.set()                                          # cancelled before any photo is dispatched
    rep, exc = run_bounded(cfg_for(root, str(tmp_path / schedule), schedule=schedule), embedder, cancel=cancel)
    assert exc is None and rep["cancelled"] and not rep["failed"]            # a cancel is not a failure
