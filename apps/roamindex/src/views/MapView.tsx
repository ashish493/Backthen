import { useEffect, useMemo, useRef, useState } from "react";
import { api, fmtInt, mediaUrl, type Asset, type Filters, type Trip } from "../api";
import { Grid, Icon } from "../components";
import { Globe, type GlobeHandle, type GlobePick, type GlobePoints, type LonLat } from "../Globe";
import { useJourneys } from "./Memories";
import { Viewer } from "./Viewer";

const fmtRange = (t: Trip) => {
  const a = new Date(t.start + "T00:00:00Z"), b = new Date(t.end + "T00:00:00Z");
  const o: Intl.DateTimeFormatOptions = { day: "numeric", month: "short", timeZone: "UTC" };
  return t.start === t.end ? a.toLocaleDateString(undefined, { ...o, year: "numeric" })
    : `${a.toLocaleDateString(undefined, o)} – ${b.toLocaleDateString(undefined, { ...o, year: "numeric" })}`;
};

export function MapView({ focus, onSimilar }: { focus: Asset | null; onSimilar: (a: Asset, frame?: number) => void }) {
  const [type, setType] = useState<"" | "photo" | "video">("");
  const [pts, setPts] = useState<GlobePoints & { total: number } | null>(null);
  const [pick, setPick] = useState<GlobePick | null>(null);
  const [pickLabel, setPickLabel] = useState<string | null>(null);
  const [near, setNear] = useState<Asset[]>([]);
  const [nearTotal, setNearTotal] = useState(0);
  const [open, setOpen] = useState<number | null>(null);
  const [year, setYear] = useState<number | null>(null);          // null: all years; else everything up to that year
  const [playing, setPlaying] = useState(false);
  const [replay, setReplay] = useState<{ i: number; landed: boolean } | null>(null);
  const [openCountry, setOpenCountry] = useState<string | null>(null);
  const globe = useRef<GlobeHandle>(null);
  const replayToken = useRef(0);
  const journeys = useJourneys();

  useEffect(() => { api.points({ type }).then(setPts).catch(() => setPts(null)); }, [type]);
  useEffect(() => { if (pick) choose(pick, pickLabel); }, [type]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {                                     // "Show on map" from a viewer
    if (!focus || focus.lat == null || focus.lon == null || !pts) return;
    globe.current?.flyTo(focus.lat, focus.lon, 400);
    choose({ lat: focus.lat, lon: focus.lon, radiusKm: 1 });
  }, [focus?.asset_id, !!pts]); // eslint-disable-line react-hooks/exhaustive-deps

  function choose(p: GlobePick, label: string | null = null) {
    const r = Math.round(p.radiusKm * 10) / 10;
    setPick({ ...p, radiusKm: r }); setPickLabel(label);
    const f: Filters = { type, lat: p.lat, lon: p.lon, radius_km: r };
    api.browse(f, 120, 0).then((pg) => { setNear(pg.items); setNearTotal(pg.total); })
      .catch(() => { setNear([]); setNearTotal(0); });
  }

  // ---------------------------------------------------------------- time slider: pins appear year by year
  const years = useMemo(() => {
    const ys = [...new Set((pts?.year || []).filter((y) => y > 0))].sort((a, b) => a - b);
    return ys.length ? Array.from({ length: ys[ys.length - 1] - ys[0] + 1 }, (_, i) => ys[0] + i) : [];
  }, [pts]);
  const shown = useMemo(() => {
    if (!pts || year == null || !pts.year) return pts;
    const keep = pts.year.map((y, i) => (y > 0 && y <= year ? i : -1)).filter((i) => i >= 0);
    const pick = <T,>(a: T[] | undefined) => (a ? keep.map((i) => a[i]) : undefined);
    return { ...pts, asset_ids: pick(pts.asset_ids)!, lat: pick(pts.lat)!, lon: pick(pts.lon)!, video: pick(pts.video)!,
      year: pick(pts.year), thumb: pick(pts.thumb), cover: pick(pts.cover), town: pick(pts.town), total: keep.length };
  }, [pts, year]);
  useEffect(() => {
    if (!playing || !years.length) return;
    const id = setInterval(() => setYear((y) => {
      if (y == null) return years[0];
      if (y >= years[years.length - 1]) { setPlaying(false); return null; }
      return y + 1;
    }), 1100);
    return () => clearInterval(id);
  }, [playing, years]);

  // ---------------------------------------------------------------- travel replay: fly trip to trip, oldest first
  const trips = useMemo(() => [...(journeys?.trips || [])].sort((a, b) => a.start.localeCompare(b.start)), [journeys]);
  const stops = useMemo(() => trips.map((t) => [t.lon, t.lat] as LonLat), [trips]);
  const startReplay = async () => {
    if (!trips.length || !globe.current) return;
    const token = ++replayToken.current;
    setPick(null); setYear(null); setPlaying(false);
    const alive = () => replayToken.current === token;
    await new Promise((r) => setTimeout(r, 50));
    globe.current.world();
    await new Promise((r) => setTimeout(r, 900));
    let from: LonLat = stops[0];
    for (let i = 0; i < trips.length && alive(); i++) {
      setReplay({ i, landed: false });
      const to = stops[i];
      if (i === 0) globe.current?.flyTo(to[1], to[0], 7);
      else await globe.current?.travel(from, to, 7, 2800);
      if (!alive()) return;
      setReplay({ i, landed: true });
      await new Promise((r) => setTimeout(r, 3400));                // the postcard stays a moment
      from = to;
    }
    if (alive()) { setReplay(null); globe.current?.fit(); }
  };
  const stopReplay = () => { replayToken.current++; setReplay(null); };
  useEffect(() => () => { replayToken.current++; }, []);
  const cur = replay ? trips[replay.i] : null;
  // with the time slider on a year, only the trips up to that year are drawn
  const routeStops = useMemo(() => (year == null || replay ? stops : stops.filter((_, i) => Number(trips[i].start.slice(0, 4)) <= year)),
    [stops, trips, year, replay]);

  const km = (r: number) => (r < 1 ? `${Math.round(r * 1000)} m` : r < 20 ? `${r.toFixed(1)} km` : `${fmtInt(Math.round(r))} km`);
  const countries = journeys?.world?.countries_list || [];

  return (
    <div className="view mapview">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 10 }}>
        <h1 style={{ margin: 0 }}>Map</h1>
        <div className="row">
          <span className="hint">{shown ? `${fmtInt(shown.total)} items with a location` : "Loading…"} · drag to turn, scroll to zoom</span>
          {trips.length > 0 && (replay
            ? <button className="btn" onClick={stopReplay}><Icon.close />Stop replay</button>
            : <button className="btn primary" onClick={startReplay}><Icon.play />Replay my travels</button>)}
          <div className="seg">
            {([["", "All"], ["photo", "Photos"], ["video", "Videos"]] as const).map(([v, l]) => (
              <button key={v} className={type === v ? "on" : ""} onClick={() => setType(v)}>{l}</button>
            ))}
          </div>
        </div>
      </div>
      <div className="mapwrap">
        <div className="map">
          <Globe ref={globe} points={shown} fitKey={type} onPick={(p) => { stopReplay(); choose(p); }} photoPins
            routes={routeStops.length > 1 ? routeStops : null} routeDone={replay ? replay.i : -1}
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
          {cur && replay?.landed && (
            <div className="replaycard" key={cur.start}>
              <div className={`mosaic n${cur.cover.length}`}>{cur.cover.map((id) => <img key={id} src={mediaUrl("thumb", id)} alt="" />)}</div>
              <div className="replaymeta">
                <span className="memtag trip">Trip {replay.i + 1} of {trips.length}</span>
                <b className="posttitle">{cur.title}</b>
                <small>{cur.country && cur.country !== cur.title ? `${cur.country} · ` : ""}{fmtRange(cur)} · {fmtInt(cur.count)} items</small>
              </div>
            </div>
          )}
          {years.length > 1 && !replay && (
            <div className="timeslider">
              <button className="playbtn" onClick={() => { if (!playing && year == null) setYear(years[0]); setPlaying(!playing); }}
                aria-label={playing ? "Pause" : "Play through the years"} title={playing ? "Pause" : "Play through the years"}>
                {playing ? <svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 5h4v14H7zM13 5h4v14h-4z" /></svg> : <Icon.play />}
              </button>
              <input type="range" min={0} max={years.length} value={year == null ? years.length : years.indexOf(year)}
                onChange={(e) => { setPlaying(false); const i = Number(e.target.value); setYear(i >= years.length ? null : years[i]); }}
                aria-label="Show places up to this year" />
              <span className="yearlabel">{year == null ? "All years" : `Up to ${year}`}</span>
            </div>
          )}
        </div>
        <div className="side">
          {pick ? (
            <>
              <div className="row" style={{ marginBottom: 8 }}>
                <button className="backbtn" onClick={() => { setPick(null); setNear([]); }}><span aria-hidden>←</span> Places</button>
                {pickLabel && <b>{pickLabel}</b>}
              </div>
              <div className="meta-line">
                {fmtInt(nearTotal)} items within {km(pick.radiusKm)}{nearTotal > near.length && ` (showing the newest ${near.length})`}
              </div>
              <Grid items={near} onOpen={setOpen} />
            </>
          ) : replay ? (
            <div className="triplist">
              <h2 className="display" style={{ marginTop: 0 }}>Your travels</h2>
              {trips.map((t, i) => (
                <div key={t.start} className={`tripline ${i === replay.i ? "on" : i < replay.i ? "done" : ""}`}>
                  <span className="tripdot" /><div><b>{t.title}</b><small>{fmtRange(t)}</small></div>
                </div>
              ))}
            </div>
          ) : (
            <div className="places">
              <h2 className="display" style={{ marginTop: 0 }}>Your places</h2>
              {!countries.length && <div className="empty">Photos with a location show up here, grouped by country and town.</div>}
              {countries.map((c) => (
                <div key={c.name} className="country">
                  <button className="countryhead" onClick={() => setOpenCountry(openCountry === c.name ? null : c.name)}>
                    <div className="coverstack">{c.towns.slice(0, 3).map((t) => t.cover && <img key={t.name} src={mediaUrl("thumb", t.cover)} alt="" />)}</div>
                    <div className="cname"><b>{c.name}</b><small>{c.towns.length === 1 ? c.towns[0].name : `${c.towns.length} places`}</small></div>
                    <span className="ccount">{fmtInt(c.count)}</span>
                  </button>
                  {(openCountry === c.name || countries.length <= 3) && (
                    <div className="towns">
                      {c.towns.map((t) => (
                        <button key={t.name} className="town" onClick={() => { globe.current?.flyTo(t.lat, t.lon, 60); choose({ lat: t.lat, lon: t.lon, radiusKm: 15 }, `${t.name}, ${c.name}`); }}>
                          {t.cover ? <img src={mediaUrl("thumb", t.cover)} alt="" /> : <span className="noimg" />}
                          <span>{t.name}</span><small>{fmtInt(t.count)}</small>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      {open !== null && (
        <Viewer list={near} index={open} onIndex={setOpen} onClose={() => setOpen(null)}
          onSimilar={(a, fr) => { setOpen(null); onSimilar(a, fr); }} />
      )}
    </div>
  );
}
