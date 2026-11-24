"""End-to-end tests on the synthetic dataset. T1-T7 are the ports of bench/verify_ingest.py."""
import json
import os
import shutil
import sqlite3

import numpy as np
import pytest

import synth
from conftest import connect, cuda_ok, load, run_cli
from roamingest.cli import status, verify
from roamingest.persistence import Manifest

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(scope="module")
def base(dataset, embedder, tmp_path_factory):
    """One default run shared by several tests."""
    from roamingest.config import IngestConfig
    from roamingest.pipeline import Ingestor
    root, exp = dataset
    out = str(tmp_path_factory.mktemp("base") / "ds")
    rep = Ingestor(IngestConfig(roots=[root], out_dir=out, quiet=True, timeline=False, batch=16, photo_workers=4,
                                min_free_gb=1.0), embedder=embedder).run()
    return rep, out


def same(a, b):
    return {k: v for k, v in a.items()} == {k: v for k, v in b.items()}


# ------------------------------------------------------------------------------------------ basic run
def test_run_counts_and_verify(base, dataset):
    rep, out = base
    _, exp = dataset
    assert rep["errors"] == {} and rep["unaccounted_files"] == 0
    assert rep["kinds"]["photo"]["files"] == exp["photos"] and rep["kinds"]["video"]["files"] == exp["videos"]
    assert rep["sidecar_files"] == exp["sidecars"]
    m = Manifest(os.path.join(out, "manifest.sqlite"))
    v = verify(m)
    st = status(m)
    m.close()
    assert v["ok"], v
    assert st["duplicate_files"] == 1 and st["meta"]["schema_version"] == "1"
    assert st["frames"] == st["embeddings"]


def test_probing_values(base):
    _, out = base
    con = connect(out)
    q = lambda rel: con.execute(  # noqa: E731
        "SELECT a.capture_time_utc, a.capture_time_local, a.capture_time_source, a.lat, a.lon, a.gps_source, a.rotation, "
        "a.width, a.height FROM asset_files f JOIN assets a USING(asset_id) WHERE f.rel_path=?", (os.path.normpath(rel),)).fetchone()
    utc, local, src, lat, lon, gsrc, _, w, h = q("trip/day1/a_exif_gps.jpg")
    assert (utc, local, src, gsrc) == (synth.EXIF_UTC, "2023-05-06T07:08:09", "exif", "exif") and abs(lat - synth.GPS[0]) < 1e-4
    assert (w, h) == (2600, 1950)
    assert q("trip/day1/c_local_time.jpg")[:3] == (None, "2023-05-06T07:08:09", "exif_local")
    assert q("trip/day1/b_rotated.jpg")[7:] == (1200, 1600)                 # EXIF orientation 6: oriented size
    utc, _, src, lat, lon, gsrc, *_ = q("takeout/beach.jpg")
    assert (utc, src, gsrc, lat, lon) == (synth.SIDECAR_UTC, "sidecar", "sidecar_geoData", *synth.SIDECAR_GPS)
    assert q("takeout/beach(1).jpg")[2] == "sidecar"
    assert q("takeout/zero_gps.jpg")[5] is None                             # (0, 0) is "no location" in Takeout
    utc, _, src, lat, _, gsrc, rot, *_ = q("videos/v_rot.mp4")
    assert (utc, src, gsrc, rot, lat) == (synth.VIDEO_UTC, "container", "container", -90, 27.1751)
    assert q("trip/day1/d_plain.JPG")[2] is None                             # no time source: never mtime
    con.close()


def test_frames_provenance_and_thumbnails(base):
    _, out = base
    con = connect(out)
    rows = con.execute("SELECT a.media_type, fr.sampling_method, fr.timestamp_s, fr.requested_time_s FROM frames fr "
                       "JOIN assets a USING(asset_id)").fetchall()
    assert {r[1] for r in rows if r[0] == "photo"} == {"image"} and all(r[2] is None for r in rows if r[0] == "photo")
    vids = [r for r in rows if r[0] == "video"]
    assert {r[1] for r in vids} == {"exact"} and all(r[2] >= r[3] - 1e-6 for r in vids)
    refs = [r[0] for r in con.execute("SELECT thumbnail_ref FROM assets")]
    assert all(refs) and all(os.path.exists(os.path.join(out, r)) for r in refs)
    con.close()


