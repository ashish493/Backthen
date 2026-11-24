import http.client
import json
import os
import threading

import numpy as np
import pytest

from roamengine.engine import Engine, SearchFilters, haversine_km, parse_query
from roamengine.evaluation import Labels, evaluate, metrics_for

from conftest import ATHENS


def by_name(engine, root):
    """asset_id -> source file name (test data only)."""
    out = {}
    for aid in engine.cat.asset_ids:
        for f in engine.cat.files_of(aid):
            out.setdefault(aid, os.path.basename(f["path"]))
    return out


# ------------------------------------------------------------------ catalogue
def test_catalog_counts(engine):
    c = engine.cat
    assert len(c.asset_ids) == 5                 # red + copy share one asset
    assert int(c.is_video.sum()) == 1
    assert c.n_files == 6
    assert len(c.f_asset) == 4 + 7               # 4 photo frames + 7 video frames (2 s stride over 12 s)
    assert np.allclose(np.linalg.norm(c.vectors, axis=1), 1, atol=1e-3)
    s = c.stats()
    assert s["with_time"] == 3 and s["with_gps"] == 2 and s["assets"] == 5


def test_files_of_duplicate(engine, library):
    names = by_name(engine, library[0])
    red = next(a for a, n in names.items() if n == "red.jpg")
    files = engine.cat.files_of(red)
    assert sorted(os.path.basename(f["path"]) for f in files) == ["red.jpg", "red_copy.jpg"]
    assert engine.cat.primary_path(red).endswith("red.jpg")


# ------------------------------------------------------------------ search
@pytest.mark.parametrize("colour", ["red", "green", "blue", "yellow"])
def test_text_search_finds_colour(engine, library, colour):
    names = by_name(engine, library[0])
    res = engine.search(f"a plain solid {colour} colour", k=5)
    top = [names[it["asset_id"]] for it in res["items"]]
    if colour in ("red", "blue"):                # the video is half blue, half red: either may rank first
        assert set(top[:2]) == {f"{colour}.jpg" if colour != "yellow" else "yellow.png", "blue_then_red.mp4"}
    else:
        assert top[0] == (f"{colour}.png" if colour == "yellow" else f"{colour}.jpg")
    assert res["total"] == 5 and res["timing_ms"]["rank"] < 50


def test_video_moments_point_to_matching_half(engine, library):
    names = by_name(engine, library[0])
    for colour, half in (("red", lambda t: t >= 6), ("blue", lambda t: t < 6)):
        res = engine.search(f"a plain solid {colour} colour", SearchFilters(media_type="video"), k=1)
        it = res["items"][0]
        assert names[it["asset_id"]] == "blue_then_red.mp4"
        assert half(it["moments"][0]["timestamp_s"]), (colour, it["moments"])
        assert len(it["moments"]) == 3


def test_filters(engine, library):
    names = by_name(engine, library[0])
    q = "a colour"

    def got(f):
        return sorted(names[i["asset_id"]] for i in engine.search(q, f, k=50)["items"])
    assert got(SearchFilters(media_type="photo")) == ["blue.jpg", "green.jpg", "red.jpg", "yellow.png"]
    assert got(SearchFilters(date_from="2023", date_to="2023")) == ["green.jpg", "red.jpg"]
    assert got(SearchFilters(date_to="2023-05-06")) == ["red.jpg"]          # bare date: whole day inclusive
    assert got(SearchFilters(date_from="2023-06")) == ["blue_then_red.mp4", "green.jpg"]
    assert got(SearchFilters(has_gps=True)) == ["green.jpg", "red.jpg"]
    assert got(SearchFilters(has_time=False)) == ["blue.jpg", "yellow.png"]
    assert got(SearchFilters(near=(ATHENS[0], ATHENS[1], 10))) == ["red.jpg"]
    assert got(SearchFilters(bbox=(40.5, 22.5, 41.0, 23.5))) == ["green.jpg"]


def test_inline_query_syntax():
    text, f = parse_query("sunset on a beach type:video after:2019-05 before:2020 has:gps")
    assert text == "sunset on a beach"
    assert (f.media_type, f.date_from, f.date_to, f.has_gps) == ("video", "2019-05", "2020", True)
    text, f = parse_query("year:2021 temple")
    assert text == "temple" and f.date_from == f.date_to == "2021"


