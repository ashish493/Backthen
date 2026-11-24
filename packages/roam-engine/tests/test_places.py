"""Locations added by hand: stored in places.sqlite, overlaid in memory, never written to the manifest or media."""
import hashlib
import os

import pytest

from conftest import ATHENS, THESSALONIKI


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest() if os.path.exists(path) else None


def _by_name(engine, root):
    """file name -> asset_id"""
    c = engine.cat
    out = {}
    for aid in c.asset_ids.tolist():
        for f in c.files_of(aid):
            out[os.path.basename(f["rel_path"])] = aid
    return out


@pytest.fixture()
def fresh(library):
    from roamengine.engine import Engine
    e = Engine(library[1])
    yield e
    e.clear_place(e.cat.asset_ids.tolist())                 # leave the shared library as it was


def test_manual_place_overlays_without_touching_manifest_or_media(fresh, library):
    root, lib = library
    ids = _by_name(fresh, root)
    blue, red = ids["blue.jpg"], ids["red.jpg"]
    media = {n: _sha(os.path.join(root, d, n)) for d, n in [("a", "blue.jpg"), ("a", "red.jpg"), ("b", "blue_then_red.mp4")]}
    manifest = [_sha(os.path.join(lib, "manifest.sqlite" + s)) for s in ("", "-wal")]
    before = {a: fresh.asset(a) for a in (blue, red)}
    assert before[blue]["lat"] is None

    assert fresh.set_place([blue, red], 48.8584, 2.2945, "Test place") == 2
    b, r = fresh.asset(blue), fresh.asset(red)
    assert (b["lat"], b["lon"], b["gps_source"], b["place_name"]) == (48.8584, 2.2945, "manual", "Test place")
    assert b["original_lat"] is None                         # it had no location before
    assert (r["original_lat"], r["original_lon"]) == (round(ATHENS[0], 6), round(ATHENS[1], 6))
    for k in ("capture_time", "capture_time_utc", "capture_time_local", "capture_time_source"):
        assert b[k] == before[blue][k] and r[k] == before[red][k]   # dates are never changed

    from roamengine.engine import SearchFilters
    pts = fresh.points()
    assert blue in pts["asset_ids"]
    assert {x["asset_id"] for x in fresh.browse(SearchFilters(manual_place=True))["items"]} == {blue, red}
    assert fresh.browse(SearchFilters(near=(48.8584, 2.2945, 1)))["total"] == 2

    # persists across engine restarts; the manifest and the media files are byte-identical
    from roamengine.engine import Engine
    e2 = Engine(lib)
    assert e2.asset(blue)["gps_source"] == "manual"
    assert [_sha(os.path.join(lib, "manifest.sqlite" + s)) for s in ("", "-wal")] == manifest
    assert {n: _sha(os.path.join(root, d, n)) for d, n in [("a", "blue.jpg"), ("a", "red.jpg"), ("b", "blue_then_red.mp4")]} == media

    # removing the tag brings back exactly what the manifest says
    assert fresh.clear_place([blue, red]) == 2
    assert fresh.asset(blue)["lat"] is None
    assert fresh.asset(red)["lat"] == before[red]["lat"] and fresh.asset(red)["gps_source"] == before[red]["gps_source"]


def test_place_rejects_bad_coordinates(fresh, library):
    blue = _by_name(fresh, library[0])["blue.jpg"]
    for lat, lon in [(91, 0), (0, 181), (float("nan"), 0)]:
        with pytest.raises(ValueError):
            fresh.set_place([blue], lat, lon)
    assert fresh.asset(blue)["lat"] is None


def test_place_suggestions_come_from_items_near_in_time(fresh, library):
    ids = _by_name(fresh, library[0])
    red = ids["red.jpg"]
    assert fresh.place_suggestions(red) == []                 # nothing with a location within 6 h
    s = fresh.place_suggestions(red, window_h=24 * 365)
    assert s and (s[0]["lat"], s[0]["lon"]) == (round(THESSALONIKI[0], 6), round(THESSALONIKI[1], 6))
    assert fresh.place_suggestions(ids["blue.jpg"]) == []    # undated: no suggestion
    fresh.set_place([ids["blue.jpg"]], 1.0, 2.0, "Here")
    assert fresh.places.recent()[0] == dict(name="Here", lat=1.0, lon=2.0, count=1)


def test_offline_place_search():
    from roamengine.geocode import Gazetteer
    g = Gazetteer()
    top = lambda q: g.search(q, 3)[0]["label"]                                  # noqa: E731
    assert top("lisbon") == "Lisbon, Portugal"
    assert top("Galle").startswith("Galle, ")                              # own name before Halle (alias Galle)
    assert top("bombay").startswith("Mumbai")                              # former names
    assert top("munchen").startswith("Munich")                             # accents ignored
    assert g.search("paris, texas", 3)[0]["label"] == "Paris, Texas, United States"   # ", region" filter
    assert g.search("x") == [] and g.search("zzzqqq") == []
    assert all(r["source"] == "geonames" for r in g.search("paris"))