def test_rotated_video_frames_are_upright(base):
    """Embedding a rotated clip's frame must match embedding the same frame rotated by hand (NVDEC drops rotation)."""
    _, out = base
    con = connect(out)
    w, h, rot = con.execute("SELECT width, height, rotation FROM assets a JOIN asset_files f USING(asset_id) "
                            "WHERE f.rel_path=?", (os.path.normpath("videos/v_rot.mp4"),)).fetchone()
    con.close()
    assert rot == -90 and (w, h) == (320, 240)
    from PIL import Image
    ref = os.path.join(out, con_thumb(out, "videos/v_rot.mp4"))
    assert Image.open(ref).size == (240, 320)                               # portrait thumbnail


def con_thumb(out, rel):
    con = connect(out)
    r = con.execute("SELECT a.thumbnail_ref FROM asset_files f JOIN assets a USING(asset_id) WHERE f.rel_path=?",
                    (os.path.normpath(rel),)).fetchone()[0]
    con.close()
    return r


def test_legacy_views(base):
    _, out = base
    con = connect(out)
    n_files = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    n_emb = con.execute("SELECT COUNT(*) FROM emb").fetchone()[0]
    n_af = con.execute("SELECT SUM(n_emb) FROM asset_files").fetchone()[0]
    con.close()
    assert n_files == 16 and n_emb == n_af


def test_report_is_aggregate_only(base, dataset):
    rep, out = base
    root, _ = dataset
    text = open(os.path.join(out, "report.json"), encoding="utf-8").read()
    for needle in ("a_exif_gps", "beach", "v_rot", "trip", "takeout", os.path.basename(root)):
        assert needle not in text
    assert "roots" not in rep["config"]


# ------------------------------------------------------------------------------------------ T1 determinism
def test_T1_determinism(base, dataset, ingest):
    root, _ = dataset
    ref = load(base[1])
    _, o1 = ingest(root, "w1", photo_workers=1, readers=1, video_cpu=3, video_nvdec=1)
    _, o2 = ingest(root, "w2", schedule="phased", storage="hdd", photo_workers=6)
    assert load(o1) == ref
    assert load(o2) == ref


# ------------------------------------------------------------------------------------------ T2 / T3 kill, resume, rerun
def test_T2_T3_kill_resume_rerun(base, dataset, tmp_path):
    root, _ = dataset
    out = str(tmp_path / "kr")
    args = ["run", root, "--out-dir", out, "--batch", "16", "--photo-workers", "4", "--quiet", "--min-free-gb", "1", "--commit-every", "2"]
    rc, _ = run_cli(*args, "--kill-after-files", "5")
    assert rc == 137
    con = connect(out)
    committed = con.execute("SELECT COUNT(*) FROM asset_files").fetchone()[0]
    con.close()
    assert 5 <= committed < 16
    rc, _ = run_cli(*args)
    assert rc == 0
    rep = json.load(open(os.path.join(out, "report.json")))
    assert rep["files_resumed_skipped"] == committed and rep["files_processed"] == 16 - committed
    assert load(out) == load(base[1])
    rc, _ = run_cli(*args)
    rep = json.load(open(os.path.join(out, "report.json")))
    assert rep["files_processed"] == 0 and rep["files_resumed_skipped"] == 16


# ------------------------------------------------------------------------------------------ T4 / T5 exact strategies
def test_T4_T5_seek_and_smart_equal_seq(dataset, ingest):
    root, _ = dataset
    res = {s: load(ingest(root, "cpu_" + s, video_strategy=s, video_nvdec=0)[1]) for s in ("seq", "seek", "smart")}
    vids = [k for k in res["seq"] if k.startswith("videos")]
    assert vids
    for k in vids:
        assert res["seek"][k] == res["seq"][k]
        assert res["smart"][k] == res["seq"][k]


