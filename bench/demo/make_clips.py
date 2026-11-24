"""Demo videos for the screenshots, assembled from the CC0 demo photos (no download needed): each clip shows a few
photos in turn with a slow zoom, 4 s per photo, so moment search has distinct moments to find. H.264 MP4 with a
creation time and an ISO 6709 location, like a phone video.

usage: python bench/demo/make_clips.py DEMO_DIR      (after fetch_demo.py; adds the clips to ATTRIBUTION.json)
"""
import json, os, sys

import av
import numpy as np
from PIL import Image

D = sys.argv[1]
M = os.path.join(D, "media")
CLIPS = [  # file, creation time (UTC), location, photos in order
    ("iceland-2022/road-trip.mp4", "2022-08-06T14:10:00", (63.6156, -19.9886),
     ["iceland-2022/waterfall-iceland-2.jpg", "iceland-2022/icelandic-horse-1.jpg", "iceland-2022/glacier-lagoon-3.jpg",
      "iceland-2022/black-sand-beach-1.jpg", "iceland-2022/waterfall-iceland-5.jpg"]),
    ("kyoto-2024/day-walk.mp4", "2024-11-20T09:30:00", (35.0116, 135.7681),
     ["kyoto-2024/torii-gate-1.jpg", "kyoto-2024/bamboo-forest-2.jpg", "kyoto-2024/kyoto-temple-1.jpg",
      "kyoto-2024/ramen-1.jpg", "kyoto-2024/autumn-maple-leaves-1.jpg"]),
    ("lisbon-2024/evening.mp4", "2024-04-22T18:45:00", (38.7139, -9.1334),
     ["lisbon-2024/lisbon-tram-2.jpg", "lisbon-2024/azulejo-tiles-1.jpg", "lisbon-2024/city-street-night-2.jpg",
      "lisbon-2024/seafood-dish-2.jpg"]),
]
W, H, FPS, SEC = 1280, 720, 30, 4


def frames(img):
    """Slow zoom from 100% to 112% of a 16:9 centre crop."""
    im = Image.open(img).convert("RGB")
    s = max(W / im.width, H / im.height) * 1.12
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    n = FPS * SEC
    for i in range(n):
        w = round(W * 1.12 / (1.0 + 0.12 * i / (n - 1)))      # crop shrinks from 112% to 100% of the frame: zoom in
        h = round(w * H / W)
        x, y = (im.width - w) // 2, (im.height - h) // 2
        yield np.asarray(im.crop((x, y, x + w, y + h)).resize((W, H), Image.BILINEAR))


attr = json.load(open(os.path.join(D, "ATTRIBUTION.json")))
have = {a["file"] for a in attr}
for name, ts, (lat, lon), photos in CLIPS:
    photos = [p for p in photos if os.path.exists(os.path.join(M, p))]
    out = av.open(os.path.join(M, name), "w", options={"movflags": "+faststart"})
    out.metadata["creation_time"] = ts + ".000000Z"
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
    attr = [a for a in attr if a["file"] != name] + [dict(file=name, license="CC0 1.0",
            note="assembled for the demo from these CC0 photos: " + ", ".join(photos))]
    print(name, len(photos), "photos,", len(photos) * SEC, "s")
json.dump(attr, open(os.path.join(D, "ATTRIBUTION.json"), "w"), indent=1)
