"""Trips and "your world" stats for the home screen, from capture times and GPS alone (offline).

Home is the 1-degree cell where photos were taken on the most distinct days. A trip is a run of located photos taken
more than AWAY_KM from home, with no gap longer than MAX_GAP_DAYS and no photo back home in between. Its name is the
most common nearest town (GeoNames, bundled), or the country when no town dominates. Undated or unlocated items inside
the trip's time span count towards it.
"""
import collections
import datetime as dt
import math

import numpy as np

AWAY_KM = 80.0
MAX_GAP_DAYS = 3.0
MIN_ITEMS = 6
MIN_TRIP_DAYS = 3      # a day or two away is a day out, not a trip
MAX_TRIPS = 12


def _km(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dl = np.radians(lon2 - lon1)
    c = np.sin(p1) * np.sin(p2) + np.cos(p1) * np.cos(p2) * np.cos(dl)
    return np.degrees(np.arccos(np.clip(c, -1, 1))) * 111.195


def _day(sec):
    return dt.datetime.fromtimestamp(float(sec), dt.timezone.utc).strftime("%Y-%m-%d")


def _home(t, lat, lon):
    """Centre of the 1-degree cell with the most distinct photo days, or None."""
    days = collections.defaultdict(set)
    for a, o, s in zip(np.floor(lat).astype(int), np.floor(lon).astype(int), t):
        days[(a, o)].add(int(s // 86400) if math.isfinite(s) else None)
    if not days:
        return None
    cell = max(days, key=lambda k: len(days[k] - {None}))
    m = (np.floor(lat) == cell[0]) & (np.floor(lon) == cell[1])
    return float(np.median(lat[m])), float(np.median(lon[m]))


def _first_covers(cat, idx, k):
    """Fallback cover choice: photos with a thumbnail, evenly spread over the group."""
    thumbs = [i for i in idx if cat.thumb[i]]
    photos = [i for i in thumbs if not cat.is_video[i]] or thumbs
    if not photos:
        return []
    return list(dict.fromkeys(photos[int(j)] for j in np.linspace(0, len(photos) - 1, min(k, len(photos)))))


def structure(cat, gazetteer):
    """Trips, towns and "your world" stats without any cover photos (those are chosen separately and can improve
    later as the face model looks at more photos). Private fields start with an underscore:
    trips[i]["_members"] asset indices, trips[i]["_town"] the trip's main (town, country);
    towns {(town, country): asset indices}; town_of per asset index (-1 = none) into town_keys."""
    n = len(cat.asset_ids)
    located = np.isfinite(cat.lat) & np.isfinite(cat.lon)
    dated = np.isfinite(cat.t)
    loc_idx = np.flatnonzero(located)
    places = gazetteer.reverse(cat.lat[located], cat.lon[located]) if located.any() else []
    countries = {p[1] for p in places if p}
    town_set = {p for p in places if p}
    world = dict(
        photos=int((~cat.is_video).sum()), videos=int(cat.is_video.sum()),
        video_hours=round(float(np.nansum(cat.duration[cat.is_video])) / 3600, 1) if n else 0.0,
        years=sorted({int(y) for y in (np.array([dt.datetime.fromtimestamp(float(s), dt.timezone.utc).year
                                                for s in cat.t[dated]]) if dated.any() else [])}),
        countries=len(countries), places=len(town_set), located=int(located.sum()),
    )
    pin = {}
    for i, p in zip(loc_idx, places):
        if p and p not in pin:
            pin[p] = (round(float(cat.lat[i]), 3), round(float(cat.lon[i]), 3))
    world["pins"] = [dict(lat=a, lon=o) for a, o in pin.values()][:2000]

    towns = collections.defaultdict(list)
    for i, p in zip(loc_idx, places):
        if p:
            towns[p].append(i)
    towns = {k: np.array(v) for k, v in towns.items()}
    town_keys = list(towns)
    town_of = np.full(n, -1, np.int32)
    for j, k in enumerate(town_keys):
        town_of[towns[k]] = j

    trips = []
    both = located & dated
    if both.sum() >= MIN_ITEMS:
        home = _home(cat.t[both], cat.lat[both], cat.lon[both])
        idx = np.flatnonzero(both)
        idx = idx[np.argsort(cat.t[idx])]
        away = _km(home[0], home[1], cat.lat[idx], cat.lon[idx]) > AWAY_KM
        place_of = dict(zip(loc_idx, places))
        runs, cur = [], []
        for i, a in zip(idx, away):
            if not a:
                if cur:
                    runs.append(cur)
                cur = []
                continue
            if cur and cat.t[i] - cat.t[cur[-1]] > MAX_GAP_DAYS * 86400:
                runs.append(cur)
                cur = []
            cur.append(i)
        if cur:
            runs.append(cur)
        for run in runs:
            t0, t1 = float(cat.t[run[0]]), float(cat.t[run[-1]])
            if int((t1 - t0) // 86400) + 1 < MIN_TRIP_DAYS:
                continue
            span = dated & (cat.t >= t0) & (cat.t <= t1)
            members = np.flatnonzero(span)
            if len(members) < MIN_ITEMS:
                continue
            names = collections.Counter(place_of[i] for i in run if place_of.get(i))
            ctry = collections.Counter(p[1] for p in names.elements())
            if not names:
                title, country, main = "A trip", None, None
            else:
                main, k = names.most_common(1)[0]
                country = ctry.most_common(1)[0][0]
                title = main[0] if k >= 0.5 * sum(names.values()) or len(ctry) > 1 else country
            mid = run[len(run) // 2]
            trips.append(dict(
                title=title, country=country, start=_day(t0), end=_day(t1),
                days=int((t1 - t0) // 86400) + 1, count=int(len(members)),
                lat=round(float(cat.lat[mid]), 3), lon=round(float(cat.lon[mid]), 3),
                _members=members, _town=main))
        trips.sort(key=lambda r: r["start"], reverse=True)
        trips = trips[:MAX_TRIPS]
    world["trips"] = len(trips)
    return dict(trips=trips, world=world, towns=towns, town_keys=town_keys, town_of=town_of)


def public(st, cat, trip_covers, town_covers):
    """The JSON for /api/journeys: trips with cover ids, world with the places panel (countries -> towns)."""
    by_country = collections.defaultdict(list)
    for key, ids in st["towns"].items():
        best = town_covers.get(key)
        by_country[key[1]].append(dict(name=key[0], count=int(len(ids)),
                                       lat=round(float(np.median(cat.lat[ids])), 4),
                                       lon=round(float(np.median(cat.lon[ids])), 4),
                                       cover=str(cat.asset_ids[best]) if best is not None else None))
    countries_list = []
    for country, towns in by_country.items():
        towns.sort(key=lambda r: -r["count"])
        countries_list.append(dict(name=country, count=sum(r["count"] for r in towns), towns=towns[:60]))
    countries_list.sort(key=lambda r: -r["count"])
    world = dict(st["world"], countries_list=countries_list[:120])
    trips = [dict({k: v for k, v in t.items() if not k.startswith("_")},
                  cover=[str(cat.asset_ids[i]) for i in trip_covers[j]]) for j, t in enumerate(st["trips"])]
    return dict(trips=trips, world=world)


def compute(cat, gazetteer, pick=None, trip_pick=None, town_pick=None):
    """structure() plus covers from simple callbacks (tests and scripts; the server fills covers itself).
    pick(asset_indices, k) -> chosen indices; trip_pick(indices) -> up to 3; town_pick(indices) -> [one]."""
    pick = pick or (lambda idx, k: _first_covers(cat, idx, k))
    trip_pick = trip_pick or (lambda idx: pick(idx, 3))
    town_pick = town_pick or (lambda idx: pick(idx, 1))
    st = structure(cat, gazetteer)
    town_covers = {k: (town_pick(ids) or [None])[0] for k, ids in st["towns"].items()}
    return public(st, cat, [trip_pick(t["_members"]) for t in st["trips"]], town_covers)
