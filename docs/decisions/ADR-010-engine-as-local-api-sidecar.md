# ADR-010: RoamEngine runs as a local HTTP sidecar of the desktop app

**Status:** accepted, 22 Nov 2026

## Context
- The engine is Python (numpy, open_clip and RoamIngest), and the desktop shell is Tauri (Rust + WebView2).
- The architecture doc requires that the UI never imports engine internals (Stage 7).

## Decision
- The Tauri shell starts `python -m roamengine serve --exit-with-parent` and reads a one-line JSON handshake `{event: ready, port, token}`. The UI calls the engine over `http://127.0.0.1:<port>` with that token.
- The engine exits when the shell's stdin pipe closes. Verified: both a window close and a hard kill of the app end the engine, so no orphan processes are left.

## Security
- 127.0.0.1 only.
- A per-launch random token, checked in constant time.
- A `Host` header allow-list against DNS rebinding.
- CORS only for the app's own origins.
- The app's CSP limits `connect-src`, `img-src` and `media-src` to `127.0.0.1`.
- No request logging.

## Consequences
- The engine is testable without the UI: 23 pytest tests, including the HTTP layer.
- The engine can also be scripted (`roamengine search/eval`).
- The app needs a Python environment. In v0.1 the shell finds the repository's `.venv`, and Settings can point elsewhere. Bundling an embedded Python runtime is future work (packaging).
