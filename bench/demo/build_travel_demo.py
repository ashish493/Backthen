"""Build the public travel demo library for the README from travel_demo.json: a hand-picked list of CC0 photos
(Flickr, StockSnap, Wikimedia Commons) arranged into six fictional trips and some days at home.

Capture dates, places and the grouping into trips are made up and written into the demo copies' EXIF. The group photos
are CC0 stock photos that were not taken at those places; the people in them are not named. Photos were chosen by eye:
adults only, nothing private or sensitive, no logos or posters.

usage: python bench/demo/build_travel_demo.py OUT_DIR
       -> OUT_DIR/media/<trip>/*.jpg, OUT_DIR/ATTRIBUTION.json, docs/screenshots/ATTRIBUTION.md
"""
import datetime as dt
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1]
UA = "Backthen-demo/0.3 (open-source local photo search)"
ITEMS = json.load(open(os.path.join(HERE, "travel_demo.json"), encoding="utf-8"))
CACHE = os.path.join(OUT, "cache")
os.makedirs(CACHE, exist_ok=True)


def fetch(url):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=90) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504):
                break
        except OSError:
            pass
        time.sleep(10 * (attempt + 1))
    return None


def dms(v):
    v = abs(v); d = int(v); m = int((v - d) * 60)
    return float(d), float(m), round(((v - d) * 60 - m) * 60, 4)


def save(data, path, taken, lat, lon):
    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((1800, 1800))
    ex = Image.Exif()
    stamp = dt.datetime.strptime(taken, "%Y-%m-%d %H:%M:%S").strftime("%Y:%m:%d %H:%M:%S")
    ex[0x0132] = stamp
    sub = ex.get_ifd(0x8769)
    sub[36867], sub[36881] = stamp, "+00:00"                  # DateTimeOriginal, OffsetTimeOriginal (demo times are UTC)
    if lat is not None:
        g = ex.get_ifd(0x8825)
        g[1], g[2], g[3], g[4] = ("N" if lat >= 0 else "S"), dms(lat), ("E" if lon >= 0 else "W"), dms(lon)
    im.save(path, "JPEG", quality=89, exif=ex)


done = []
for it in ITEMS:
    cached = os.path.join(CACHE, it["file"].replace("/", "__"))
    if not os.path.exists(cached):
        data = fetch(it["url"])
        if not data:
            print("could not download", it["file"], it["source"])
            continue
        with open(cached, "wb") as f:
            f.write(data)
        time.sleep(0.3)
    path = os.path.join(OUT, "media", it["file"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(cached, "rb") as f:
        save(f.read(), path, it["taken"], it["lat"], it["lon"])
    done.append(it)
json.dump([dict(file=i["file"], source=i["source"], title=i["title"], artist=i["author"], license="CC0 1.0") for i in done],
          open(os.path.join(OUT, "ATTRIBUTION.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
cell = lambda s: " ".join((s or "").replace("|", "/").replace("[", "(").replace("]", ")").split())[:80]
with open(os.path.join(HERE, "..", "..", "docs", "screenshots", "ATTRIBUTION.md"), "w", encoding="utf-8") as f:
    f.write("# Demo library attribution\n\nThe screenshots and animations in the README show a demo library built with "
            "`bench/demo/build_travel_demo.py`.\nEvery photo is **CC0 1.0** (public domain dedication), from Flickr, StockSnap "
            "or Wikimedia Commons, found through [Openverse](https://openverse.org) and Commons.\nDates, GPS positions and the "
            "grouping into trips are made up and written into the demo copies. The group photos are stock photos that were "
            "not taken at those places, and the people in them are not named. The short videos were assembled from these "
            "photos.\nNo credit is legally required for CC0 works; the authors are listed with thanks.\n\n"
            "| Demo file | Source | Author |\n|---|---|---|\n")
    for i in done:
        f.write(f"| `{i['file']}` | [{cell(i['title'])}]({i['source']}) | {cell(i['author'])} |\n")
print(len(done), "of", len(ITEMS), "photos")
