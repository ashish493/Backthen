# RoamEngine

Local search over a RoamIngest library (`manifest.sqlite`, schema v1). Nothing leaves the machine.

## What it does
- **Natural-language search:** CLIP text embedding against every photo and every sampled video frame. A video ranks by its best frame and returns its best **moments** with timestamps.
- **Similar items:** for a whole photo or video, or for one video moment.
- **Filters:** photo or video; date range (`2019`, `2019-05`, `2019-05-06` bounds, inclusive); has a date; has a location; within a radius of a point; inside a bounding box.
  - The same filters can be typed inline: `type:video after:2019-05 before:2020 year:2021 has:gps`.
- **Browsing:** newest first, a timeline (counts per month), and map points.
- **Evaluation:** a labelled query store (`labels.sqlite` in the library folder) with Recall@K, Precision@K, nDCG@K and MRR against a random-ranking floor (`roamengine eval`).
- **Local API** for the RoamIndex app, with ingest control (start, progress, stop) through a kill-safe RoamIngest subprocess.

## Use
```
pip install -e packages/roam-engine
roamengine search "a waterfall in a forest" --library D:\Roam\out\archive355 [--type video] [--from 2019 --to 2020] [--paths]
roamengine stats  --library ...
roamengine bench  --library ...          # query latency, aggregate only
roamengine eval   --library ...          # metrics over the labels made in the app
roamengine serve  --library ... [--port 0] [--token ...]
```

## Measured on the 355 GB index (48,569 items, 73,815 frames, ViT-B/32)
| What | Result |
|---|---|
| Catalogue load | 1.8 s, 337 MB RSS (151 MB of fp32 vectors) |
| Query, CPU text encoder (36 queries) | p50 32 ms, p95 37 ms: encode 26 ms + rank 6 ms |
| In the app, end to end | about 85 ms to rendered results |
| First query after start | up to about 3 s while the text model loads in the background |

Exact search is fast enough at this scale; ANN indexing is deferred (`docs/decisions/ADR-009`).

## Local API security
- Binds **127.0.0.1 only**.
- Requires a per-launch token, sent in the `X-Roam-Token` header, or in `?token=` for image and video sources.
- Rejects any `Host` other than 127.0.0.1 or localhost, which blocks DNS rebinding.
- Sends CORS headers only to the app's own origins.
- Does not log requests, because URLs carry search text.
- Opens originals read-only and streams them with HTTP range support.
- Renders HEIC and NEF to JPEG on demand.
- Video frame grabs give a picture for codecs the webview cannot play.

## Notes and limitations
- **Score calibration:** a video's score is its best frame's score. With up to 32 frames per video, videos get more chances to match than single photos.
- **Local camera times:** EXIF times without a time zone are treated as UTC for sorting and filtering, so they can be a few hours off.
- **Duplicates:** byte-identical copies are one item; every copy's path is listed in the asset detail.
- **Tests:** `pytest packages/roam-engine` runs 23 tests on a synthetic colour library: ranking, video moments, filters, API authentication, media endpoints, labels and evaluation, and an ingest started over the API.

## Places added by hand
`places.sqlite`, next to the manifest, holds locations the user adds in RoamIndex ("Add places"). The engine overlays them in memory: search, filters, the map and `/api/asset` see them, with `gps_source = "manual"` and the original location kept as `original_lat` and `original_lon`.

The manifest and the media files are never written, so capture dates and times and the camera's GPS stay exactly as ingested (`tests/test_places.py` checks this byte for byte).

API:
- `GET /api/places` lists recent places.
- `POST /api/places/set {asset_ids, lat, lon, name?}` adds a location.
- `POST /api/places/clear {asset_ids}` removes it.
- `GET /api/places/suggest/<id>` returns the locations of items taken within 6 h of this one.
