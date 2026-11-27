"""Fast unit tests (no model)."""
import io
import json
import os

import av
import numpy as np
import pytest
from PIL import Image

import synth
from roamingest import identity, probing as P, transforms as T
from roamingest.discovery import classify, discover, match_sidecar
from roamingest.images import decode_photo
from roamingest.videos import sample_targets, stream_rotation

HERE = os.path.dirname(__file__)
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))


def test_schema_copy_matches_contract():
    pkg = open(os.path.join(HERE, "..", "src", "roamingest", "manifest_v1.sql"), encoding="utf-8").read()
    shared = open(os.path.join(REPO, "shared", "schemas", "manifest_v1.sql"), encoding="utf-8").read()
    assert pkg == shared


def test_classify():
    assert classify(".jpg") == "photo" and classify(".nef") == "photo" and classify(".gif") == "photo"
    assert classify(".3gp") == "video" and classify(".mov") == "video"
    assert classify(".json") == "sidecar" and classify(".txt") is None


@pytest.mark.parametrize("media,jsons,expected", [
    ("a.jpg", ["a.jpg.json"], "a.jpg.json"),
    ("a.jpg", ["a.jpg.supplemental-metadata.json"], "a.jpg.supplemental-metadata.json"),
    ("a.jpg", ["a.jpg.suppl.json", "a.jpg.json"], "a.jpg.json"),
    ("a(1).jpg", ["a.jpg(1).json", "a.jpg.json"], "a.jpg(1).json"),
    ("a(2).jpg", ["a.jpg.supplemental-metadata(2).json"], "a.jpg.supplemental-metadata(2).json"),
    ("a-edited.jpg", ["a.jpg.json"], "a.jpg.json"),
    ("Screenshot_20190101-101010_Long_Application_Name.jpg", ["screenshot_20190101-101010_long_applicati.json"],
     "screenshot_20190101-101010_long_applicati.json"),
    ("b.jpg", ["a.jpg.json"], None),
    ("a.jpg", ["metadata.json"], None),
])
def test_sidecar_rules(media, jsons, expected):
    assert match_sidecar(media, {j.lower(): j for j in jsons}) == expected


def _match_sidecar_scan(name, jsons):
    """The original O(media x sidecars) matcher, kept as the reference for the indexed one."""
    import re
    dup = re.compile(r"^(.*)\((\d+)\)$")
    ml = name.lower()
    if ml + ".json" in jsons:
        return jsons[ml + ".json"]
    pref = [j for j in jsons if j.startswith(ml + ".")]
    if pref:
        return jsons[min(pref, key=len)]
    stem, ext = os.path.splitext(ml)
    m = dup.match(stem)
    if m:
        base, n = m.group(1) + ext, m.group(2)
        if f"{base}({n}).json" in jsons:
            return jsons[f"{base}({n}).json"]
        cand = [j for j in jsons if j.startswith(base + ".") and j.endswith(f"({n}).json")]
        if cand:
            return jsons[min(cand, key=len)]
    if stem.endswith("-edited"):
        return _match_sidecar_scan(stem[:-7] + ext, jsons)
    trunc = [j for j in jsons if len(j) >= 25 and ml.startswith(j[:-5]) and not dup.match(j[:-5])]
    if trunc:
        return jsons[max(trunc, key=len)]
    return None


def test_sidecar_index_matches_the_scan():
    """Random Takeout-like directories, including equal-length ties: the indexed matcher picks what the scan picked."""
    import random
    from roamingest.discovery import sidecar_index
    rng = random.Random(0)
    stems = ["IMG_0001", "IMG_0002", "a", "PXL_20230101_101010123", "Screenshot_20190101-101010_Long_Application"]
    tails = ["", ".supplemental-metadata", ".supplemental-metad", ".suppl", ".xx", ".yy"]
    checked = 0
    for _ in range(400):
        media, jsons = [], {}
        for _ in range(rng.randint(1, 12)):
            s, e = rng.choice(stems), rng.choice([".jpg", ".heic", ".mp4"])
            n = rng.choice(["", "", "(1)", "(2)"])
            media.append(rng.choice([f"{s}{n}{e}", f"{s}-edited{e}", f"{s}{e}"]))
            j = rng.choice([f"{s}{e}{rng.choice(tails)}{n}.json", f"{s}{e}{n}.json", f"{(s + e)[:rng.randint(18, 40)]}.json"])
            jsons[j.lower()] = j
        idx = sidecar_index(jsons)
        for mf in media:
            assert match_sidecar(mf, jsons, idx) == _match_sidecar_scan(mf, jsons), (mf, list(jsons))
            checked += 1
    assert checked > 1000


def test_exif_probe_roundtrip(tmp_path):
    rng = np.random.default_rng(0)
    p = tmp_path / "x.jpg"
    synth.jpeg(str(p), rng, gps=True, orientation=6)
    e = P.exif_probe(p.read_bytes())
    assert e["capture_time_local"] == "2023-05-06T07:08:09" and e["capture_time_utc"] == synth.EXIF_UTC
    assert e["tz_offset_min"] == 330 and e["orientation"] == 6
    assert abs(e["lat"] - synth.GPS[0]) < 1e-4 and abs(e["lon"] - synth.GPS[1]) < 1e-4 and e["alt"] == 12.5


