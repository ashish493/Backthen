"""Nostalgia round 2 (BOTTLENECKS.md #17): On this day, Year in review, Relive a trip.

Everything is computed from the catalogue (times as the catalogue sorts them: UTC, or the camera clock treated as
UTC) and the home structure (trips, towns). Cover choice goes through the engine, like the memory cards."""
import collections
import datetime as dt

import numpy as np

OTD_MIN = 3                 # photos on a day before it gets an On this day card
OTD_WIDEN_DAYS = 3          # nothing on the exact day: try this week (± days)
RELIVE_MAX = 80             # slides in a Relive show
ROUTE_MAX = 400             # points on the Relive route


def _dates(cat, idx):
    return cat.t[idx].astype("datetime64[s]").astype("datetime64[D]")


def _day_str(sec):
    return dt.datetime.fromtimestamp(float(sec), dt.timezone.utc).strftime("%Y-%m-%d")


def on_this_day(e, today=None):
    """[{year, years_ago, from, to, count, cover}] newest first: this date in earlier years, or this week when no
    year has OTD_MIN items on the exact day (then `window` = days either side)."""
    cat, today = e.cat, today or dt.date.today()
    idx = np.flatnonzero(np.isfinite(cat.t))
    if not len(idx):
        return dict(window=0, cards=[])
    d = _dates(cat, idx)
    years = d.astype("datetime64[Y]").astype(int) + 1970
    for window in (0, OTD_WIDEN_DAYS):
        cards = []
        for y in sorted({int(v) for v in years if v < today.year}, reverse=True):
            try:
                mid = dt.date(y, today.month, today.day)
            except ValueError:                           # 29 Feb in a non-leap year
                mid = dt.date(y, today.month, 28)
            lo, hi = mid - dt.timedelta(days=window), mid + dt.timedelta(days=window)
            m = idx[(d >= np.datetime64(lo)) & (d <= np.datetime64(hi))]
            if len(m) >= OTD_MIN:
                cards.append(dict(year=y, years_ago=today.year - y, count=int(len(m)), _members=m,
                                  **{"from": lo.isoformat()}, to=hi.isoformat()))
        if cards:
            for c in cards:
                c["cover"] = [str(cat.asset_ids[i]) for i in e.memory_covers(c.pop("_members"), 3)]
            return dict(window=window, cards=cards)
    return dict(window=0, cards=[])


def years(cat):
    """[{year, count}] newest first."""
    idx = np.flatnonzero(np.isfinite(cat.t))
    if not len(idx):
        return []
    y = _dates(cat, idx).astype("datetime64[Y]").astype(int) + 1970
    return [dict(year=int(k), count=int(v)) for k, v in sorted(collections.Counter(y.tolist()).items(), reverse=True)]


def year_review(e, st, public_trips, town_cover_ids, year):
    """One year: totals, items per month, busiest day, trips, top places (and how many are new), best photos."""
    cat = e.cat
    idx = np.flatnonzero(np.isfinite(cat.t))
    d = _dates(cat, idx)
    y = d.astype("datetime64[Y]").astype(int) + 1970
    m = idx[y == year]
    if not len(m):
        return None
    dm = _dates(cat, m)
    months = np.bincount(dm.astype("datetime64[M]").astype(int) % 12, minlength=12)
    days = collections.Counter(dm.astype(str).tolist())
    busiest, busiest_n = days.most_common(1)[0]

    town_of, keys = st["town_of"], st["town_keys"]
    first_year = {}                                      # (town, country) -> first year seen
    for j, yy in zip(town_of[idx].tolist(), y.tolist()):
        if j >= 0 and (j not in first_year or yy < first_year[j]):
            first_year[j] = yy
    here = collections.Counter(int(j) for j in town_of[m] if j >= 0)
    countries = {keys[j][1] for j in here}
    new_countries = countries - {keys[j][1] for j, fy in first_year.items() if fy < year}
    places = [dict(name=keys[j][0], country=keys[j][1], count=n, new=bool(first_year[j] == year),
                   cover=town_cover_ids.get(keys[j])) for j, n in here.most_common(8)]
    return dict(
        year=year, photos=int((~cat.is_video[m]).sum()), videos=int(cat.is_video[m].sum()),
        days=len(days), months=months.astype(int).tolist(), busiest=dict(day=busiest, count=busiest_n),
        trips=[t for t in public_trips if t["start"][:4] == str(year)],
        places=places, n_places=len(here), new_places=sum(1 for j in here if first_year[j] == year),
        countries=sorted(countries), new_countries=sorted(new_countries),
        best=[str(cat.asset_ids[i]) for i in e.pick_covers(m, 12)])


def relive(e, st, start):
    """The trip starting on `start` (YYYY-MM-DD) as a slideshow: the best items of each day in time order, each with
    day number and place, and the route of its located items. None if there is no such trip."""
    cat = e.cat
    trip = next((t for t in st["trips"] if t["start"] == start), None)
    if trip is None:
        return None
    members = np.asarray(trip["_members"], dtype=np.int64)
    members = members[np.argsort(cat.t[members], kind="stable")]
    day0 = int(cat.t[members[0]] // 86400)
    dayn = (cat.t[members] // 86400).astype(np.int64) - day0
    picks = []
    for k in np.unique(dayn):
        on = members[dayn == k]
        quota = max(2, round(RELIVE_MAX * len(on) / len(members)))
        picks += e.pick_covers(on, min(quota, len(on)))
    picks = sorted(set(picks), key=lambda i: cat.t[i])
    if len(picks) > RELIVE_MAX:                          # keep the order, thin evenly
        picks = [picks[int(j)] for j in np.linspace(0, len(picks) - 1, RELIVE_MAX)]
    town_of, keys = st["town_of"], st["town_keys"]
    slides = []
    for i in picks:
        s = cat.summary(i)
        j = int(town_of[i])
        s.update(day=int(cat.t[i] // 86400) - day0 + 1, place=keys[j][0] if j >= 0 else None)
        slides.append(s)
    loc = members[np.isfinite(cat.lat[members])]
    if len(loc) > ROUTE_MAX:
        loc = loc[np.linspace(0, len(loc) - 1, ROUTE_MAX).astype(int)]
    route = [[round(float(cat.lon[i]), 5), round(float(cat.lat[i]), 5)] for i in loc]
    return dict(title=trip["title"], country=trip["country"], start=trip["start"], end=trip["end"],
                days=trip["days"], count=trip["count"], slides=slides, route=route)