def test_search_is_deterministic_and_paginates(engine):
    a = engine.search("a colour", k=5)["items"]
    b = engine.search("a colour", k=2, offset=0)["items"] + engine.search("a colour", k=3, offset=2)["items"]
    assert [x["asset_id"] for x in a] == [x["asset_id"] for x in b]
    assert [x["score"] for x in a] == sorted([x["score"] for x in a], reverse=True)


def test_similar_excludes_self(engine, library):
    names = by_name(engine, library[0])
    red = next(a for a, n in names.items() if n == "red.jpg")
    res = engine.similar(red, k=10)
    ids = [i["asset_id"] for i in res["items"]]
    assert red not in ids and len(ids) == 4
    assert names[ids[0]] == "blue_then_red.mp4"          # the only other asset with red content


def test_browse_and_timeline(engine, library):
    names = by_name(engine, library[0])
    order = [names[i["asset_id"]] for i in engine.browse(k=10)["items"]]
    assert order[:3] == ["blue_then_red.mp4", "green.jpg", "red.jpg"]       # newest first, undated last
    assert set(order[3:]) == {"blue.jpg", "yellow.png"}
    tl = engine.timeline()
    assert tl["months"] == [dict(month="2023-05", count=1), dict(month="2023-11", count=1),
                            dict(month="2024-01", count=1)] and tl["undated"] == 2
    pts = engine.points()
    assert pts["total"] == 2 and len(pts["lat"]) == 2


def test_filters_only_query_browses(engine):
    r = engine.search("type:photo")
    assert r["text"] == "" and r["total"] == 4


def test_haversine():
    assert abs(float(haversine_km(np.array([0.0]), np.array([0.0]), 0.0, 1.0)[0]) - 111.195) < 0.01
    assert np.isinf(haversine_km(np.array([np.nan]), np.array([np.nan]), 0.0, 0.0)[0])


def test_empty_library(tmp_path):
    e = Engine(str(tmp_path))
    assert e.cat.stats()["empty"] is True
    assert e.search("anything")["items"] == [] and e.browse()["total"] == 0
    assert e.timeline()["months"] == [] and e.points()["total"] == 0


def test_reload_after_new_ingest(library, tmp_path):
    """A second ingest into a copy of the library is picked up by maybe_reload without restarting."""
    import shutil
    from conftest import jpeg, COLOURS
    from roamingest.config import IngestConfig
    from roamingest.pipeline import Ingestor
    src, lib = library
    src2, lib2 = str(tmp_path / "src"), str(tmp_path / "lib")
    shutil.copytree(src, src2)
    shutil.copytree(lib, lib2)
    con = __import__("sqlite3").connect(os.path.join(lib2, "manifest.sqlite"))
    con.execute("UPDATE sources SET root_path=?, root_prefix=?", (src2, src2 + os.sep))
    con.commit()
    con.close()
    e = Engine(lib2)
    assert len(e.cat.asset_ids) == 5
    jpeg(os.path.join(src2, "a", "purple.jpg"), (130, 30, 160))
    Ingestor(IngestConfig(roots=[src2], out_dir=lib2, storage="ssd", quiet=True, min_free_gb=0,
                          timeline=False)).run()
    assert e.maybe_reload(force=False) in (True, False)    # rate-limited check
    assert e.maybe_reload(force=True)
    assert len(e.cat.asset_ids) == 6


# ------------------------------------------------------------------ evaluation
def test_metrics_known_values():
    m = metrics_for(["a", "x", "b", "y"], {"a": 2, "b": 1, "c": 1, "x": 0}, ks=(1, 2, 4))
    assert m["recall@1"] == pytest.approx(1 / 3) and m["recall@4"] == pytest.approx(2 / 3)
    assert m["precision@2"] == 0.5 and m["rr"] == 1.0
    dcg = 3 / 1 + 1 / np.log2(4)
    idcg = 3 / 1 + 1 / np.log2(3) + 1 / np.log2(4)
    assert m["ndcg@4"] == pytest.approx(dcg / idcg)
    assert metrics_for(["x"], {"a": 1})["rr"] == 0.0


