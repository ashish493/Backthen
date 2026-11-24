"""Build a public demo library for screenshots: CC0 photos and videos from Wikimedia Commons, grouped into fictional
trips with made-up dates and GPS written into the demo copies. No personal media is involved.

usage: python bench/demo/fetch_demo.py OUT_DIR        -> OUT_DIR/media/<trip>/..., OUT_DIR/ATTRIBUTION.json
       DEMO_COMMONS_VIDEOS=1 also fetches CC0 videos (Commons often rate-limits these; see make_clips.py)
"""
import io, json, os, random, re, sys, time, urllib.parse, urllib.request

from PIL import Image

API = "https://commons.wikimedia.org/w/api.php"
UA = "RoamIndex-demo/0.1 (open-source photo search demo)"
OUT = sys.argv[1]
rng = random.Random(42)

# trip, start date, days, [(query, n, (lat, lon) or None)]
TRIPS = [
    ("iceland-2022", (2022, 8, 3), 6, [("waterfall iceland", 6, (63.6156, -19.9886)), ("glacier lagoon", 4, (64.0784, -16.2306)),
                                       ("black sand beach", 3, (63.4040, -19.0440)), ("icelandic horse", 3, (64.2559, -21.1295))]),
    ("alps-2023", (2023, 7, 14), 5, [("alpine lake", 5, (46.0207, 7.7491)), ("mountain hiking trail", 5, (46.0300, 7.7200)),
                                     ("cows alpine meadow", 3, (46.6863, 7.8632)), ("matterhorn", 3, (45.9763, 7.6586))]),
    ("lisbon-2024", (2024, 4, 20), 4, [("lisbon tram", 4, (38.7139, -9.1334)), ("azulejo tiles", 3, (38.7110, -9.1390)),
                                       ("seafood dish", 3, (38.7070, -9.1450)), ("city street night", 4, (38.7100, -9.1420))]),
    ("kyoto-2024", (2024, 11, 18), 5, [("kyoto temple", 5, (34.9671, 135.7727)), ("autumn maple leaves", 4, (35.0156, 135.6772)),
                                       ("bamboo forest", 3, (35.0170, 135.6713)), ("ramen", 3, (35.0037, 135.7681)),
                                       ("torii gate", 3, (34.9671, 135.7727))]),
    ("home-2025", (2025, 3, 1), 120, [("dog", 5, None), ("cat", 5, None), ("birthday cake", 3, None),
                                      ("garden flowers", 4, None), ("pizza", 3, None)]),
]
VIDEOS = [("waterfall", "iceland-2022", (63.6156, -19.9886)), ("ocean waves beach", "iceland-2022", (63.4040, -19.0440)),
          ("mountain", "alps-2023", (46.0207, 7.7491)), ("tram", "lisbon-2024", (38.7139, -9.1334)),
          ("cat", "home-2025", None), ("fireworks", "home-2025", None)]


def get(params):
    q = urllib.parse.urlencode(dict(params, format="json"))
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(API + "?" + q, headers={"User-Agent": UA}), timeout=30) as r:
                return json.loads(r.read())
        except OSError as e:
            print("  api retry:", e, flush=True)
            time.sleep(5 + attempt * 10)
    raise RuntimeError("Commons API unreachable")


def fetch(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=120) as r:
                return r.read()
        except OSError as e:
            print("  download retry:", e, flush=True)
            time.sleep(5 + attempt * 10)
    raise RuntimeError("download failed")


