import { useEffect, useRef, useState } from "react";
import { api, fmtInt, type Asset, type Filters } from "../api";
import { Grid } from "../components";
import { Globe, type GlobeHandle, type GlobePick, type GlobePoints } from "../Globe";
import { Viewer } from "./Viewer";

export function MapView({ focus, onSimilar }: { focus: Asset | null; onSimilar: (a: Asset, frame?: number) => void }) {
  const [type, setType] = useState<"" | "photo" | "video">("");
  const [pts, setPts] = useState<GlobePoints & { total: number } | null>(null);
  const [pick, setPick] = useState<GlobePick | null>(null);
  const [near, setNear] = useState<Asset[]>([]);
  const [nearTotal, setNearTotal] = useState(0);
  const [open, setOpen] = useState<number | null>(null);
  const globe = useRef<GlobeHandle>(null);

  useEffect(() => { api.points({ type }).then(setPts).catch(() => setPts(null)); }, [type]);
  useEffect(() => { if (pick) choose(pick); }, [type]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {                                     // "Show on map" from a viewer
    if (!focus || focus.lat == null || focus.lon == null || !pts) return;
    globe.current?.flyTo(focus.lat, focus.lon, 400);
    choose({ lat: focus.lat, lon: focus.lon, radiusKm: 1 });
  }, [focus?.asset_id, !!pts]); // eslint-disable-line react-hooks/exhaustive-deps

  function choose(p: GlobePick) {
    const r = Math.round(p.radiusKm * 10) / 10;
    setPick({ ...p, radiusKm: r });
    const f: Filters = { type, lat: p.lat, lon: p.lon, radius_km: r };
    api.browse(f, 120, 0).then((pg) => { setNear(pg.items); setNearTotal(pg.total); })
      .catch(() => { setNear([]); setNearTotal(0); });
  }

  const km = (r: number) => (r < 1 ? `${Math.round(r * 1000)} m` : r < 20 ? `${r.toFixed(1)} km` : `${fmtInt(Math.round(r))} km`);

  return (
    <div className="view">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 10 }}>
        <h1 style={{ margin: 0 }}>Map</h1>
        <div className="row">
          <span className="hint">{pts ? `${fmtInt(pts.total)} items with a location` : "Loading…"} · drag to turn, scroll or pinch to zoom, click a bubble to see it</span>
          <div className="seg">
            {([["", "All"], ["photo", "Photos"], ["video", "Videos"]] as const).map(([v, l]) => (
              <button key={v} className={type === v ? "on" : ""} onClick={() => setType(v)}>{l}</button>
            ))}
          </div>
        </div>
      </div>
      <div className="mapwrap">
        <div className="map">
          <Globe ref={globe} points={pts} fitKey={type} onPick={choose}
            ring={pick ? { lat: pick.lat, lon: pick.lon, km: pick.radiusKm } : null} />
          <div className="mapctl">
            <button onClick={() => globe.current?.zoomBy(2)} aria-label="Zoom in" title="Zoom in">+</button>
            <button onClick={() => globe.current?.zoomBy(0.5)} aria-label="Zoom out" title="Zoom out">−</button>
            <button onClick={() => globe.current?.fit()} aria-label="Fit to my places" title="Fit to my places">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" /><circle cx="12" cy="12" r="2.5" /></svg>
            </button>
            <button onClick={() => globe.current?.world()} aria-label="Whole globe" title="Whole globe">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="12" cy="12" r="8.5" /><path d="M3.5 12h17M12 3.5c3 3.2 3 13.8 0 17M12 3.5c-3 3.2-3 13.8 0 17" /></svg>
            </button>
          </div>
          <div className="legend"><span className="dot" />photos <span className="dot v" />videos</div>
        </div>
        <div className="side">
          {!pick && <div className="empty">Click a bubble or anywhere on the globe to see photos and videos taken there.</div>}
          {pick && (
            <div className="meta-line">
              {fmtInt(nearTotal)} items within {km(pick.radiusKm)}{nearTotal > near.length && ` (showing the newest ${near.length})`}
            </div>
          )}
          <Grid items={near} onOpen={setOpen} />
        </div>
      </div>
      {open !== null && (
        <Viewer list={near} index={open} onIndex={setOpen} onClose={() => setOpen(null)}
          onSimilar={(a, fr) => { setOpen(null); onSimilar(a, fr); }} />
      )}
    </div>
  );
}
