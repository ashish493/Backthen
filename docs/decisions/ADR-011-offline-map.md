# ADR-011: Offline world map; no map tiles

**Status:** accepted, 22 Nov 2026

## Context
Online map tiles (OpenStreetMap, Mapbox and others) are fetched for the region being viewed. Every pan and zoom over the user's photo locations would reveal those places to a third party, which breaks "nothing leaves the machine".

## Decision
- Draw an Equal Earth projection of the Natural Earth 1:110m country outlines (`world-atlas`, bundled into the app) with `d3-geo`, as SVG.
- Plot photo and video points from `/api/points`.
- A click searches within a radius that shrinks as you zoom.

## Consequences
- No street-level detail.
- Place names (search by city) need an offline gazetteer (Stage 5, e.g. GeoNames `cities15000`), not an online geocoder.

## Update (22 Nov 2026): globe
- **Projection:** the map is now an orthographic globe drawn on a canvas, still from the bundled Natural Earth outlines with no tiles or network. The 1:50m outlines load lazily on deep zoom.
- **Input:** drag turns the globe. Mouse wheel, touchpad pinch (ctrl+wheel, captured so the webview does not page-zoom) and touch pinch zoom around the pointer.
- **Pins:** nearby pins merge into count bubbles in screen space.
- **First view:** it frames the spherical centre of the pins at a zoom that fits 99% of them. When they spread more than about 57° from that centre, it shows the whole globe.

## Update (22 Nov 2026): touchpad pinch and place search
- **Touchpad pinch:** wry (0.57) calls `SetIsPinchZoomEnabled(false)` unless `zoomHotkeysEnabled` is set, and WebView2 then drops touchpad pinch gestures before the page sees them.
  - The window now sets `zoomHotkeysEnabled: true`, so a pinch reaches the page as ctrl+wheel. Page-level ctrl+wheel is cancelled everywhere (`main.tsx`), and only the globe uses it.
  - Verified in the real WebView2 with a synthesized touchpad pinch: 2× pinch → exactly 2× globe zoom, page zoom stays 1.
- **Place search** is offline by default: the GeoNames gazetteer of every place with 1,000+ people (172k places, 4 MB, CC BY 4.0), bundled with the engine.
  - **Online search (opt-in):** OpenStreetMap Nominatim, off by default, only on an explicit search, rate-limited to 1 request/s per its usage policy. It sends the typed text.
  - **Google Places/Geocoding was not used:** it needs an API key with a billing account, and it would send every search to Google.
