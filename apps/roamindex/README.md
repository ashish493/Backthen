# RoamIndex (desktop app)

Search, browse and map your photo and video archive. Tauri 2 (Rust shell + WebView2) with React and TypeScript. The search engine is RoamEngine, running locally ([ADR-010](../../docs/decisions/ADR-010-engine-as-local-api-sidecar.md)).

## Views
- **Search:**
  - Describe what you're looking for: "a waterfall in a forest", optionally with `type:video year:2019`.
  - Filter by type, date range and "has location".
  - Video results show the matching moment.
  - With no query, the view shows the library newest first, grouped by month.
- **Viewer:**
  - Photos are shown as upright previews (HEIC and NEF included).
  - Videos play from the matching moment, with a strip of best moments to jump between.
  - Codecs the webview can't play (for example HEVC without the Windows extension, or MPEG-4 Part 2) show the frame at that moment, plus **Open in default player**.
  - Also shown: date and its source, location and its source, every duplicate copy with **Show in folder**, **Find similar**, **Similar to this moment**, and **Show on map**.
- **Timeline:** items per month (bars on a continuous time axis). Click a month to open it. Undated items are listed separately.
- **Map:** an offline world map, with no map tiles ([ADR-011](../../docs/decisions/ADR-011-offline-map.md)). Scroll to zoom, drag to pan, and click to see what was taken nearby.
- **Library:**
  - Counts and coverage (dated and located share).
  - Indexed folders, with **Update** to rescan.
  - **Index folder** to add a new one, with a progress bar, time left and **Stop** (kill-safe: the next run resumes).
  - Settings: library folder and Python.
- **Evaluate:**
  - Add the queries you really search for, and grade the top 48 results (keys 0/1/2).
  - Shows Recall, Precision and nDCG at 1/5/10/20, plus MRR, against a random baseline.
  - Labels stay in the library folder.

## Run (development)
```
cd apps/roamindex
npm install
npx tauri dev            # or: npx tauri build  -> src-tauri/target/release/bundle/nsis/*.exe
```
- **Python:** the shell uses `<repo>/.venv/Scripts/python.exe` (with `roamingest` and `roamengine` installed) unless `ROAM_PYTHON` is set.
- **Library:** the shell opens `<repo>/out/archive355` if it exists, otherwise `%APPDATA%\app.roamindex.desktop\library`, unless `ROAM_LIBRARY` is set. Both can be changed in Library → Settings, which are saved in `%APPDATA%\app.roamindex.desktop\config.json`.
- **Logs:** engine errors go to `%LOCALAPPDATA%\app.roamindex.desktop\logs\engine.log`.

## Privacy
- **Network:** the only peer is `127.0.0.1`, enforced by the CSP (`connect-src`, `img-src` and `media-src`).
- **No outside requests:** no fonts, tiles, analytics or update checks.
- **Read-only:** the app never modifies source files.

## Verified (22 Nov 2026)
- **Synthetic demo library, rendered in headless Edge:** all views, plus video moments.
- **Real Tauri window, driven over the WebView2 DevTools protocol:**
  - Handshake and library load.
  - Text search.
  - Video playback starting at the matched moment, and moment jumps.
- **Engine lifecycle:** the engine exits on both window close and hard kill.
- **On the 355 GB index** (text only, no screenshots of personal media):
  - Library ready 2.5 s after launch.
  - Search to rendered results: about 85 ms.
  - The first query after launch: about 3 s, while the text model loads.

## Not yet
- Bundled Python runtime (the installer currently expects the repository's Python environment).
- Place-name search (offline gazetteer).
- Multiple libraries.
- Thumbnails at the matching moment in the grid (the moments show in the viewer).
