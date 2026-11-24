"""Trip detection and world stats on a synthetic catalogue (public city coordinates)."""
from types import SimpleNamespace

import numpy as np

from roamengine import journeys
from roamengine.geocode import Gazetteer

DAY = 86400.0
T0 = 1_700_000_000.0


def fake_cat(rows):
    """rows: (t, lat, lon, is_video)"""
    t, la, lo, v = (np.array(x, dtype=float) for x in zip(*rows))
    n = len(rows)
    return SimpleNamespace(asset_ids=np.array([f"{i:032x}" for i in range(n)], dtype=object), t=t, lat=la, lon=lo,
                           is_video=v.astype(bool), duration=np.where(v.astype(bool), 30.0, np.nan),
                           thumb=["thumbnails/x.jpg"] * n)


def test_home_is_not_a_trip_and_a_trip_is_found():
    home = [(T0 + d * DAY * 7, 52.52, 13.40, 0) for d in range(30)]                    # Berlin, weekly for 30 weeks
    trip = [(T0 + 42.5 * DAY + h * 3600 * 9, 38.72 + 0.01 * h, -9.14, h == 3) for h in range(12)]   # Lisbon, ~4.5 days
    undated = [(np.nan, np.nan, np.nan, 0)] * 3
    r = journeys.compute(fake_cat(home + trip + undated), Gazetteer())
    assert len(r["trips"]) == 1
    t = r["trips"][0]
    assert (t["title"], t["country"]) == ("Lisbon", "Portugal")
    assert t["count"] == 12 and t["days"] == 5 and 1 <= len(t["cover"]) <= 3
    w = r["world"]
    assert w["countries"] == 2 and w["videos"] == 1 and w["photos"] == 44 and w["trips"] == 1
    assert len(w["pins"]) == 2


def test_small_or_empty_library():
    assert journeys.compute(fake_cat([(T0, 52.52, 13.40, 0)]), Gazetteer())["trips"] == []
