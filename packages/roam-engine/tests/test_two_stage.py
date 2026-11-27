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
        w._by_id = {str(c.asset_ids[0]): (1.0, 0.0, 0.0, 2.0)}
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


def test_memory_candidates_and_selection():
    day = lambda s: dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp()
    times = ([day("2024-11-10")] * 5 + [day("2025-03-02")] * 9 + [day("2025-03-20")] * 4 + [day("2023-07-15")] * 6
             + [day("2025-08-20")] * 2 + [day("2022-11-05")] * 4)
    cat = fake_cat(times)
    day_of = (cat.t // 86400).astype(np.int64)
    trips = [dict(title="Lisbon", country="Portugal", start="2025-03-01", end="2025-03-04", days=4, count=9,
                  _members=np.arange(5, 14), _town=("Lisbon", "Portugal"))]
    cands = memories.candidates(cat, trips, today=dt.date(2026, 11, 24))
    assert [c["kind"] for c in cands][:3] == ["trip", "ago", "ago"]
    cards = memories.select(cands, day_of, lambda m: True)
    keys = [c["key"] for c in cards]
    assert keys[:3] == ["t2025-03-01", "a2024-11", "a2022-11"]
    m03 = next(c for c in cards if c["key"] == "m2025-03")          # the month keeps only days the trip didn't use
    assert m03["count"] == 4 and set(day_of[m03["_members"]]) == {day_of[14]}
    days = [set(day_of[c["_members"]].tolist()) for c in cards]
    assert all(not (days[i] & days[j]) for i in range(len(days)) for j in range(i))   # no day on two cards
    assert "m2025-08" not in keys                                    # 2 photos: too few
    # a solo memory is dropped and the next candidate takes its place; unchecked ones stay for now
    solo = set(range(14, 18)) | set(range(24, 26))
    cards = memories.select(cands, day_of, lambda m: False if set(m.tolist()) <= solo else (None if 0 in m else True))
    keys2 = [c["key"] for c in cards]
    assert "m2025-03" not in keys2 and "a2024-11" in keys2 and not next(c for c in cards if c["key"] == "a2024-11")["_checked"]
    k = lambda cs: [c["key"] for c in cs]
    assert k(memories.select(cands, day_of, lambda m: True, max_cards=2)) == k(memories.select(cands, day_of, lambda m: True))[:2]
    assert all(not k.startswith("_") for c in memories.public(cards) for k in c)


def test_shared_and_covers_from_different_days(engine):
    c = engine.cat
    allidx = np.arange(len(c.asset_ids))
    assert engine.shared(allidx) in (True, False)                   # faces off: CLIP decides, never "unknown"
    picks = engine.pick_covers(allidx, 3)
    days = [int(c.t[i] // 86400) for i in picks if np.isfinite(c.t[i])]
    assert len(days) == len(set(days)) or len(set(int(c.t[i] // 86400) for i in allidx if np.isfinite(c.t[i]))) < len(picks)


def test_api_memories(server):
    import time
    port, _ = server
    deadline = time.monotonic() + 60                      # the first answer may be provisional while the text model
    while True:                                           # loads (BOTTLENECKS.md #8); the app polls the same way
        st, _, b = req(port, "/api/memories")
        d = json.loads(b)
        if not d.get("refining") or time.monotonic() > deadline:
            break
        time.sleep(0.5)
    assert st == 200 and {"cards", "version", "refining", "checked", "total", "provisional"} <= set(d) and d["refining"] is False
    assert all("cover" in c for c in d["cards"])
    st, _, b = req(port, "/api/points")
    p = json.loads(b)
    assert st == 200 and len(p["town"]) == len(p["asset_ids"]) and isinstance(p["towns"], list)


def test_group_photo_prefers_the_repeated_headcount(engine):
    c = engine.cat
    cands = engine._candidates(np.arange(len(c.asset_ids)), prefer_photos=False)
    assert len(cands) >= 3
    a, b, d = (int(x) for x in cands[:3])
    n = len(c.asset_ids)

    def stub(heads):
        g = np.full(n, np.nan, np.float32)
        for i, h in heads.items():
            g[i] = h
        z = np.where(np.isfinite(g), 0.0, np.nan).astype(np.float32)
        return SimpleNamespace(features=lambda: (z, z, z, g))
    try:
        engine.faces = stub({a: 4, b: 4, d: 3})                        # 4 people in two photos: the group is 4
        assert engine.group_photo(cands) in (a, b)
        engine.faces = stub({a: 7, b: 3, d: 3})                        # 7 only once (strangers?): the group is 3
        assert engine.group_photo(cands) in (b, d)
        engine.faces = stub({a: 5, b: 1, d: 1})                        # no headcount of 2+ repeats
        assert engine.group_photo(cands) is None
        engine.faces = stub({a: 4, b: 4, d: 3})
        assert engine.trip_covers(cands)[0] in (a, b) and engine.memory_covers(cands)[0] in (a, b)
    finally:
        engine.faces = None


def test_people_results_survive_a_restart(engine, library):
    c = engine.cat
    w = FaceWorker(engine, library[1], model_factory=lambda: None)
    w._scanned = w.store.scanned()
    w._by_id = {str(c.asset_ids[0]): (1.0, 0.0, 0.0, 3.0)}
    w._save_people(1)
    w2 = FaceWorker(engine, library[1], model_factory=lambda: None)
    assert w2._load_people() and w2.ready and w2._by_id[str(c.asset_ids[0])] == (1.0, 0.0, 0.0, 3.0)
    w2.store.add([(str(c.asset_ids[1]), 0, 0, 0.1, 0.1, 0.01, 0.9, np.zeros(128, np.float32).tobytes())],
                 [(str(c.asset_ids[1]), 1)])
    w3 = FaceWorker(engine, library[1], model_factory=lambda: None)
    assert not w3._load_people() and not w3.ready                       # faces changed since: re-group instead
