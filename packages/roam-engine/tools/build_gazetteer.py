"""Build the offline place-name index used by "Add places" search from GeoNames (CC BY 4.0, geonames.org).

Inputs (from https://download.geonames.org/export/dump/): cities1000.txt (every place with 1,000+ people),
admin1CodesASCII.txt, countryInfo.txt. Output: src/roamengine/data/gazetteer.tsv.gz with columns
name, ascii name, alternate names (|-separated, Latin script, larger places only), lat, lon, country, region, population.

    python tools/build_gazetteer.py <dir with the three files>
"""
import gzip
import os
import re
import sys

src = sys.argv[1]
out = os.path.join(os.path.dirname(__file__), "..", "src", "roamengine", "data", "gazetteer.tsv.gz")
_latin = re.compile(r"^[^\W\d_][^\W\d_ .'\-]*(?:[ .'\-]+[^\W\d_]+)*\.?$")


class latin:                                       # Latin script (incl. accents) names only, 2..40 characters
    @staticmethod
    def match(a):
        return 2 <= len(a) <= 40 and all(ord(c) < 0x250 for c in a) and _latin.match(a)

countries = {}
for line in open(os.path.join(src, "countryInfo.txt"), encoding="utf-8"):
    if line.startswith("#"):
        continue
    f = line.rstrip("\n").split("\t")
    countries[f[0]] = f[4]
admin1 = {}
for line in open(os.path.join(src, "admin1CodesASCII.txt"), encoding="utf-8"):
    f = line.rstrip("\n").split("\t")
    admin1[f[0]] = f[1]

clean = lambda s: s.replace("\t", " ").replace("|", " ").strip()  # noqa: E731
n = 0
with gzip.open(out, "wt", encoding="utf-8", compresslevel=9) as w:
    w.write("# GeoNames cities1000 (CC BY 4.0, https://www.geonames.org). Columns: name ascii alts lat lon country "
            "region population\n")
    for line in open(os.path.join(src, "cities1000.txt"), encoding="utf-8"):
        f = line.rstrip("\n").split("\t")
        name, ascii_, alts, lat, lon, cc, a1, pop = f[1], f[2], f[3], f[4], f[5], f[8], f[10], int(f[14] or 0)
        keep = []
        if pop >= 50000:
            seen = {name.lower(), ascii_.lower()}
            for a in alts.split(","):
                if latin.match(a) and a.lower() not in seen:
                    seen.add(a.lower()); keep.append(clean(a))
                if len(keep) >= (80 if pop >= 500000 else 12):
                    break
        w.write("\t".join([clean(name), clean(ascii_), "|".join(keep), f"{float(lat):.5f}", f"{float(lon):.5f}",
                           countries.get(cc, cc), clean(admin1.get(f"{cc}.{a1}", "")), str(pop)]) + "\n")
        n += 1
print(n, "places ->", os.path.abspath(out), os.path.getsize(out), "bytes")
