"""Short demo videos for the travel library, assembled from its own CC0 photos (Commons rate-limits video
downloads): each clip shows a few photos of one trip in turn with a slow zoom, 4 s each, as H.264 with a creation time
and an ISO 6709 location taken from the first photo's EXIF, like a phone video. Moment search then finds the shot.

usage: python bench/demo/make_travel_clips.py DEMO_DIR      (after build_travel_demo.py; adds the clips to ATTRIBUTION)
"""
import datetime as dt
import json
import os
import sys

import av
import numpy as np
from PIL import Image

D = sys.argv[1]
M = os.path.join(D, "media")
CLIPS = [("italy-2023", "evening-walk.mp4"), ("japan-2024", "temple-day.mp4"), ("peru-2022", "the-hike.mp4")]
W, H, FPS, SEC = 1280, 720, 30, 4


def exif_of(path):
    ex = Image.open(path).getexif()
    when = dt.datetime.strptime(ex.get_ifd(0x8769)[36867], "%Y:%m:%d %H:%M:%S")
    g = ex.get_ifd(0x8825)
    to = lambda v, ref: (v[0] + v[1] / 60 + v[2] / 3600) * (-1 if ref in ("S", "W") else 1)
    return when, (to(g[2], g[1]), to(g[4], g[3]))


def frames(img):
    im = Image.open(img).convert("RGB")
    s = max(W / im.width, H / im.height) * 1.12
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    n = FPS * SEC
    for i in range(n):
        w = round(W * 1.12 / (1.0 + 0.12 * i / (n - 1)))
        h = round(w * H / W)
        x, y = (im.width - w) // 2, (im.height - h) // 2
        yield np.asarray(im.crop((x, y, x + w, y + h)).resize((W, H), Image.BILINEAR))


attr = json.load(open(os.path.join(D, "ATTRIBUTION.json")))
for trip, name in CLIPS:
    files = sorted(f for f in os.listdir(os.path.join(M, trip)) if f.endswith(".jpg"))
    scenery = [f for f in files if f.startswith("scene-")][:3]
    groups = [f for f in files if f.startswith("group-")][:1]
    photos = [f"{trip}/{f}" for f in scenery[:2] + groups + scenery[2:]]
    if not photos:
        continue
    when, (lat, lon) = exif_of(os.path.join(M, photos[0]))
    out = av.open(os.path.join(M, trip, name), "w", options={"movflags": "+faststart"})
    out.metadata["creation_time"] = when.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    out.metadata["location"] = f"{lat:+08.4f}{lon:+09.4f}/"
    st = out.add_stream("libx264", rate=FPS)
    st.width, st.height, st.pix_fmt = W, H, "yuv420p"
    st.options = {"crf": "24", "preset": "medium"}
    for p in photos:
        for a in frames(os.path.join(M, p)):
            for pkt in st.encode(av.VideoFrame.from_ndarray(a, format="rgb24")):
                out.mux(pkt)
    for pkt in st.encode():
        out.mux(pkt)
    out.close()
    attr = [a for a in attr if a["file"] != f"{trip}/{name}"] + [dict(file=f"{trip}/{name}", license="CC0 1.0",
            note="assembled for the demo from these photos: " + ", ".join(photos))]
    print(trip, name, len(photos), "photos")
json.dump(attr, open(os.path.join(D, "ATTRIBUTION.json"), "w"), indent=1)