def search(query, kind, limit=40):
    d = get(dict(action="query", generator="search", gsrnamespace=6, gsrlimit=limit,
                 gsrsearch=f"{query} filetype:{kind} incategory:CC-zero", prop="imageinfo",
                 iiprop="url|size|mime|extmetadata", iiextmetadatafilter="LicenseShortName|Artist|ObjectName",
                 iiurlwidth=1280))
    pages = sorted(d.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    out = []
    for p in pages:
        ii = p["imageinfo"][0]
        m = ii.get("extmetadata", {})
        if m.get("LicenseShortName", {}).get("value") != "CC0":
            continue
        out.append(dict(title=p["title"], page=ii["descriptionurl"], url=ii["url"], thumb=ii.get("thumburl"),
                        mime=ii["mime"], w=ii.get("width", 0), h=ii.get("height", 0), size=ii.get("size", 0),
                        duration=ii.get("duration"),
                        artist=re.sub(r"<[^>]+>", "", m.get("Artist", {}).get("value", "")).strip()[:120]))
    return out


def dms(v):
    v = abs(v); d = int(v); m = int((v - d) * 60); s = round(((v - d) * 60 - m) * 60, 4)
    return (float(d), float(m), float(s))


def save_photo(data, path, when, gps):
    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((1600, 1600))
    ex = Image.Exif()
    ex[0x0132] = when.replace("-", ":")
    sub = ex.get_ifd(0x8769)
    sub[36867] = when.replace("-", ":")          # DateTimeOriginal
    sub[36881] = "+00:00"                         # OffsetTimeOriginal (demo times are UTC)
    if gps:
        g = ex.get_ifd(0x8825)
        g[1], g[2], g[3], g[4] = ("N" if gps[0] >= 0 else "S"), dms(gps[0]), ("E" if gps[1] >= 0 else "W"), dms(gps[1])
    im.save(path, "JPEG", quality=90, exif=ex)


def jitter(p, km=3.0):
    return None if p is None else (p[0] + rng.uniform(-km, km) / 111, p[1] + rng.uniform(-km, km) / 111)


def when_in(trip):
    (y, mo, d), days = trip[1], trip[2]
    import datetime as dt
    t = dt.datetime(y, mo, d, 8) + dt.timedelta(days=rng.randrange(days), minutes=rng.randrange(13 * 60))
    return t.strftime("%Y-%m-%d %H:%M:%S")


from concurrent.futures import ThreadPoolExecutor  # noqa: E402

attr, seen, jobs = [], set(), []
for trip in TRIPS:
    os.makedirs(os.path.join(OUT, "media", trip[0]), exist_ok=True)
    for query, n, loc in trip[3]:
        got = 0
        for c in search(query, "bitmap"):
            if got >= n:
                break
            if c["title"] in seen or c["mime"] != "image/jpeg" or min(c["w"], c["h"]) < 900 or not c["thumb"]:
                continue
            seen.add(c["title"])
            name = f"{re.sub(r'[^a-z0-9]+', '-', query)}-{got + 1}.jpg"
            # home photos: a third keep a location, the rest are left for the "Add places" demo
            gps = jitter(loc) if loc else ((52.5200 + rng.uniform(-.05, .05), 13.4050 + rng.uniform(-.05, .05)) if got % 3 == 0 else None)
            jobs.append((c["thumb"], os.path.join(OUT, "media", trip[0], name), when_in(trip), gps))
            attr.append(dict(file=f"{trip[0]}/{name}", source=c["page"], title=c["title"], artist=c["artist"], license="CC0 1.0"))
            got += 1
        print(trip[0], query, got, flush=True)


def job(j):
    if not os.path.exists(j[1]):
        try:
            save_photo(fetch(j[0]), *j[1:])
        except RuntimeError as e:                  # an image Commons will not serve at this size: leave it out
            print("  skip photo:", e, flush=True)


with ThreadPoolExecutor(4) as ex:
    for i, _ in enumerate(ex.map(job, jobs)):
        if i % 20 == 0:
            print("photos", i, "/", len(jobs), flush=True)

attr = [a for a in attr if os.path.exists(os.path.join(OUT, "media", a["file"]))]
json.dump(attr, open(os.path.join(OUT, "ATTRIBUTION.json"), "w"), indent=1)
print("photos:", len(attr))
if os.environ.get("DEMO_COMMONS_VIDEOS") != "1":     # Commons rate-limits original media; make_clips.py is the default
    sys.exit(0)

import av  # noqa: E402  (PyAV, already a pipeline dependency)

os.makedirs(os.path.join(OUT, "raw_video"), exist_ok=True)
for query, tripname, loc in VIDEOS:
    trip = next(t for t in TRIPS if t[0] == tripname)
    for c in search(query, "video", 30):
        if c["title"] in seen or c["size"] > 60e6 or (c["duration"] or 0) > 120 or (c["duration"] or 0) < 4:
            continue
        seen.add(c["title"])
        src = os.path.join(OUT, "raw_video", "src" + os.path.splitext(c["url"])[1])
        open(src, "wb").write(fetch(c["url"]))
        name = f"{re.sub(r'[^a-z0-9]+', '-', query)}.mp4"
        dst = os.path.join(OUT, "media", tripname, name)
        try:
            with av.open(src) as inp:
                vs = inp.streams.video[0]
                out = av.open(dst, "w", options={"movflags": "+faststart"})
                w = vs.codec_context.width; h = vs.codec_context.height
                s = min(1.0, 720 / min(w, h))
                ow, oh = int(w * s) // 2 * 2, int(h * s) // 2 * 2
                o = out.add_stream("libx264", rate=30)
                o.width, o.height, o.pix_fmt = ow, oh, "yuv420p"
                o.options = {"crf": "26", "preset": "veryfast"}
                ts = when_in(trip)
                out.metadata["creation_time"] = ts.replace(" ", "T") + ".000000Z"
                g = jitter(loc)
                if g:
                    out.metadata["location"] = f"{g[0]:+08.4f}{g[1]:+09.4f}/"
                n = 0
                for f in inp.decode(vs):
                    if f.time is not None and f.time > 30:
                        break
                    for p in o.encode(f.reformat(ow, oh, "yuv420p")):
                        out.mux(p)
                    n += 1
                for p in o.encode():
                    out.mux(p)
                out.close()
        except Exception as e:                    # noqa: BLE001  unusable source: try the next result
            print("  skip video:", e)
            if os.path.exists(dst):
                os.remove(dst)
            continue
        finally:
            os.remove(src)
        attr.append(dict(file=f"{tripname}/{name}", source=c["page"], title=c["title"], artist=c["artist"],
                         license="CC0 1.0", note="first 30 s, re-encoded to H.264"))
        print("video", query, n, "frames", flush=True)
        break
os.rmdir(os.path.join(OUT, "raw_video"))
attr = [a for a in attr if os.path.exists(os.path.join(OUT, "media", a["file"]))]
json.dump(attr, open(os.path.join(OUT, "ATTRIBUTION.json"), "w"), indent=1)
print("photos+videos:", len(attr))
