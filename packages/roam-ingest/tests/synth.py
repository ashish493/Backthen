"""Synthetic test dataset (no personal data). Deterministic for a given seed.

    python packages/roam-ingest/tests/synth.py OUT_DIR      # write the dataset somewhere to look at it
"""
import io
import json
import os
import shutil
import sys

import av
import numpy as np
from PIL import Image

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HAVE_HEIC = True
except Exception:  # noqa: BLE001
    HAVE_HEIC = False

EXIF_LOCAL = "2023:05:06 07:08:09"
EXIF_OFFSET = "+05:30"
EXIF_UTC = "2023-05-06T01:38:09Z"
GPS = (35.0116, 135.7681)              # decimal degrees encoded as DMS below
SIDECAR_TS = 1700000000                 # 2023-11-14T22:13:20Z
SIDECAR_UTC = "2023-11-14T22:13:20Z"
SIDECAR_GPS = (41.1579, -8.6291)
VIDEO_CT = "2024-01-02T03:04:05.000000Z"
VIDEO_UTC = "2024-01-02T03:04:05Z"
VIDEO_LOC = "+27.1751+078.0421+171.000/"


def smooth(rng, w, h, cells=12):
    small = rng.integers(0, 256, (max(2, h // (h // cells or 1)), cells, 3), dtype=np.uint8)
    return np.asarray(Image.fromarray(small).resize((w, h), Image.BICUBIC))


def _dms(x):
    d = int(x)
    m = int((x - d) * 60)
    s = round(((x - d) * 60 - m) * 60, 4)
    return (float(d), float(m), float(s))


def jpeg(path, rng, size=(1600, 1200), exif_time=True, offset=True, gps=False, orientation=None, quality=88):
    im = Image.fromarray(smooth(rng, *size))
    ex = Image.Exif()
    if orientation:
        ex[274] = orientation
    if exif_time:
        sub = ex.get_ifd(0x8769)
        sub[36867] = EXIF_LOCAL
        if offset:
            sub[36881] = EXIF_OFFSET
    if gps:
        g = ex.get_ifd(0x8825)
        g[1], g[2], g[3], g[4] = "N", _dms(GPS[0]), "E", _dms(GPS[1])
        g[5], g[6] = 0, 12.5
    im.save(path, "JPEG", quality=quality, exif=ex.tobytes())


def video(path, codec="libx264", seconds=12, fps=15, size=(320, 240), gop=30, rotation=0, fmt=None,
          creation_time=None, location=None, seed=0):
    rng = np.random.default_rng(seed)
    base = smooth(rng, size[0] * 2, size[1])
    with av.open(path, "w", format=fmt) as c:
        if creation_time:
            c.metadata["creation_time"] = creation_time
        if location:
            c.metadata["location"] = location
        s = c.add_stream(codec, rate=fps)
        s.width, s.height, s.pix_fmt = size[0], size[1], "yuv420p"
        if codec == "libx264":
            s.options = {"g": str(gop), "keyint_min": str(gop), "sc_threshold": "0", "bf": "2"}
        elif codec == "libx265":
            s.options = {"x265-params": f"keyint={gop}:min-keyint={gop}:scenecut=0:log-level=error"}
        else:
            s.codec_context.gop_size = gop
        if rotation:
            s.set_display_rotation(rotation)
        for i in range(seconds * fps):
            x = (i * 7) % size[0]
            img = base[:, x:x + size[0]].copy()
            y0 = (i * 5) % (size[1] - 40)
            img[y0:y0 + 40, 20:60] = (255 * ((i // fps) % 2), 128, 255 - 20 * (i % 12))
            for pkt in s.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
                c.mux(pkt)
        for pkt in s.encode():
            c.mux(pkt)


def sidecar(path, title, ts=SIDECAR_TS, gps=SIDECAR_GPS):
    d = {"title": title, "description": "", "imageViews": "0",
         "creationTime": {"timestamp": str(ts + 86400 * 30), "formatted": ""},
         "photoTakenTime": {"timestamp": str(ts), "formatted": ""},
         "geoData": {"latitude": gps[0], "longitude": gps[1], "altitude": 500.0, "latitudeSpan": 0.0, "longitudeSpan": 0.0},
         "people": [{"name": "synthetic"}], "url": "https://example.invalid/x"}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f)


def build(root, seed=0, videos=True):
    """Write the dataset. Returns a dict describing what to expect."""
    rng = np.random.default_rng(seed)
    os.makedirs(os.path.join(root, "trip", "day1"), exist_ok=True)
    os.makedirs(os.path.join(root, "trip", "day2"), exist_ok=True)
    os.makedirs(os.path.join(root, "takeout"), exist_ok=True)
    d1, d2, tk = (os.path.join(root, *p) for p in (("trip", "day1"), ("trip", "day2"), ("takeout",)))
    jpeg(os.path.join(d1, "a_exif_gps.jpg"), rng, size=(2600, 1950), gps=True)          # draft scale 1/2
    jpeg(os.path.join(d1, "b_rotated.jpg"), rng, orientation=6)
    jpeg(os.path.join(d1, "c_local_time.jpg"), rng, offset=False)
    jpeg(os.path.join(d1, "d_plain.JPG"), rng, exif_time=False, size=(800, 600))
    shutil.copyfile(os.path.join(d1, "a_exif_gps.jpg"), os.path.join(d2, "a_copy.jpg"))  # exact duplicate
    Image.fromarray(smooth(rng, 640, 480)).save(os.path.join(d2, "e.png"))
    Image.fromarray(smooth(rng, 640, 480)).save(os.path.join(d2, "f.webp"), quality=90)
    g1, g2 = Image.fromarray(smooth(rng, 200, 150)), Image.fromarray(smooth(rng, 200, 150))
    g1.save(os.path.join(d2, "g_anim.gif"), save_all=True, append_images=[g2], duration=100, loop=0)
    if HAVE_HEIC:
        Image.fromarray(smooth(rng, 640, 480)).save(os.path.join(d2, "h.heic"), quality=90)
    # Takeout: sidecar naming rules
    jpeg(os.path.join(tk, "beach.jpg"), rng, exif_time=False)
    sidecar(os.path.join(tk, "beach.jpg.supplemental-metadata.json"), "beach.jpg")
    jpeg(os.path.join(tk, "beach(1).jpg"), rng, exif_time=False)
    sidecar(os.path.join(tk, "beach.jpg(1).json"), "beach(1).jpg")
    jpeg(os.path.join(tk, "zero_gps.jpg"), rng, exif_time=False)
    sidecar(os.path.join(tk, "zero_gps.jpg.json"), "zero_gps.jpg", gps=(0.0, 0.0))
    sidecar(os.path.join(tk, "metadata.json"), "album")                                  # album file, no media
    with open(os.path.join(root, "notes.txt"), "w") as f:
        f.write("not media")
    exp = dict(photos=12 if HAVE_HEIC else 11, sidecars=4, other=1)
    if videos:
        vd = os.path.join(root, "videos")
        os.makedirs(vd, exist_ok=True)
        video(os.path.join(vd, "v_h264.mp4"), seed=1)
        video(os.path.join(vd, "v_hevc.mp4"), codec="libx265", seconds=8, seed=2)
        video(os.path.join(vd, "v_rot.mp4"), seconds=6, rotation=-90, creation_time=VIDEO_CT, location=VIDEO_LOC, seed=3)
        video(os.path.join(vd, "v_clip.3gp"), codec="mpeg4", seconds=5, size=(176, 144), fmt="3gp", seed=4)
        exp["videos"] = 4
    return exp


if __name__ == "__main__":
    print(build(sys.argv[1]))
