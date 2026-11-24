"""Place-name search for "Add places".

Offline (the default): the bundled GeoNames gazetteer (data/gazetteer.tsv.gz, every place with 1,000+ people,
CC BY 4.0 geonames.org; rebuilt by tools/build_gazetteer.py). Nothing leaves the machine.

Online (only when the user asks for it, per search): OpenStreetMap Nominatim. Finds streets, landmarks and small
villages the gazetteer lacks, but the typed text is sent to nominatim.openstreetmap.org. Its usage policy allows at
most one request per second and no search-as-you-type, so this runs only on an explicit search, rate-limited here.
"""
import gzip
import json
import os
import threading
import time
import unicodedata
import urllib.parse
import urllib.request

DATA = os.path.join(os.path.dirname(__file__), "data", "gazetteer.tsv.gz")
NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "RoamIndex/0.1 (local photo organiser)"


def norm(s):
    s = unicodedata.normalize("NFKD", s.casefold())
    return "".join(ch for ch in s if not unicodedata.combining(ch)).strip()


class Gazetteer:
    def __init__(self, path=DATA):
        self.path = path
        self._lock = threading.Lock()
        self.rows = None

    def _load(self):
        with self._lock:
            if self.rows is not None:
                return
            rows, keys, alt_keys = [], [], []
            with gzip.open(self.path, "rt", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("#"):
                        continue
                    name, ascii_, alts, lat, lon, country, region, pop = line.rstrip("\n").split("\t")
                    rows.append((name, float(lat), float(lon), country, region, int(pop),
                                 norm(f"{region} {country}")))
                    ks = {norm(name), norm(ascii_)}
                    keys.append("\x00" + "\x00".join(ks) + "\x00")
                    alt_keys.append("\x00" + "\x00".join({norm(a) for a in alts.split("|") if a} - ks) + "\x00")
            # one string per place: "\0key1\0key2\0". A prefix test is then a C-level substring search.
            self.keys, self.alts, self.rows = keys, alt_keys, rows

    def search(self, q, n=8):
        self._load()
        parts = [norm(p) for p in q.split(",")]
        head, ctx = parts[0], [p for p in parts[1:] if p]
        if len(head) < 2:
            return []
        exact, pre, word = "\x00" + head + "\x00", "\x00" + head, " " + head
        hits = []
        for i, k in enumerate(self.keys):
            a = self.alts[i]
            # the place's own name beats a former or foreign one ("Galle" is the city Galle before Halle, whose
            # alternate names include Galle); exact beats prefix beats a later word
            if exact in k:
                score = 5
            elif exact in a:
                score = 4
            elif pre in k:
                score = 3
            elif pre in a:
                score = 2
            elif word in k or word in a:
                score = 1
            else:
                continue
            r = self.rows[i]
            if ctx and not all(c in r[6] for c in ctx):
                continue
            hits.append((score, r[5], i))
        hits.sort(key=lambda h: (-h[0], -h[1]))
        out = []
        for _, _, i in hits[:n]:
            name, lat, lon, country, region, pop, _ = self.rows[i]
            label = ", ".join(x for x in (name, region if region != name else "", country) if x)
            out.append(dict(name=name, label=label, lat=lat, lon=lon, population=pop, source="geonames"))
        return out


_last_online = 0.0
_online_lock = threading.Lock()


def search_online(q, n=8, timeout=8):
    """OpenStreetMap Nominatim. Sends `q` to nominatim.openstreetmap.org: only on an explicit user request."""
    global _last_online
    q = q.strip()[:200]
    if len(q) < 2:
        return []
    with _online_lock:                                  # usage policy: at most 1 request per second
        wait = 1.05 - (time.monotonic() - _last_online)
        if wait > 0:
            time.sleep(wait)
        _last_online = time.monotonic()
        url = NOMINATIM + "?" + urllib.parse.urlencode(dict(q=q, format="jsonv2", limit=n))
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except OSError as e:
            raise RuntimeError(f"online search failed: {e}") from e
    return [dict(name=d.get("name") or d.get("display_name", "").split(",")[0], label=d.get("display_name", ""),
                 lat=round(float(d["lat"]), 6), lon=round(float(d["lon"]), 6), population=None, source="osm")
            for d in data if "lat" in d and "lon" in d]
