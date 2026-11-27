"""On this day, Year in review and Relive on a synthetic catalogue (public city coordinates)."""
import datetime as dt
from types import SimpleNamespace

import numpy as np

from roamengine import journeys, nostalgia
from roamengine.geocode import Gazetteer

DAY = 86400.0


def ts(y, m, d, h=12):
    return dt.datetime(y, m, d, h, tzinfo=dt.timezone.utc).timestamp()


def fake_engine(rows):
    """rows: (t, lat, lon)"""
    t, la, lo = (np.array(x, dtype=float) for x in zip(*rows))
    n = len(rows)
    cat = SimpleNamespace(asset_ids=np.array([f"{i:032x}" for i in range(n)], dtype=object), t=t, lat=la, lon=lo,
                          is_video=np.zeros(n, bool), duration=np.full(n, np.nan), thumb=["x"] * n)
    cat.summary = lambda i: dict(asset_id=cat.asset_ids[i], capture_time=None, lat=None if np.isnan(la[i]) else la[i],
                                 lon=None if np.isnan(lo[i]) else lo[i], media_type="photo")
    pick = lambda idx, k=3, **kw: [int(i) for i in list(idx)[:k]]  # noqa: E731
    return SimpleNamespace(cat=cat, pick_covers=pick, memory_covers=pick)


# Berlin weekly through 2022-2023 (home), a Lisbon trip in June 2023, and three 27 Nov photos in 2021 (Berlin)
HOME = [(ts(2022, 1, 3) + w * 7 * DAY, 52.52, 13.40) for w in range(100)]
TRIP = [(ts(2023, 6, 13) + h * 9 * 3600, 38.72 + 0.01 * h, -9.14) for h in range(12)]   # Tue-Sat: no home photo
OTD = [(ts(2021, 11, 27, h), 52.52, 13.40) for h in (9, 12, 15)]
UNDATED = [(np.nan, np.nan, np.nan)]


def test_on_this_day_exact_then_week():
    e = fake_engine(HOME + TRIP + OTD + UNDATED)
    r = nostalgia.on_this_day(e, today=dt.date(2026, 11, 27))
    assert r["window"] == 0 and [c["year"] for c in r["cards"]] == [2021]
    c = r["cards"][0]
    assert c["count"] == 3 and c["years_ago"] == 5 and c["from"] == c["to"] == "2021-11-27" and len(c["cover"]) == 3
    r = nostalgia.on_this_day(e, today=dt.date(2026, 11, 29))       # nothing on 29 Nov: this week (± 3 days)
    assert r["window"] == 3 and r["cards"][0]["from"] == "2021-11-26"
    assert nostalgia.on_this_day(e, today=dt.date(2026, 3, 1))["cards"] == []


def test_years_and_review():
    e = fake_engine(HOME + TRIP + OTD + UNDATED)
    assert [y["year"] for y in nostalgia.years(e.cat)] == [2023, 2022, 2021]
    st = journeys.structure(e.cat, Gazetteer())
    pub = [{k: v for k, v in t.items() if not k.startswith("_")} for t in st["trips"]]
    r = nostalgia.year_review(e, st, pub, {}, 2023)
    assert r["photos"] == sum(1 for t, *_ in HOME + TRIP if dt.datetime.fromtimestamp(t, dt.timezone.utc).year == 2023)
    assert sum(r["months"]) == r["photos"] and r["months"][5] >= 12
    assert [t["title"] for t in r["trips"]] == ["Lisbon"]
    places = {p["name"]: p for p in r["places"]}
    assert places["Lisbon"]["new"] and not places["Berlin"]["new"]
    assert r["new_countries"] == ["Portugal"] and set(r["countries"]) == {"Germany", "Portugal"}
    assert nostalgia.year_review(e, st, pub, {}, 1999) is None


def test_relive_slides_in_order_with_days_and_route():
    e = fake_engine(HOME + TRIP)
    st = journeys.structure(e.cat, Gazetteer())
    start = st["trips"][0]["start"]
    r = nostalgia.relive(e, st, start)
    t = [e.cat.t[int(s["asset_id"], 16)] for s in r["slides"]]
    assert t == sorted(t) and r["slides"][0]["day"] == 1 and r["slides"][-1]["day"] == r["days"]
    assert {s["place"] for s in r["slides"]} == {"Lisbon"} and len(r["route"]) == 12
    assert nostalgia.relive(e, st, "1999-01-01") is None
