"""Memory cards for the home screen, newest trips first, then this month in earlier years, then the busiest months.

Structure only: which photos belong to which card. Covers are chosen by the engine, first from CLIPScores alone
(instant) and then refined as the face model looks at each card's best candidates -- the first FIRST cards first.
Private fields start with an underscore and never reach the API.
"""
import datetime as dt

import numpy as np

MAX_CARDS = 12
FIRST = 4            # cards refined before anything else


def months(cat):
    """{'YYYY-MM': asset indices} for dated assets (UTC or camera-local time, as the catalogue sorts them)."""
    idx = np.flatnonzero(np.isfinite(cat.t))
    if not len(idx):
        return {}
    m = cat.t[idx].astype("datetime64[s]").astype("datetime64[M]")
    out = {}
    order = np.argsort(m, kind="stable")
    keys, starts = np.unique(m[order], return_index=True)
    bounds = list(starts) + [len(order)]
    for j, k in enumerate(keys):
        out[str(k)] = idx[order[bounds[j]:bounds[j + 1]]]
    return out


def cards(cat, trips, today=None):
    """Cards in display order. trips: journeys.structure()["trips"] (newest first, with _members)."""
    today = today or dt.date.today()
    out = []
    trip_months = {t["start"][:7] for t in trips}
    for t in trips:
        out.append(dict(key=f"t{t['start']}", kind="trip", title=t["title"], country=t["country"],
                        **{"from": t["start"]}, to=t["end"], days=t["days"], count=t["count"],
                        _members=t["_members"], _town=t["_town"]))
    by_month = months(cat)
    ago = []
    for m in sorted(by_month, reverse=True):
        y, mo = int(m[:4]), int(m[5:])
        if mo == today.month and y < today.year and m not in trip_months:
            ago.append(dict(key=f"a{m}", kind="ago", years_ago=today.year - y, month=m, **{"from": m}, to=m,
                            count=int(len(by_month[m])), _members=by_month[m]))
    out += ago
    taken = trip_months | {c["month"] for c in ago}
    busiest = [m for m, ids in sorted(by_month.items(), key=lambda kv: -len(kv[1]))
               if len(ids) >= 3 and m not in taken][:max(0, MAX_CARDS - len(out))]
    for m in sorted(busiest, reverse=True):
        out.append(dict(key=f"m{m}", kind="month", month=m, **{"from": m}, to=m, count=int(len(by_month[m])),
                        _members=by_month[m]))
    return out[:MAX_CARDS]


def public(cards_):
    return [{k: v for k, v in c.items() if not k.startswith("_")} for c in cards_]
