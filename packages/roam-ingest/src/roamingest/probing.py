"""Probing: capture time, GPS and orientation from EXIF, video container tags and Takeout-style JSON sidecars.

Precedence (each value records its source):
  capture_time_utc : EXIF with an offset tag > sidecar photoTakenTime > video container creation_time.
                     EXIF without an offset is never treated as UTC.
  capture_time_local: EXIF DateTimeOriginal (camera clock, no zone) when present.
                     source 'exif_local' = only this local time is known.
  GPS              : EXIF GPS > sidecar geoData > sidecar geoDataExif > video ISO 6709 location tag.
A capture time from file mtime is never stored: mtime reflects copying, not capture.
All values stay in the local manifest; reports aggregate only counts per source.
"""
import datetime as dt
import io
import json
import re

from PIL import Image

_EXIF_IFD, _GPS_IFD = 0x8769, 0x8825
_DTO, _DTD, _DT, _OFF_O, _OFF = 36867, 36868, 306, 36881, 36882
_ORIENT = 274
_MIN_YEAR, _MAX_YEAR = 1980, 2100
_ISO6709 = re.compile(r"^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)?")


def _parse_exif_dt(s):
    if not isinstance(s, str):
        return None
    s = s.strip().strip("\x00")
    try:
        t = dt.datetime.strptime(s[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    return t if _MIN_YEAR <= t.year <= _MAX_YEAR else None


def _parse_offset(s):
    if not isinstance(s, str):
        return None
    m = re.match(r"^([+-])(\d{2}):?(\d{2})$", s.strip())
    if not m:
        return None
    mins = int(m.group(2)) * 60 + int(m.group(3))
    return -mins if m.group(1) == "-" else mins


def _rat(x):
    try:
        return float(x)
    except (TypeError, ValueError, ZeroDivisionError):
        try:
            return x[0] / x[1]
        except Exception:  # noqa: BLE001
            return None


def _dms(v, ref):
    try:
        d, m, s = (_rat(x) for x in v)
        val = d + m / 60 + s / 3600
    except Exception:  # noqa: BLE001
        return None
    if isinstance(ref, bytes):
        ref = ref.decode(errors="ignore")
    return -val if str(ref).strip().upper() in ("S", "W") else val


def _valid_latlon(lat, lon):
    return (lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180
            and not (abs(lat) < 1e-9 and abs(lon) < 1e-9))


def _iso_utc(t):
    return t.astimezone(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def exif_probe(data):
    """EXIF fields from in-memory image bytes (header parse only, no pixel decode). Never raises."""
    out = {}
    try:
        with Image.open(io.BytesIO(data)) as im:
            ex = im.getexif()
            out["orientation"] = ex.get(_ORIENT)
            try:
                sub = ex.get_ifd(_EXIF_IFD)
            except Exception:  # noqa: BLE001
                sub = {}
            t = _parse_exif_dt(sub.get(_DTO)) or _parse_exif_dt(sub.get(_DTD)) or _parse_exif_dt(ex.get(_DT))
            if t:
                out["capture_time_local"] = t.isoformat(timespec="seconds")
                off = _parse_offset(sub.get(_OFF_O)) if sub.get(_DTO) else None
                if off is None:
                    off = _parse_offset(sub.get(_OFF))
                if off is not None:
                    out["tz_offset_min"] = off
                    out["capture_time_utc"] = _iso_utc(t.replace(tzinfo=dt.timezone(dt.timedelta(minutes=off))))
            try:
                g = ex.get_ifd(_GPS_IFD)
            except Exception:  # noqa: BLE001
                g = {}
            if g:
                lat, lon = _dms(g.get(2), g.get(1)), _dms(g.get(4), g.get(3))
                if _valid_latlon(lat, lon):
                    out["lat"], out["lon"] = round(lat, 7), round(lon, 7)
                    alt = _rat(g.get(6)) if g.get(6) is not None else None
                    if alt is not None:
                        out["alt"] = round(-alt if g.get(5) in (1, b"\x01") else alt, 2)
    except Exception:  # noqa: BLE001
        pass
    return out


def sidecar_probe(raw):
    """Google Takeout style JSON sidecar -> {capture_time_utc, lat, lon, alt, gps_field}. Never raises."""
    out = {}
    try:
        d = json.loads(raw)
    except Exception:  # noqa: BLE001
        return out
    if not isinstance(d, dict):
        return out
    for key in ("photoTakenTime",):                     # creationTime is the upload time, not the capture time
        try:
            ts = int(d[key]["timestamp"])
        except Exception:  # noqa: BLE001
            continue
        if ts > 0:
            t = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
            if _MIN_YEAR <= t.year <= _MAX_YEAR:
                out["capture_time_utc"] = _iso_utc(t)
                out["time_field"] = key
                break
    for key in ("geoData", "geoDataExif"):
        g = d.get(key)
        if not isinstance(g, dict):
            continue
        lat, lon = _rat(g.get("latitude")), _rat(g.get("longitude"))
        if _valid_latlon(lat, lon):
            out["lat"], out["lon"] = round(lat, 7), round(lon, 7)
            alt = _rat(g.get("altitude"))
            if alt is not None and alt != 0:
                out["alt"] = round(alt, 2)
            out["gps_field"] = key
            break
    return out


def container_probe(meta):
    """Video container tags (videos.container_meta) -> capture time UTC and ISO 6709 location."""
    out = {}
    ct = meta.get("creation_time") if meta else None
    if ct:
        try:
            t = dt.datetime.fromisoformat(ct.replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt.timezone.utc)
            if _MIN_YEAR <= t.year <= _MAX_YEAR:
                out["capture_time_utc"] = _iso_utc(t)
        except ValueError:
            pass
    loc = meta.get("location") if meta else None
    if loc:
        m = _ISO6709.match(loc.strip())
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if _valid_latlon(lat, lon):
                out["lat"], out["lon"] = round(lat, 7), round(lon, 7)
                if m.group(3):
                    out["alt"] = round(float(m.group(3)), 2)
    return out


def merge(exif=None, sidecar=None, container=None):
    """Apply the precedence rules -> asset columns (with *_source fields)."""
    exif, sidecar, container = exif or {}, sidecar or {}, container or {}
    out = dict(capture_time_utc=None, capture_time_local=exif.get("capture_time_local"),
               tz_offset_min=exif.get("tz_offset_min"), capture_time_source=None,
               lat=None, lon=None, alt=None, gps_source=None, orientation=exif.get("orientation"))
    for src, d in (("exif", exif), ("sidecar", sidecar), ("container", container)):
        if d.get("capture_time_utc"):
            out["capture_time_utc"], out["capture_time_source"] = d["capture_time_utc"], src
            break
    if out["capture_time_source"] is None and out["capture_time_local"]:
        out["capture_time_source"] = "exif_local"
    for src, d in (("exif", exif), ("sidecar", sidecar), ("container", container)):
        if d.get("lat") is not None:
            out["lat"], out["lon"], out["alt"] = d["lat"], d["lon"], d.get("alt")
            out["gps_source"] = src if src != "sidecar" else "sidecar_" + sidecar.get("gps_field", "geoData")
            break
    return out