def test_labels_and_evaluate(engine, library, tmp_path):
    names = by_name(engine, library[0])
    import shutil
    lib = str(tmp_path / "lab")
    os.makedirs(lib)
    lab = Labels(lib)
    q = lab.add_query("a plain solid green colour")
    assert lab.add_query("a plain solid green colour") == q       # idempotent
    green = next(a for a, n in names.items() if n == "green.jpg")
    blue = next(a for a, n in names.items() if n == "blue.jpg")
    lab.judge(q, green, 2)
    lab.judge(q, blue, 0)
    assert lab.queries()[0]["relevant"] == 1 and lab.queries()[0]["judged"] == 2
    rep = evaluate(engine, lab)
    assert rep["queries"] == 1 and rep["mean"]["recall@1"] == 1.0 and rep["mean"]["mrr"] == 1.0
    assert rep["random_recall"]["@1"] == pytest.approx(1 / 5, abs=1e-6)
    lab.judge(q, blue, None)
    assert lab.judgments(q) == {green: 2}
    lab.delete_query(q)
    assert lab.queries() == []
    shutil.rmtree(lib, ignore_errors=True)


# ------------------------------------------------------------------ HTTP API
@pytest.fixture(scope="module")
def server(library):
    from roamengine import server as srv
    lines = []
    httpd = srv.serve(library[1], token="test-token", ready=lines.append)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield httpd.server_address[1], json.loads(lines[0])
    httpd.shutdown()