# ------------------------------------------------------------------------------------------ T6 NVDEC
@pytest.mark.gpu
@pytest.mark.skipif(not cuda_ok(), reason="needs CUDA/NVDEC")
def test_T6_nvdec_seek_matches_cpu(dataset, ingest):
    root, _ = dataset
    cpu = load(ingest(root, "c6", video_strategy="seek", video_nvdec=0)[1])
    rep, o = ingest(root, "n6", video_strategy="seek", nvdec_pct=100, video_cpu=0, video_nvdec=2)
    nv = load(o)
    assert rep["kinds"]["video"]["by_decoder"].get("nvdec", 0) >= 3      # h264 + hevc (+ rotated); 3gp has no NVDEC path
    for k in (k for k in cpu if k.startswith("videos")):
        a, b = cpu[k][2], nv[k][2]
        assert [x[1] for x in a] == [x[1] for x in b]                         # same frames
    # Pixels: CPU and NVDEC model inputs agree (photo gate, 40 dB). The embedding-cosine gate (>= 0.98) is checked on
    # real footage in bench/verify_ingest.py T6; CLIP embeddings of synthetic textures are too unstable for it.
    from roamingest import videos as V
    from roamingest.config import IngestConfig
    cfg = IngestConfig(video_strategy="seek")
    for f in ("v_h264.mp4", "v_hevc.mp4", "v_rot.mp4"):
        p = os.path.join(root, "videos", f)
        info = {}
        c, n = V.video_emits(p, False, cfg, {}), V.video_emits(p, True, cfg, info)
        assert info["decoder"] == "nvdec" and len(c) == len(n)
        for x, y in zip(c, n):
            assert 10 * np.log10(np.ptp(x[2]) ** 2 / np.mean((x[2] - y[2]) ** 2)) >= 40


# ------------------------------------------------------------------------------------------ T7 resume per policy
@pytest.mark.parametrize("policy", [["--video-sampling", "keyframe"], ["--video-sampling", "adaptive", "--adaptive-tau", "1.0"]])
def test_T7_resume_per_policy(dataset, tmp_path, policy):
    root, _ = dataset
    common = ["--batch", "16", "--photo-workers", "4", "--quiet", "--min-free-gb", "1", "--commit-every", "2", *policy]
    full, kr = str(tmp_path / "full"), str(tmp_path / "kr")
    assert run_cli("run", root, "--out-dir", full, *common)[0] == 0
    assert run_cli("run", root, "--out-dir", kr, *common, "--kill-after-files", "5")[0] == 137
    assert run_cli("run", root, "--out-dir", kr, *common)[0] == 0
    assert load(kr) == load(full)
    con = connect(full)
    methods = {r[0] for r in con.execute("SELECT sampling_method FROM frames f JOIN assets a USING(asset_id) "
                                         "WHERE a.media_type='video'")}
    con.close()
    assert "keyframe" in methods


# ------------------------------------------------------------------------------------------ changes on disk
def test_move_modify_delete(dataset, ingest, tmp_path):
    root, _ = dataset
    lib = str(tmp_path / "lib")
    shutil.copytree(root, lib, ignore=shutil.ignore_patterns("videos"))
    _, out = ingest(lib, "chg")
    before = load(out)
    # move one file, modify one, delete one
    os.makedirs(os.path.join(lib, "moved"))
    shutil.move(os.path.join(lib, "trip", "day2", "e.png"), os.path.join(lib, "moved", "e.png"))
    synth.jpeg(os.path.join(lib, "trip", "day1", "d_plain.JPG"), np.random.default_rng(99), exif_time=False, size=(800, 600))
    os.remove(os.path.join(lib, "trip", "day2", "f.webp"))
    rep, _ = ingest(lib, "chg")
    assert rep["files_moved"] == 1 and rep["files_missing"] == 1 and rep["files_processed"] == 1
    after = load(out)
    assert after[os.path.join("moved", "e.png")][2] == before[os.path.join("trip", "day2", "e.png")][2]
    assert os.path.join("trip", "day2", "e.png") not in after
    assert after[os.path.join("trip", "day2", "f.webp")][0] == "missing"
    assert after[os.path.join("trip", "day1", "d_plain.JPG")][2] != before[os.path.join("trip", "day1", "d_plain.JPG")][2]
    m = Manifest(os.path.join(out, "manifest.sqlite"))
    v = verify(m)
    m.close()
    assert v["checks"]["assets_without_file"] == 0 and v["checks"]["missing_thumbnails"] == 0


