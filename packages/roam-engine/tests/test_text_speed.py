"""Text model memory and first-home latency (BOTTLENECKS.md #6, #8)."""
import os
import shutil

import numpy as np

import roamengine.text as tx
from roamengine.engine import constant_prompts
from roamengine.server import Api

TEXTS = ["a photo of a beach", "my grandmother's birthday cake", "x", "snow on mountains at dusk", ""]


def test_text_tower_only_load_is_bit_identical(engine):
    """The text-tower-only load (#6) must give exactly the vectors of the full open_clip load."""
    fast = tx.TextEncoder(engine.cat.model_id, fast=True)
    assert fast.load_path == "text_tower"
    full = tx.TextEncoder(engine.cat.model_id, fast=False)
    assert full.load_path == "full"
    assert np.array_equal(fast.encode(TEXTS), full.encode(TEXTS))
    assert sum(p.numel() for p in fast.model.parameters()) == sum(p.numel() for p in full.model.parameters())


def test_shipped_prompts_cover_every_constant_prompt(engine):
    """The shipped vectors (#8) must cover every constant prompt and match the encoder; after editing a prompt,
    rerun tools/build_prompt_seed.py."""
    path = tx.seed_path(engine.cat.model_id)
    assert os.path.exists(path), "run tools/build_prompt_seed.py"
    seed = tx._read_npz(path)
    texts = constant_prompts()
    assert not set(texts) - set(seed), "prompts changed: rerun tools/build_prompt_seed.py"
    got = np.stack([seed[t] for t in texts])
    assert np.allclose(got, engine.text.encode(texts), atol=1e-5)


def _fresh(library, tmp_path, name):
    lib = str(tmp_path / name)
    shutil.copytree(library[1], lib, ignore=shutil.ignore_patterns("cache"))
    return lib


def test_first_home_does_not_wait_for_the_text_model(library, tmp_path, monkeypatch):
    """A new library (no prompt cache): the home screen is built from the shipped prompts and plain town covers
    without loading the text model, then refilled once it is loaded, ending exactly where a preloaded build does."""
    ref = Api(_fresh(library, tmp_path, "ref"))
    ref.engine.text
    want = ref.home()
    assert not want["towns_pending"] and want["town_vec"]

    lib = _fresh(library, tmp_path, "new")
    api = Api(lib)
    api.engine.warm_text = lambda: None                   # no background load: the test decides when it loads
    loads = []
    real = tx.TextEncoder.__init__
    monkeypatch.setattr(tx.TextEncoder, "__init__", lambda self, *a, **k: (loads.append(1), real(self, *a, **k))[1])
    h = api.home()
    assert loads == [] and h["towns_pending"] and h["town_vec"] == {}
    assert api.memories()["refining"] is True
    assert not os.path.exists(os.path.join(lib, "cache", "home.json"))   # provisional covers are never saved

    api.engine.text                                       # the model arrives
    h = api.home()
    assert loads == [1] and not h["towns_pending"] and api.memories()["refining"] is False
    assert os.path.exists(os.path.join(lib, "cache", "home.json"))
    strip = lambda cards: [(c["kind"], c["cover"]) for c in cards]       # noqa: E731
    assert strip(h["cards"]) == strip(want["cards"]) and h["journeys"] == want["journeys"] and h["towns"] == want["towns"]