def req(port, path, token="test-token", host=None, method="GET", body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    h = {"Host": host or f"127.0.0.1:{port}", **(headers or {})}
    if token:
        h["X-Roam-Token"] = token
    data = json.dumps(body).encode() if body is not None else None
    if data:
        h["Content-Type"] = "application/json"
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read()


def test_api_auth(server):
    port, ready = server
    assert ready["event"] == "ready" and ready["port"] == port
    assert req(port, "/api/health", token=None)[0] == 403
    assert req(port, "/api/health", token="wrong")[0] == 403
    assert req(port, "/api/health", host="evil.example:80")[0] == 403          # DNS-rebinding guard
    assert req(port, "/api/health?token=test-token", token=None)[0] == 200
    st, h, b = req(port, "/api/health", headers={"Origin": "http://tauri.localhost"})
    assert st == 200 and h.get("Access-Control-Allow-Origin") == "http://tauri.localhost"
    st, h, _ = req(port, "/api/health", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in h


def test_api_search_and_media(server, engine, library):
    port, _ = server
    names = by_name(engine, library[0])
    st, _, b = req(port, "/api/search?q=a%20plain%20solid%20green%20colour&k=3")
    res = json.loads(b)
    assert st == 200 and names[res["items"][0]["asset_id"]] == "green.jpg"
    aid = res["items"][0]["asset_id"]
    st, _, b = req(port, f"/api/asset/{aid}")
    a = json.loads(b)
    assert st == 200 and a["capture_time_source"] == "sidecar" and len(a["files"]) == 1
    for kind in ("thumb", "preview"):
        st, h, b = req(port, f"/{kind}/{aid}")
        assert st == 200 and h["Content-Type"] == "image/jpeg" and b[:2] == b"\xff\xd8"
    vid = next(i for i, n in names.items() if n.endswith(".mp4"))
    st, h, b = req(port, f"/frame/{vid}?t=9")
    assert st == 200 and b[:2] == b"\xff\xd8"
    from PIL import Image
    import io
    px = np.asarray(Image.open(io.BytesIO(b)).convert("RGB")).reshape(-1, 3).mean(0)
    assert px[0] > 150 and px[2] < 100                                           # t=9 s is in the red half
    st, h, b = req(port, f"/media/{vid}", headers={"Range": "bytes=0-99"})
    assert st == 206 and len(b) == 100 and h["Content-Range"].startswith("bytes 0-99/")
    st, h, b = req(port, f"/media/{vid}")
    assert st == 200 and int(h["Content-Length"]) == len(b) > 1000
    assert req(port, "/api/asset/ffffffffffffffff")[0] == 404
    assert req(port, "/thumb/../../etc")[0] in (403, 404)


def test_api_glimpses_and_fresh_thumbs(server, engine, library):
    port, _ = server
    st, _, b = req(port, "/api/ingest/glimpses?n=4")
    items = json.loads(b)["items"]
    assert st == 200 and 0 < len(items) <= 4 and all(i["media_type"] in ("photo", "video") for i in items)
    assert all(req(port, f"/thumb/{i['asset_id']}")[0] == 200 for i in items)
    # an item a running ingest has just written is not in the loaded catalogue yet: its thumbnail comes from the manifest
    from roamengine import server as srv
    api = srv.Api.__new__(srv.Api)
    api.library, api.engine = os.path.abspath(library[1]), engine
    aid = items[0]["asset_id"]
    i = engine.cat.index.pop(aid)
    try:
        path = api.thumb_path(aid)
        assert path and os.path.exists(path)
        with pytest.raises(KeyError):
            api.thumb_path("f" * 32)
    finally:
        engine.cat.index[aid] = i


def test_api_labels_roundtrip(server, engine):
    port, _ = server
    st, _, b = req(port, "/api/labels/queries", method="POST", body={"text": "a plain solid blue colour"})
    qid = json.loads(b)["query_id"]
    aid = engine.search("a plain solid blue colour", SearchFilters(media_type="photo"), k=1)["items"][0]["asset_id"]
    assert req(port, "/api/labels/judge", method="POST", body={"query_id": qid, "asset_id": aid, "grade": 1})[0] == 200
    ev = json.loads(req(port, "/api/eval")[2])
    assert ev["queries"] == 1 and ev["mean"]["recall@5"] == 1.0
    assert req(port, f"/api/labels/queries/{qid}", method="DELETE")[0] == 200


def test_api_browse_timeline_points(server):
    port, _ = server
    assert json.loads(req(port, "/api/browse?type=video")[2])["total"] == 1
    assert len(json.loads(req(port, "/api/timeline")[2])["months"]) == 3
    assert json.loads(req(port, "/api/points?gps=1")[2])["total"] == 2
    lib = json.loads(req(port, "/api/library")[2])
    assert lib["assets"] == 5 and lib["photos"] == 4


def test_api_ingest_subprocess(tmp_path):
    """The app's ingest path: POST /api/ingest on an empty library runs RoamIngest in a subprocess."""
    import time
    from conftest import jpeg, COLOURS
    from roamengine import server as srv
    src = tmp_path / "src"
    src.mkdir()
    jpeg(str(src / "red.jpg"), COLOURS["red"])
    lib = str(tmp_path / "newlib")
    os.makedirs(lib)
    lines = []
    httpd = srv.serve(lib, token="t", ready=lines.append)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        assert json.loads(req(port, "/api/library", token="t")[2])["empty"] is True
        st, _, b = req(port, "/api/ingest", token="t", method="POST", body={"roots": [str(src)], "storage": "ssd"})
        assert st == 200, b
        assert req(port, "/api/ingest", token="t", method="POST", body={"roots": [str(src)]})[0] == 400  # one at a time
        deadline = time.time() + 300
        while time.time() < deadline:
            s = json.loads(req(port, "/api/ingest", token="t")[2])
            if not s["running"]:
                break
            time.sleep(1)
        assert s["phase"] == "done" and s["report"]["files_processed"] == 1, s
        lib_info = json.loads(req(port, "/api/reload", token="t", method="POST", body={})[2])
        assert lib_info["assets"] == 1
    finally:
        httpd.shutdown()


def test_suggestions(engine):
    from roamengine.engine import SUGGESTION_POOL
    r = engine.suggestions(n=8)
    texts = [i["text"] for i in r["items"]]
    assert len(texts) == 8 and set(texts) <= set(SUGGESTION_POOL)
    st = [i["strength"] for i in r["items"]]
    assert st == sorted(st, reverse=True)
    assert engine.suggestions(n=8) is r                    # cached until the catalogue changes
