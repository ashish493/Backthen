"""Engine tests on a small synthetic library with known semantics (solid colours) - no personal data."""
import os

import av
import numpy as np
import pytest
from PIL import Image

EXIF_LOCAL, EXIF_OFFSET = "2023:05:06 07:08:09", "+05:30"          # -> 2023-05-06T01:38:09Z
ATHENS = (37.9838, 23.7275)
THESSALONIKI = (40.6401, 22.9444)
SIDECAR_TS = 1700000000                                              # 2023-11-14T22:13:20Z
VIDEO_CT = "2024-01-02T03:04:05.000000Z"

COLOURS = dict(red=(220, 30, 30), green=(30, 180, 40), blue=(30, 60, 220), yellow=(240, 220, 30))


def _dms(x):
    d = int(x); m = int((x - d) * 60); s = round(((x - d) * 60 - m) * 60, 4)
    return (float(d), float(m), float(s))


def solid(rgb, w=640, h=480, seed=0):
    rng = np.random.default_rng(seed)
    a = np.clip(np.array(rgb, np.float32)[None, None] + rng.normal(0, 6, (h, w, 3)), 0, 255)
    return Image.fromarray(a.astype(np.uint8))


def jpeg(path, rgb, exif_time=False, gps=None):
    ex = Image.Exif()
    if exif_time:
        sub = ex.get_ifd(0x8769)
        sub[36867], sub[36881] = EXIF_LOCAL, EXIF_OFFSET
    if gps:
        g = ex.get_ifd(0x8825)
        g[1], g[2], g[3], g[4] = "N", _dms(gps[0]), "E", _dms(gps[1])
    solid(rgb).save(path, "JPEG", quality=90, exif=ex.tobytes())


def two_colour_video(path, first, second, seconds=12, fps=15, size=(320, 240)):
    with av.open(path, "w") as c:
        c.metadata["creation_time"] = VIDEO_CT
        s = c.add_stream("libx264", rate=fps)
        s.width, s.height, s.pix_fmt = size[0], size[1], "yuv420p"
        s.options = {"g": "30", "keyint_min": "30", "sc_threshold": "0"}
        a, b = (np.asarray(solid(x, *size)) for x in (first, second))
        for i in range(seconds * fps):
            img = a if i < seconds * fps // 2 else b
            for pkt in s.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
                c.mux(pkt)
        for pkt in s.encode():
            c.mux(pkt)


def build(root):
    import json
    os.makedirs(os.path.join(root, "a"), exist_ok=True)
    os.makedirs(os.path.join(root, "b"), exist_ok=True)
    jpeg(os.path.join(root, "a", "red.jpg"), COLOURS["red"], exif_time=True, gps=ATHENS)
    jpeg(os.path.join(root, "a", "green.jpg"), COLOURS["green"])
    with open(os.path.join(root, "a", "green.jpg.json"), "w") as f:
        json.dump({"title": "green.jpg", "photoTakenTime": {"timestamp": str(SIDECAR_TS)},
                   "geoData": {"latitude": THESSALONIKI[0], "longitude": THESSALONIKI[1], "altitude": 0.0}}, f)
    jpeg(os.path.join(root, "a", "blue.jpg"), COLOURS["blue"])
    solid(COLOURS["yellow"]).save(os.path.join(root, "b", "yellow.png"))
    import shutil
    shutil.copyfile(os.path.join(root, "a", "red.jpg"), os.path.join(root, "b", "red_copy.jpg"))
    two_colour_video(os.path.join(root, "b", "blue_then_red.mp4"), COLOURS["blue"], COLOURS["red"])


@pytest.fixture(scope="session")
def library(tmp_path_factory):
    """-> (source_root, library_dir) after one ingest."""
    from roamingest.config import IngestConfig
    from roamingest.embedder import ClipEmbedder
    from roamingest.pipeline import Ingestor
    root = str(tmp_path_factory.mktemp("src") / "lib")
    build(root)
    out = str(tmp_path_factory.mktemp("library"))
    cfg = IngestConfig(roots=[root], out_dir=out, storage="ssd", quiet=True, min_free_gb=0, timeline=False)
    rep = Ingestor(cfg, embedder=ClipEmbedder(batch=16)).run()
    assert rep["unaccounted_files"] == 0 and sum(rep["errors"].values()) == 0
    return root, out


@pytest.fixture(scope="session")
def engine(library):
    from roamengine.engine import Engine
    return Engine(library[1])
