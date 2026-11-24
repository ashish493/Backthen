"""Speed options from results/BOTTLENECK_ARCHIVE355.md: --dedup skip, --route-key content, --read-order disk.
Each must leave the manifest identical (apart from timing) to a run without it."""
import os
import shutil
import sqlite3

from conftest import load
from roamingest.cli import verify
from roamingest.persistence import Manifest
from roamingest.pipeline import manifest_path


def _copies(lib):
    """Add byte-identical copies of one photo, one PNG and two videos under new names (a second 'album')."""
    album = os.path.join(lib, "zz_album_copy")
    os.makedirs(album)
    pairs = [(("trip", "day1", "b_rotated.jpg"), "b.jpg"), (("trip", "day2", "e.png"), "e.png"),
             (("videos", "v_h264.mp4"), "v1.mp4"), (("videos", "v_hevc.mp4"), "v2.mp4")]
    for src, dst in pairs:
        shutil.copyfile(os.path.join(lib, *src), os.path.join(album, dst))
    return [os.path.join("zz_album_copy", d) for _, d in pairs], {os.path.join(*s): os.path.join("zz_album_copy", d) for s, d in pairs}


def _assets(out):
    con = sqlite3.connect(manifest_path(out))
    rows = sorted(con.execute("SELECT asset_id, n_frames, capture_time_utc, lat, lon, width, height FROM assets"))
    con.close()
    return rows


def test_dedup_skip_links_known_copies(dataset, ingest, tmp_path):
    root, _ = dataset
    lib = str(tmp_path / "lib")
    shutil.copytree(root, lib)
    # pass 1: the original library; dedup must give exactly the manifest of a run without it (default path routing)
    _, ref = ingest(lib, "ref", dedup="off")
    _, ded = ingest(lib, "ded", dedup="skip")
    assert load(ref) == load(ded)
    new, src_of = _copies(lib)
    # pass 2: only the new copies are processed; with dedup they are linked without decoding
    r_ref, _ = ingest(lib, "ref", dedup="off")
    r_ded, _ = ingest(lib, "ded", dedup="skip")
    assert r_ref["files_processed"] == r_ded["files_processed"] == 4
    assert r_ref["files_dedup_linked"] == 0
    assert r_ded["files_dedup_linked"] == 4
    assert r_ded["bytes_dedup_skipped"] == sum(os.path.getsize(os.path.join(lib, p)) for p in new)
    assert r_ded["unaccounted_files"] == 0
    # identical manifests: same file -> asset mapping, frames and vectors (dedup only skipped work)
    a, b = load(ref), load(ded)
    assert a == b
    for orig, cp in src_of.items():
        assert b[cp] == b[orig]                           # the copy points at the original's frames
    assert _assets(ref) == _assets(ded)
    con = sqlite3.connect(manifest_path(ded))
    strat = dict(con.execute("SELECT rel_path, strategy FROM file_stats"))
    con.close()
    assert all(strat[p] == "dedup_link" for p in new)
    m = Manifest(manifest_path(ded))
    v = verify(m)
    m.close()
    assert v["ok"], v


def test_dedup_skip_reads_fewer_bytes(dataset, ingest, tmp_path):
    root, _ = dataset
    lib = str(tmp_path / "lib")
    shutil.copytree(root, lib, ignore=shutil.ignore_patterns("videos"))
    import numpy as np
    from PIL import Image
    big = os.path.join(lib, "trip", "day1", "noise.jpg")                    # incompressible: about 2 MB
    Image.fromarray(np.random.default_rng(5).integers(0, 256, (1200, 1600, 3), dtype=np.uint8)).save(big, quality=95)
    ingest(lib, "o", dedup="skip")
    album = os.path.join(lib, "zz")
    os.makedirs(album)
    for i in range(5):
        shutil.copyfile(big, os.path.join(album, f"c{i}.jpg"))
    rep, _ = ingest(lib, "o", dedup="skip")
    assert rep["files_dedup_linked"] == 5
    size = os.path.getsize(big)
    assert size > 3 * 128 * 1024
    assert rep["process_read_bytes"] < 5 * size / 2      # 2 x 64 KiB per copy instead of the whole file


def test_route_key_content_gives_copies_identical_frames(dataset, ingest):
    """With content routing, a copy decodes on the same decoder as the original, so every copy has identical frames
    even when it is the copy that gets processed (no owner rule needed)."""
    root, _ = dataset
    _, o = ingest(root, "rk", route_key="content", nvdec_pct=50)
    r = load(o)
    a, b = os.path.join("trip", "day1", "a_exif_gps.jpg"), os.path.join("trip", "day2", "a_copy.jpg")
    assert r[a] == r[b]


def test_read_order_disk_same_manifest(dataset, ingest):
    root, _ = dataset
    _, ref = ingest(root, "path", read_order="path")
    rep, o = ingest(root, "disk", read_order="disk")
    assert rep["read_order"] == "disk" and rep["unaccounted_files"] == 0
    assert load(o) == load(ref)


def test_dedup_copy_with_smaller_path_becomes_owner(dataset, ingest, tmp_path):
    """A new copy that sorts BEFORE the committed original must be decoded (it is the owner now), not linked: the
    asset then holds exactly what a run without dedup stores, sidecar-derived metadata included."""
    import json
    root, _ = dataset
    lib = str(tmp_path / "lib")
    shutil.copytree(root, lib)
    ingest(lib, "ref", dedup="off")
    ingest(lib, "ded", dedup="skip")
    first = os.path.join(lib, "aa_first")                 # sorts before 'trip' and 'videos'
    os.makedirs(first)
    shutil.copyfile(os.path.join(lib, "trip", "day1", "c_local_time.jpg"), os.path.join(first, "c.jpg"))
    with open(os.path.join(first, "c.jpg.json"), "w") as f:  # a sidecar the original does not have
        json.dump({"title": "c.jpg", "photoTakenTime": {"timestamp": "1600000000"}}, f)
    shutil.copyfile(os.path.join(lib, "videos", "v_h264.mp4"), os.path.join(first, "v.mp4"))
    r_ref, ref = ingest(lib, "ref", dedup="off")
    r_ded, ded = ingest(lib, "ded", dedup="skip")
    assert r_ded["files_dedup_linked"] == 0 and r_ded["files_processed"] == 2
    assert load(ref) == load(ded)
    assert _assets(ref) == _assets(ded)
    con = sqlite3.connect(manifest_path(ded))
    src = con.execute("SELECT capture_time_source FROM assets a JOIN asset_files f USING(asset_id) "
                      "WHERE f.rel_path = ?", (os.path.join("aa_first", "c.jpg"),)).fetchone()[0]
    con.close()
    assert src == "sidecar"                               # the owner's sidecar now dates the shared asset


def test_dedup_with_disk_order_matches_reference(dataset, ingest, tmp_path):
    root, _ = dataset
    lib = str(tmp_path / "lib")
    shutil.copytree(root, lib)
    _copies(lib)
    _, ref = ingest(lib, "ref", dedup="off")
    rep, fast = ingest(lib, "fast", dedup="skip", read_order="disk", storage="hdd")
    assert rep["unaccounted_files"] == 0
    assert load(ref) == load(fast) and _assets(ref) == _assets(fast)
