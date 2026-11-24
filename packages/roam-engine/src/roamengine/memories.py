"""Memory cards for the home screen: shared trips first (newest first), then this month in earlier years, then the
busiest months -- and never the same day on two cards.

candidates() lists possible cards in that order; select() walks them, takes away days an earlier card already
holds, and keeps a card only if it still has enough photos and is *shared* (at least one of its best photos shows
two or more people; the check is supplied by the engine). Covers are chosen separately. Private fields start with an
underscore and never reach the API.
"""
import datetime as dt

import numpy as np

MAX_CARDS = 12
FIRST = 4            # candidates whose faces are checked before anything else
MIN_PHOTOS = dict(trip=6, ago=3, month=3)


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


def candidates(cat, trips, today=None, limit=36):
    """Possible cards in priority order. trips: journeys.structure()["trips"] (newest first, 3+ days, _members)."""
    today = today or dt.date.today()
    out = [dict(key=f"t{t['start']}", kind="trip", title=t["title"], country=t["country"], **{"from": t["start"]},
                to=t["end"], days=t["days"], count=t["count"], _members=t["_members"], _town=t["_town"])
           for t in trips]
    by_month = months(cat)
    for m in sorted(by_month, reverse=True):
        y, mo = int(m[:4]), int(m[5:])
        if mo == today.month and y < today.year:
            out.append(dict(key=f"a{m}", kind="ago", years_ago=today.year - y, month=m, **{"from": m}, to=m,
                            count=int(len(by_month[m])), _members=by_month[m]))
    ago = {c["month"] for c in out if c["kind"] == "ago"}
    for m, ids in sorted(by_month.items(), key=lambda kv: -len(kv[1])):
        if m not in ago and len(ids) >= MIN_PHOTOS["month"]:
            out.append(dict(key=f"m{m}", kind="month", month=m, **{"from": m}, to=m, count=int(len(ids)),
                            _members=ids))
    return out[:limit]


def select(cands, day_of, shared, max_cards=MAX_CARDS):
    """The cards to show: in order, each keeps only days no earlier card holds, needs MIN_PHOTOS left, and must be
    shared -- shared(members) is True, False, or None (not checked yet: shown for now, may be dropped later).
    Deterministic, so re-running as checks come in gives the settled row. Months shown newest first after trips.
    day_of: day number per asset index."""
    taken, out = set(), []
    for c in cands:
        if len(out) >= max_cards:
            break
        m = c["_members"]
        m = m[~np.isin(day_of[m], list(taken))] if taken else m
        if len(m) < MIN_PHOTOS[c["kind"]]:
            continue
        verdict = shared(m)
        if verdict is False:
            continue
        card = dict(c, _members=m, count=int(len(m)), _checked=verdict is not None)
        if card["kind"] == "trip" and len(m) < len(c["_members"]):      # an earlier card took some of its days
            card["days"] = min(c["days"], int(len(np.unique(day_of[m]))))
        out.append(card)
        taken.update(np.unique(day_of[m]).tolist())
    by_kind = lambda k: [c for c in out if c["kind"] == k]
    return by_kind("trip") + by_kind("ago") + sorted(by_kind("month"), key=lambda c: c["from"], reverse=True)


def public(cards_):
    return [{k: v for k, v in c.items() if not k.startswith("_")} for c in cards_]
