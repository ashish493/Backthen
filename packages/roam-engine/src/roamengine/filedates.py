"""Last-resort capture dates from file names (BOTTLENECKS.md #1), day precision only.

Used only for items with no camera, sidecar or container time. A WhatsApp name holds the day the file was sent, not
taken, so these dates never outrank real metadata, and trips still need GPS to start (journeys.py).
"""
import datetime as dt
import re

_PATTERNS = [
    re.compile(r"(?:IMG|VID|AUD|PTT|STK|DOC)-(\d{4})(\d{2})(\d{2})-WA\d", re.I),   # WhatsApp
    re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})[_-]\d{6,9}(?!\d)"),                 # camera: 20190101_101010
    re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"),                           # 2019-01-01
]
MIN_YEAR = 1990


def date_from_name(name, max_year=None):
    """'IMG-20190101-WA0001.jpg' -> '2019-01-01T12:00:00' (local noon, so the day survives any zone), else None."""
    max_year = max_year or dt.date.today().year + 1
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    for p in _PATTERNS:
        for m in p.finditer(base):
            y, mo, d = (int(g) for g in m.groups())
            if not MIN_YEAR <= y <= max_year:
                continue
            try:
                return dt.date(y, mo, d).isoformat() + "T12:00:00"
            except ValueError:
                continue
    return None