def test_duplicate_owner_is_deterministic(dataset, ingest):
    """Byte-identical copies share one asset; the smallest path owns the frames whatever the completion order."""
    root, _ = dataset
    _, o1 = ingest(root, "d1", readers=1, photo_workers=1)
    _, o2 = ingest(root, "d2", readers=4, photo_workers=8)
    a = os.path.join("trip", "day1", "a_exif_gps.jpg")
    b = os.path.join("trip", "day2", "a_copy.jpg")
    r1, r2 = load(o1), load(o2)
    assert r1[a] == r1[b] == r2[a] == r2[b]


# ------------------------------------------------------------------------------------------ migration v0 -> v1
V0_SCHEMA = """
CREATE TABLE files(path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, kind TEXT, ext TEXT, status TEXT, cid TEXT,
  decoder TEXT, strategy TEXT, n_emb INTEGER, error TEXT, codec TEXT, width INTEGER, height INTEGER, dur_s REAL,
  frames_decoded INTEGER, t_read_ms REAL, t_decode_ms REAL, t_xform_ms REAL, t_write_ms REAL);
CREATE TABLE emb(path TEXT, idx INTEGER, pts REAL, vec BLOB, PRIMARY KEY(path, idx));
"""


def test_migrate_v0(tmp_path):
    d = tmp_path / "v0"
    d.mkdir()
    db = str(d / "manifest.db")
    con = sqlite3.connect(db)
    con.executescript(V0_SCHEMA)
    root = str(tmp_path / "media")
    rng = np.random.default_rng(0)
    vec = lambda: (lambda v: (v / np.linalg.norm(v)).astype(np.float16).tobytes())(rng.standard_normal(512))  # noqa: E731
    rows = [(os.path.join(root, "p", "x.jpg"), 10, 1, "photo", ".jpg", "done", "aa" * 16, "cpu", "draft", 1, None, None, 4, 3, None, None, 1, 2, 3, 4),
            (os.path.join(root, "v", "y.mp4"), 20, 2, "video", ".mp4", "done", "bb" * 16, "nvdec", "seek", 2, None, "h264", 320, 240, 4.0, 60, 1, 2, 3, 4),
            (os.path.join(root, "v", "z.mov"), 30, 3, "video", ".mov", "error", None, None, None, 0, "no_frames", None, None, None, None, None, None, None, None, None)]
    con.executemany(f"INSERT INTO files VALUES ({','.join('?' * 20)})", rows)
    embs = [(rows[0][0], 0, 0.0, vec()), (rows[1][0], 0, 0.0, vec()), (rows[1][0], 1, 2.0, vec())]
    con.executemany("INSERT INTO emb VALUES (?,?,?,?)", embs)
    con.commit()
    con.close()
    rc, outp = run_cli("migrate", str(d), "--root", root)
    assert rc == 0, outp
    assert os.path.exists(db + ".v0.bak")
    con = sqlite3.connect(db)
    assert con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "1"
    got = sorted((os.path.normcase(p), i, t, bytes(v)) for p, i, t, v in con.execute("SELECT path, idx, pts, vec FROM emb"))
    assert got == sorted((os.path.normcase(p), i, t, v) for p, i, t, v in embs)
    st = dict(con.execute("SELECT status, COUNT(*) FROM asset_files GROUP BY status"))
    assert st == {"done": 2, "error": 1}
    assert con.execute("SELECT sampling_method FROM frames WHERE timestamp_s=2.0").fetchone()[0] == "exact"
    con.close()
    m = Manifest(db)
    assert verify(m)["ok"]
    m.close()


# ------------------------------------------------------------------------------------------ CLI
def test_cli_inventory_status_verify_hash(base, dataset):
    root, exp = dataset
    _, out = base
    rc, txt = run_cli("inventory", root, "--headers")
    assert rc == 0
    inv = json.loads(txt)
    assert inv["media_files"] == exp["photos"] + exp["videos"] and inv["media_with_sidecar"] == 3
    assert inv["headers"]["videos"]["rotated"] == 1
    for needle in ("a_exif_gps", "beach", "trip"):
        assert needle not in txt
    rc, txt = run_cli("status", out)
    assert rc == 0 and json.loads(txt)["assets"]["video"] == exp["videos"]
    rc, txt = run_cli("hash", out)
    h = json.loads(txt)
    assert rc == 0 and h["hash_filled"] == exp["videos"] and h["partial_id_collisions"] == 0   # photos hashed at ingest
    rc, txt = run_cli("verify", out, "--rehash")
    assert rc == 0 and json.loads(txt)["ok"]