def test_exif_probe_bad_input():
    assert P.exif_probe(b"not an image") == {}
    assert P._parse_exif_dt("0000:00:00 00:00:00") is None


def test_sidecar_probe():
    raw = json.dumps({"photoTakenTime": {"timestamp": str(synth.SIDECAR_TS)}, "creationTime": {"timestamp": "1"},
                      "geoData": {"latitude": 0.0, "longitude": 0.0},
                      "geoDataExif": {"latitude": 1.5, "longitude": 2.5, "altitude": 3.0}})
    s = P.sidecar_probe(raw)
    assert s["capture_time_utc"] == synth.SIDECAR_UTC and s["gps_field"] == "geoDataExif" and s["lat"] == 1.5
    assert P.sidecar_probe("{not json") == {} and P.sidecar_probe("[]") == {}
    assert "capture_time_utc" not in P.sidecar_probe(json.dumps({"creationTime": {"timestamp": "1700000000"}}))


def test_container_probe():
    c = P.container_probe({"creation_time": synth.VIDEO_CT, "location": synth.VIDEO_LOC})
    assert c == {"capture_time_utc": synth.VIDEO_UTC, "lat": 27.1751, "lon": 78.0421, "alt": 171.0}
    assert P.container_probe({"creation_time": "1904-01-01T00:00:00Z", "location": "+00.0000+000.0000/"}) == {}


def test_merge_precedence():
    exif = {"capture_time_local": "2020-01-01T10:00:00"}
    side = {"capture_time_utc": "2020-01-01T04:30:00Z", "lat": 1.0, "lon": 2.0, "gps_field": "geoData"}
    m = P.merge(exif=exif, sidecar=side)
    assert m["capture_time_source"] == "sidecar" and m["capture_time_local"] == "2020-01-01T10:00:00"
    assert m["gps_source"] == "sidecar_geoData"
    m = P.merge(exif={"capture_time_utc": "2020-01-01T00:00:00Z", "capture_time_local": "x", "lat": 5.0, "lon": 6.0},
                sidecar=side)
    assert m["capture_time_source"] == "exif" and m["gps_source"] == "exif" and m["lat"] == 5.0
    assert P.merge(exif=exif)["capture_time_source"] == "exif_local"
    assert P.merge()["capture_time_source"] is None


def test_content_ids(tmp_path):
    data = os.urandom(300_000)
    p = tmp_path / "f.bin"
    p.write_bytes(data)
    assert identity.cid_from_bytes(data) == identity.cid_from_file(str(p), len(data))
    assert identity.full_hash_bytes(data) == identity.full_hash_file(str(p))
    mid = bytearray(data)
    mid[150_000] ^= 1                                   # partial ID misses a mid-file edit; the full hash does not
    assert identity.cid_from_bytes(bytes(mid)) == identity.cid_from_bytes(data)
    assert identity.full_hash_bytes(bytes(mid)) != identity.full_hash_bytes(data)


def test_draft_decode_matches_reference(tmp_path):
    """Reduced-size JPEG path vs full decode: PSNR >= 40 dB on the model input (results/EXP_IMAGES.md gate)."""
    rng = np.random.default_rng(1)
    p = tmp_path / "big.jpg"
    synth.jpeg(str(p), rng, size=(2600, 1950))
    data = p.read_bytes()
    im, orig, s, strat = decode_photo(data, ".jpg", "draft")
    assert strat == "draft" and s == 2 and orig == (2600, 1950)
    a = T.to_model_input(im, orig, s)
    b = T.to_model_input(T.decode_image(io.BytesIO(data)))
    rng_ = (b.max() - b.min())
    psnr = 10 * np.log10(rng_ ** 2 / np.mean((a - b) ** 2))
    assert psnr >= 40


def test_gif_first_frame(dataset):
    root, _ = dataset
    data = open(os.path.join(root, "trip", "day2", "g_anim.gif"), "rb").read()
    im, _, _, strat = decode_photo(data, ".gif")
    assert im.mode == "RGB" and im.size == (200, 150) and strat == "full"


def test_sample_targets():
    assert sample_targets(10, 32) == [0.0, 1.667, 3.333, 5.0, 6.667, 8.333]
    assert len(sample_targets(3600, 32)) == 32 and sample_targets(0.5, 32) == [0.0]


def test_stream_rotation(dataset):
    root, _ = dataset
    with av.open(os.path.join(root, "videos", "v_rot.mp4")) as c:
        assert stream_rotation(c.streams.video[0]) == -90
    with av.open(os.path.join(root, "videos", "v_h264.mp4")) as c:
        assert stream_rotation(c.streams.video[0]) == 0


def test_discovery(dataset):
    root, exp = dataset
    items, counts = discover([root])
    kinds = [i["kind"] for i in items]
    assert kinds.count("photo") == exp["photos"] and kinds.count("video") == exp["videos"]
    assert counts["sidecars"] == exp["sidecars"] and sum(counts["other_ext"].values()) == exp["other"]
    assert [i["path"] for i in items] == sorted(i["path"] for i in items)
    again, _ = discover([root])
    assert [i["route"] for i in items] == [i["route"] for i in again]
    assert sum(1 for i in items if i["sidecar_rel"]) == 3
