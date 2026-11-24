"""Locations the user adds by hand. They live in `<library>/places.sqlite`, never in the manifest or the media files.

The manifest (written by RoamIngest) and the original photos and videos are not modified: capture time, camera GPS
and every other field stay exactly as ingested. The engine overlays these tags on its in-memory catalogue, so search,
filters and the map see them; removing a tag brings back whatever the manifest says (often: no location).
"""
import datetime as dt
import math
import os
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS geotags (
    asset_id TEXT PRIMARY KEY,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    name TEXT,                 -- optional place name the user typed, e.g. 'Lisbon'
    set_at TEXT NOT NULL
);
"""


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def check_latlon(lat, lon):
    lat, lon = float(lat), float(lon)
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("latitude must be within -90..90 and longitude within -180..180")
    return lat, lon


class Places:
    def __init__(self, library):
        self.path = os.path.join(library, "places.sqlite")
        self._lock = threading.Lock()
        self.con = sqlite3.connect(self.path, check_same_thread=False)
        self.con.executescript(SCHEMA)
        self.con.commit()

    def all(self):
        """asset_id -> (lat, lon, name)"""
        with self._lock:
            return {a: (la, lo, n) for a, la, lo, n in self.con.execute("SELECT asset_id, lat, lon, name FROM geotags")}

    def set(self, asset_ids, lat, lon, name=None):
        lat, lon = check_latlon(lat, lon)
        name = (name or "").strip()[:120] or None
        now = _now()
        with self._lock, self.con:
            self.con.executemany(
                "INSERT INTO geotags(asset_id, lat, lon, name, set_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(asset_id) DO UPDATE SET lat=excluded.lat, lon=excluded.lon, name=excluded.name, "
                "set_at=excluded.set_at", [(a, lat, lon, name, now) for a in asset_ids])
        return len(asset_ids)

    def clear(self, asset_ids):
        with self._lock, self.con:
            cur = self.con.executemany("DELETE FROM geotags WHERE asset_id = ?", [(a,) for a in asset_ids])
        return cur.rowcount

    def recent(self, n=12):
        """Recently used places: one row per (name, rounded position), newest first."""
        with self._lock:
            rows = self.con.execute(
                "SELECT name, ROUND(lat, 4), ROUND(lon, 4), COUNT(*), MAX(set_at) FROM geotags "
                "GROUP BY name, ROUND(lat, 4), ROUND(lon, 4) ORDER BY MAX(set_at) DESC LIMIT ?", (n,)).fetchall()
        return [dict(name=r[0], lat=r[1], lon=r[2], count=r[3]) for r in rows]
