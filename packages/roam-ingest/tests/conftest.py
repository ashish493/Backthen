import os
import sqlite3
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import synth  # noqa: E402

from roamingest.config import IngestConfig  # noqa: E402
from roamingest.pipeline import Ingestor, manifest_path  # noqa: E402


def cuda_ok():
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="session")
def dataset(tmp_path_factory):
    root = tmp_path_factory.mktemp("synthetic") / "lib"
    exp = synth.build(str(root))
    return str(root), exp


@pytest.fixture(scope="session")
def embedder():
    from roamingest.embedder import ClipEmbedder
    return ClipEmbedder(batch=16)


@pytest.fixture
def ingest(embedder, tmp_path):
    """ingest(roots, name, **cfg) -> (report, out_dir). Runs in-process with a shared model."""
    def run(roots, name="out", **kw):
        out = str(tmp_path / name)
        kw.setdefault("batch", 16)
        kw.setdefault("photo_workers", 4)
        cfg = IngestConfig(roots=list(roots) if isinstance(roots, (list, tuple)) else [roots], out_dir=out,
                           quiet=True, timeline=False, min_free_gb=1.0, **kw)
        return Ingestor(cfg, embedder=embedder).run(), out
    return run


def run_cli(*args):
    """roamingest CLI in a subprocess (needed for the hard-kill test). -> (returncode, stdout)."""
    p = subprocess.run([sys.executable, "-m", "roamingest.cli", *args], capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def load(out):
    """{rel_path: (status, n_emb, ((frame_index, timestamp, sampling_method, vector), ...))} via the v1 tables."""
    con = sqlite3.connect(manifest_path(out))
    rows = {}
    for rel, status, n, aid in con.execute("SELECT rel_path, status, n_emb, asset_id FROM asset_files"):
        fr = tuple(con.execute("SELECT f.frame_index, f.timestamp_s, f.sampling_method, e.vector FROM frames f "
                               "JOIN embeddings e USING(asset_id, frame_index) WHERE f.asset_id=? ORDER BY 1", (aid,)))
        rows[rel] = (status, n, fr)
    con.close()
    return rows


def connect(out):
    return sqlite3.connect(manifest_path(out))
