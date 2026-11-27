"""Inferred locations (BOTTLENECKS.md #2): an item with a real capture time but no GPS takes the location of a
located item taken close in time. Never chained (inferred points are not anchors), never from a day-only file-name
date, and only between times of the same kind (both with a zone, or both camera-clock), so a zone offset can't
shift the match by hours."""
import numpy as np

ONE_SIDE_S = 30 * 60          # the nearest located item alone is enough within 30 min
TWO_SIDE_S = 2 * 3600         # up to 2 h if the located items before and after agree
AGREE_KM = 2.0


def _km(la1, lo1, la2, lo2):
    p = np.pi / 180
    a = np.sin((la2 - la1) * p / 2) ** 2 + np.cos(la1 * p) * np.cos(la2 * p) * np.sin((lo2 - lo1) * p / 2) ** 2
    return 12742 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def infer_locations(t, is_utc, lat, lon, eligible, anchor):
    """-> (src, dt_s): per item the index of the anchor it inherits from (-1 for none) and the signed time
    difference anchor minus item in seconds (nan for none)."""
    n = len(t)
    src, dts = np.full(n, -1, np.int64), np.full(n, np.nan)
    for cls in (True, False):
        a = np.flatnonzero(anchor & (is_utc == cls) & np.isfinite(t))
        c = np.flatnonzero(eligible & (is_utc == cls) & np.isfinite(t))
        if not len(a) or not len(c):
            continue
        a = a[np.argsort(t[a], kind="stable")]
        ta = t[a]
        pos = np.searchsorted(ta, t[c])
        prev, nxt = np.clip(pos - 1, 0, len(a) - 1), np.clip(pos, 0, len(a) - 1)
        has_p, has_n = pos > 0, pos < len(a)
        dp = np.where(has_p, t[c] - ta[prev], np.inf)
        dn = np.where(has_n, ta[nxt] - t[c], np.inf)
        ip, inx = a[prev], a[nxt]
        agree = (dp <= TWO_SIDE_S) & (dn <= TWO_SIDE_S) & (_km(lat[ip], lon[ip], lat[inx], lon[inx]) <= AGREE_KM)
        use_p = dp <= dn
        best = np.where(use_p, ip, inx)
        bestd = np.where(use_p, dp, dn)
        ok = agree | (bestd <= ONE_SIDE_S)
        src[c[ok]] = best[ok]
        dts[c[ok]] = np.where(use_p, -dp, dn)[ok]
    return src, dts
