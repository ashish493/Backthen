"""Two-stage scoring: CLIPScores with fixed text first (cached prompts), the face model only on candidates."""
import datetime as dt
import json
from types import SimpleNamespace

import numpy as np

from roamengine import memories
from roamengine.faces import FaceWorker
from roamengine.text import PromptCache

from test_engine import req, server  # noqa: F401  (server is a pytest fixture)


def test_prompt_cache_round_trip(tmp_path):
    calls = []
    enc = lambda texts: (calls.extend(texts), np.eye(4, dtype=np.float32)[: len(texts)])[1]
    a = PromptCache(str(tmp_path), "open_clip/ViT-B-32/laion/fp16")
    v = a.get(["x", "y"], enc)
    assert v.shape == (2, 4) and calls == ["x", "y"]
    b = PromptCache(str(tmp_path), "open_clip/ViT-B-32/laion/fp16")      # a new launch: read from disk
    w = b.get(["y", "x", "z"], enc)
    assert calls == ["x", "y", "z"] and np.allclose(w[0], v[1]) and np.allclose(w[1], v[0])
    c = PromptCache(str(tmp_path), "open_clip/ViT-L-14/other/fp16")      # another model never reuses vectors
    c.get(["x"], enc)
    assert calls[-1] == "x" and len(calls) == 4


def test_stage1_scores(engine):
    s1 = engine.stage1()
    c = engine.cat
    n = len(c.asset_ids)
    assert s1["quality"].shape == (n,) and s1["people"].shape == (n,) and s1["has_people"].dtype == bool
    assert np.isfinite(s1["quality"][c.emb_idx]).all() and np.isfinite(s1["people"][c.emb_idx]).all()
    assert engine.stage1() is s1                                          # cached per catalogue
    cands = engine.face_candidates(np.arange(n), k=2)
    assert len(cands) <= 2 and all(s1["has_people"][i] for i in cands)


def test_place_best_pool_is_ranked_by_place_not_people(engine):
    c = engine.cat
    idx = engine._candidates(np.arange(len(c.asset_ids)))
    place = engine.stage1()["place"][idx]
    best = engine.place_best(idx, c.vectors[c.f_start[idx[0]]], k=1)
    assert best == idx[int(np.argmax(place))]                             # k=1: only the best place photo qualifies
    located = np.flatnonzero(np.isfinite(c.lat))
    covers = engine.town_covers({("Athens", "Greece"): located})
    assert covers[("Athens", "Greece")] in set(located.tolist())


def test_face_worker_queue_background_and_reload(engine, library, monkeypatch):
    seen = []
    model = SimpleNamespace(faces=lambda path: (seen.append(path), [])[1])
    w = FaceWorker(engine, library[1], model_factory=lambda: model)
    engine.faces = w
    try:
        c = engine.cat
        w._queue_background()                                             # the rest: people photos only
        people = {str(c.asset_ids[i]) for i in np.flatnonzero(engine.stage1()["has_people"])}
        assert set(w._pending) <= people
        # priority order: a priority-0 request is looked at before earlier priority-2 ones
        all_ids = [str(a) for a in c.asset_ids if a not in people]
        w.request(all_ids[1:], 2)
        w.request(all_ids[:1], 0)
        w._pending = {k: v for k, v in w._pending.items() if k in all_ids}
        monkeypatch.setattr(w, "_regroup", lambda: None)
        w._scan_some(model, batch=10)
        assert w.calls == 1 and all_ids[0] in w._scanned and all(a not in w._scanned for a in all_ids[1:])
        # features survive a catalogue reload without re-grouping faces
        w._by_id = {str(c.asset_ids[0]): (1.0, 0.0, 0.0)}
        w.version += 1
        monkeypatch.setattr(w, "_regroup", lambda: (_ for _ in ()).throw(AssertionError("re-grouped")))
        engine.maybe_reload(force=True)
        known = w.features()[0]
        assert known[engine.cat.index[str(c.asset_ids[0])]] == 1.0 and np.isnan(known).sum() == len(known) - 1
    finally:
        engine.faces = None


def fake_cat(times):
    t = np.array(times, float)
    return SimpleNamespace(t=t, asset_ids=np.array([f"{i:032x}" for i in range(len(t))], dtype=object))


def test_memory_cards_order():
    day = lambda s: dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp()
    times = ([day("2024-11-10")] * 5 + [day("2025-03-02")] * 9 + [day("2025-06-01")] * 4 + [day("2023-07-15")] * 6
             + [day("2025-08-20")] * 2)
    cat = fake_cat(times)
    trips = [dict(title="Lisbon", country="Portugal", start="2025-03-01", end="2025-03-04", days=4, count=9,
                  _members=np.arange(5, 14), _town=("Lisbon", "Portugal"))]
    cards = memories.cards(cat, trips, today=dt.date(2026, 11, 24))
    kinds = [c["kind"] for c in cards]
    assert kinds[0] == "trip" and kinds[1] == "ago" and cards[1]["month"] == "2024-11" and cards[1]["years_ago"] == 2
    months = [c["month"] for c in cards if c["kind"] == "month"]
    assert months == sorted(months, reverse=True) and "2025-03" not in months and "2025-08" not in months  # <3 items
    assert all(not k.startswith("_") for c in memories.public(cards) for k in c)


def test_api_memories(server):
    port, _ = server
    st, _, b = req(port, "/api/memories")
    d = json.loads(b)
    assert st == 200 and set(d) == {"cards", "version", "refining"} and d["refining"] is False
    assert all("cover" in c for c in d["cards"])
    st, _, b = req(port, "/api/points")
    p = json.loads(b)
    assert st == 200 and len(p["town"]) == len(p["asset_ids"]) and isinstance(p["towns"], list)
