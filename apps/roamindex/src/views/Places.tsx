import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, fmtInt, type Asset, type Filters, type GeoResult, type PlaceSuggestion, type RecentPlace } from "../api";
import { Sentinel, Tile, usePaged } from "../components";
import { Globe, type GlobeHandle, type GlobePoints } from "../Globe";
import { Viewer } from "./Viewer";

// Add a location by hand to photos and videos that have none (or a wrong one). Saved only in places.sqlite next to
// the index; the media files, the manifest, and every date and time stay exactly as they are.

type Tab = "missing" | "mine";

function dayOf(a: Asset) {
  if (!a.capture_time) return "No date";
  const d = new Date(a.capture_time_is_utc ? a.capture_time : a.capture_time + "Z");
  return d.toLocaleDateString(undefined, { weekday: "short", year: "numeric", month: "long", day: "numeric", ...(a.capture_time_is_utc ? {} : { timeZone: "UTC" }) });
}

function parseLatLon(s: string): [number, number] | null {
  const m = s.trim().match(/^\(?\s*(-?\d+(?:\.\d+)?)\s*°?\s*([NS])?\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*°?\s*([EW])?\s*\)?$/i);
  if (!m) return null;
  let lat = parseFloat(m[1]), lon = parseFloat(m[3]);
  if (m[2]?.toUpperCase() === "S") lat = -Math.abs(lat);
  if (m[4]?.toUpperCase() === "W") lon = -Math.abs(lon);
  return Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? [lat, lon] : null;
}

function ago(s: number) {
  const a = Math.abs(s), m = Math.round(a / 60), h = a / 3600;
  const t = a < 90 ? `${Math.round(a)} s` : m < 90 ? `${m} min` : `${h.toFixed(h < 10 ? 1 : 0)} h`;
  return s < 0 ? `${t} before this one` : `${t} after this one`;
}

const ONLINE_KEY = "roam.placeSearchOnline";
const readOnline = () => { try { return localStorage.getItem(ONLINE_KEY) === "1"; } catch { return false; } };

/** The Location field: a place name (offline gazetteer as you type; OpenStreetMap only when switched on and asked
 * for explicitly) or coordinates ("38.7223, -9.1393", applied on Enter or when leaving the field). */
function LocationField({ q, setQ, onChoose, onCoords }: {
  q: string; setQ: (s: string) => void; onChoose: (r: GeoResult) => void; onCoords: (lat: number, lon: number) => void;
}) {
  const [res, setRes] = useState<GeoResult[]>([]);
  const [osm, setOsm] = useState<GeoResult[] | null>(null);
  const [online, setOnline] = useState(readOnline);
  const [openList, setOpenList] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [hi, setHi] = useState(0);
  const [pending, setPending] = useState(false);

  useEffect(() => { api.geocode("ka").catch(() => {}); }, []);   // load the offline list before the first search

  useEffect(() => {
    setOsm(null); setErr(null); setHi(0);
    if (q.trim().length < 2 || parseLatLon(q)) { setRes([]); setPending(false); return; }
    let live = true;
    setPending(true);
    const t = setTimeout(() => api.geocode(q)
      .then((r) => { if (live) { setRes(r.results); setPending(false); } })
      .catch((e) => { if (live) { setRes([]); setPending(false); setErr(String(e.message || e)); } }), 150);
    return () => { live = false; clearTimeout(t); };
  }, [q]);

  const searchOnline = async () => {
    setBusy(true); setErr(null);
    try { setOsm((await api.geocode(q, true)).results); } catch (e) { setErr(String((e as Error).message || e)); }
    setBusy(false);
  };
  const toggleOnline = (v: boolean) => { setOnline(v); try { localStorage.setItem(ONLINE_KEY, v ? "1" : "0"); } catch { /* ignore */ } };
  const all = [...res, ...(osm || [])];
  const pick = (r: GeoResult) => { onChoose(r); setOpenList(false); };
  const coords = parseLatLon(q);

  return (
    <div className="placesearch">
      <label className="field" style={{ flex: 1 }}>Location
        <input className="input" style={{ flex: 1 }} placeholder="Type a town or city (e.g. Lisbon), paste 38.7223, -9.1393, or click the globe"
          value={q} onChange={(e) => { setQ(e.target.value); setOpenList(true); }}
          onFocus={(e) => { e.currentTarget.select(); setOpenList(true); }}
          onBlur={() => { setTimeout(() => setOpenList(false), 150); if (coords) onCoords(coords[0], coords[1]); }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { setHi((h) => Math.min(all.length - 1, h + 1)); e.preventDefault(); }
            else if (e.key === "ArrowUp") { setHi((h) => Math.max(0, h - 1)); e.preventDefault(); }
            else if (e.key === "Enter") {
              if (coords) { onCoords(coords[0], coords[1]); setOpenList(false); }
              else if (all[hi]) pick(all[hi]);
              else if (online && q.trim().length >= 2) searchOnline();
            } else if (e.key === "Escape") setOpenList(false);
          }} />
      </label>
      {openList && q.trim().length >= 2 && !coords && (
        <div className="placeresults" onMouseDown={(e) => e.preventDefault()}>
          {pending && res.length === 0 && <div className="hint" style={{ padding: "6px 10px" }}>Searching…</div>}
          {!pending && res.length === 0 && !osm && <div className="hint" style={{ padding: "6px 10px" }}>No town or city with that name in the offline list.</div>}
          {all.map((r, i) => (
            <button key={`${r.source}${r.lat}${r.lon}${i}`} className={i === hi ? "on" : ""} onMouseEnter={() => setHi(i)} onClick={() => pick(r)}>
              <span>{r.label}</span>
              <small>{r.source === "osm" ? "OpenStreetMap" : r.population ? `${fmtInt(r.population)} people` : ""}</small>
            </button>
          ))}
          {osm && osm.length === 0 && <div className="hint" style={{ padding: "6px 10px" }}>OpenStreetMap found nothing either.</div>}
          {online && !osm && (
            <button className="online" disabled={busy} onClick={searchOnline}>
              {busy ? "Searching OpenStreetMap…" : `Search OpenStreetMap for “${q.trim()}” (sends this text online)`}
            </button>
          )}
          {err && <div className="error" style={{ padding: "6px 10px" }}>{err}</div>}
          <div className="attrib">{osm ? "Results: GeoNames (CC BY 4.0) · © OpenStreetMap contributors" : "Offline place list: GeoNames (CC BY 4.0)"}</div>
        </div>
      )}
      <label className="onlinetoggle">
        <input type="checkbox" checked={online} onChange={(e) => toggleOnline(e.target.checked)} />
        <span><b>Also search online</b> (OpenStreetMap) for streets, landmarks and villages. Off: nothing leaves this
          computer. On: only the text you search for is sent, and only when you click "Search OpenStreetMap".</span>
      </label>
    </div>
  );
}

export function PlacesView({ focus }: { focus: Asset | null }) {
  const [tab, setTab] = useState<Tab>("missing");
  const [type, setType] = useState<"" | "photo" | "video">("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [gen, setGen] = useState(0);                        // bump to reload the list after a change
  const [sel, setSel] = useState<Map<string, Asset>>(new Map());
  const [anchor, setAnchor] = useState<number | null>(null);
  const [loc, setLoc] = useState<{ lat: number; lon: number } | null>(null);
  const [text, setText] = useState("");
  const [name, setName] = useState("");
  const [recent, setRecent] = useState<RecentPlace[]>([]);
  const [sugg, setSugg] = useState<PlaceSuggestion[]>([]);
  const [pts, setPts] = useState<GlobePoints | null>(null);
  const [msg, setMsg] = useState<{ text: string; undo?: () => void } | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const globe = useRef<GlobeHandle>(null);

  const filters: Filters = useMemo(() => ({
    type, from: from || undefined, to: to || undefined, ...(tab === "missing" ? { gps: "0" as const } : { manual: "1" as const }),
  }), [type, from, to, tab]);
  const key = JSON.stringify(filters) + gen;
  const load = useCallback((offset: number) => api.browse(filters, 200, offset, "desc"), [filters]);
  const { items, total, busy: loading, error, more } = usePaged(load, key);

  const refreshSide = useCallback(() => {
    api.points({}).then(setPts).catch(() => {});
    api.recentPlaces().then((r) => setRecent(r.recent)).catch(() => {});
  }, []);
  useEffect(refreshSide, [refreshSide]);

  // opened from a viewer ("Add location" / "Change location"): select that item
  useEffect(() => {
    if (!focus) return;
    setSel(new Map([[focus.asset_id, focus]]));
    if (focus.lat != null && focus.lon != null) { setTab("mine"); choose(focus.lat, focus.lon); }
  }, [focus?.asset_id]); // eslint-disable-line react-hooks/exhaustive-deps

  // suggestions: where items taken around the same time as the first selected one were
  const first = sel.values().next().value as Asset | undefined;
  useEffect(() => {
    setSugg([]);
    if (first) api.placeSuggestions(first.asset_id).then((r) => setSugg(r.suggestions)).catch(() => {});
  }, [first?.asset_id]); // eslint-disable-line react-hooks/exhaustive-deps


  const choose = (lat: number, lon: number, fly = false, nm?: string | null, label?: string) => {
    setLoc({ lat, lon });
    setText(label ?? `${lat.toFixed(5)}, ${lon.toFixed(5)}`);
    if (nm !== undefined) setName(nm || "");
    if (fly) globe.current?.flyTo(lat, lon, label ? 12 : 300);    // a named place: keep the region in view
  };

  const toggle = (i: number, e: React.MouseEvent | React.KeyboardEvent) => {
    const a = items[i];
    setSel((s) => {
      const n = new Map(s);
      if (e.shiftKey && anchor !== null) {                  // shift-click: select the range
        const [lo, hi] = anchor < i ? [anchor, i] : [i, anchor];
        for (let j = lo; j <= hi; j++) n.set(items[j].asset_id, items[j]);
      } else if (n.has(a.asset_id)) n.delete(a.asset_id);
      else n.set(a.asset_id, a);
      return n;
    });
    setAnchor(i);
  };

  const selectDay = (day: string) => setSel((s) => {
    const n = new Map(s);
    const inDay = items.filter((a) => dayOf(a) === day);
    const all = inDay.every((a) => n.has(a.asset_id));
    for (const a of inDay) all ? n.delete(a.asset_id) : n.set(a.asset_id, a);
    return n;
  });

  const apply = async () => {
    if (!loc || !sel.size) return;
    const ids = [...sel.keys()];
    // remember what these items had before, so Undo puts back exactly that
    const prev = [...sel.values()];
    setBusy(true);
    try {
      const r = await api.setPlace(ids, loc.lat, loc.lon, name || undefined);
      setSel(new Map()); setGen((g) => g + 1); refreshSide();
      setMsg({
        text: `Added the location to ${fmtInt(r.updated)} item${r.updated === 1 ? "" : "s"}.`,
        undo: async () => {
          const hadManual = tab === "mine" ? prev.filter((a) => a.lat != null) : [];
          await api.clearPlace(ids);
          // items that already had a location set by hand get it back
          for (const a of hadManual) await api.setPlace([a.asset_id], a.lat!, a.lon!);
          setGen((g) => g + 1); refreshSide(); setMsg({ text: "Undone." });
        },
      });
    } catch (e) { setMsg({ text: String((e as Error).message || e) }); }
    setBusy(false);
  };

  const remove = async () => {
    const ids = [...sel.keys()];
    const prev = [...sel.values()];
    setBusy(true);
    try {
      const r = await api.clearPlace(ids);
      setSel(new Map()); setGen((g) => g + 1); refreshSide();
      setMsg({
        text: `Removed the location you added from ${fmtInt(r.cleared)} item${r.cleared === 1 ? "" : "s"}.`,
        undo: async () => {
          for (const a of prev) if (a.lat != null) await api.setPlace([a.asset_id], a.lat, a.lon!);
          setGen((g) => g + 1); refreshSide(); setMsg({ text: "Undone." });
        },
      });
    } catch (e) { setMsg({ text: String((e as Error).message || e) }); }
    setBusy(false);
  };

  // day-grouped grid with selection
  const grid: ReactNode[] = [];
  let last = "";
  items.forEach((a, i) => {
    const d = dayOf(a);
    if (d !== last) {
      const day = d;
      const n = items.filter((x) => dayOf(x) === day);
      const all = n.every((x) => sel.has(x.asset_id));
      grid.push(
        <div key={`h${i}`} className="monthhead dayhead">
          <span>{d}</span>
          {n.length > 1 && <button className="btn small" onClick={() => selectDay(day)}>{all ? "Unselect day" : `Select day (${n.length})`}</button>}
        </div>);
      last = d;
    }
    const on = sel.has(a.asset_id);
    grid.push(
      <Tile key={a.asset_id} a={a} className={`pick ${on ? "on" : ""}`} onOpen={() => {}}>
        <button className="tilehit" aria-label={on ? "Unselect" : "Select"} onClick={(e) => toggle(i, e)} />
        <span className="check" aria-hidden>{on ? "✓" : ""}</span>
        <button className="peek" title="View larger" aria-label="View larger" onClick={(e) => { e.stopPropagation(); setOpen(i); }}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 4h6M4 4v6M20 20h-6M20 20v-6M4 4l6 6M20 20l-6-6" /></svg>
        </button>
      </Tile>);
  });

  const n = sel.size;

  return (
    <div className="view">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6, flexWrap: "wrap", gap: 10 }}>
        <h1 style={{ margin: 0 }}>Add places</h1>
        <div className="seg">
          <button className={tab === "missing" ? "on" : ""} onClick={() => { setTab("missing"); setSel(new Map()); }}>Without a location</button>
          <button className={tab === "mine" ? "on" : ""} onClick={() => { setTab("mine"); setSel(new Map()); }}>Added by you</button>
        </div>
      </div>
      <p className="hint" style={{ margin: "0 0 10px" }}>
        Locations you add are saved only in this app, in a separate file next to your index. Your photos and videos,
        and their original dates and times, are never changed. Remove a location here at any time.
      </p>
      <div className="placeswrap">
        <section className="placelist">
          <div className="filters" style={{ marginBottom: 8 }}>
            <div className="seg">
              {([["", "All"], ["photo", "Photos"], ["video", "Videos"]] as const).map(([v, l]) => (
                <button key={v} className={type === v ? "on" : ""} onClick={() => setType(v)}>{l}</button>
              ))}
            </div>
            <label className="field">From <input className="input" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
            <label className="field">to <input className="input" type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
            <span className="hint">{total === null ? "Loading…" : `${fmtInt(total)} item${total === 1 ? "" : "s"}`}{n > 0 && ` · ${fmtInt(n)} selected`}</span>
            {n > 0 && <button className="btn small" onClick={() => setSel(new Map())}>Clear selection</button>}
          </div>
          <div className="placescroll">
            {error && <p className="error">{error}</p>}
            {total === 0 && !loading && (
              <div className="empty">{tab === "missing" ? "Every item in this range has a location." : "You haven't added any locations yet."}</div>
            )}
            <div className="grid">{grid}</div>
            <Sentinel onVisible={more} />
          </div>
        </section>

        <section className="placeside">
          <div className="map placemap">
            <Globe ref={globe} points={pts} marker={loc} clusterZoom={false} onPick={(p) => choose(p.lat, p.lon)} />
            <div className="mapctl">
              <button onClick={() => globe.current?.zoomBy(2)} aria-label="Zoom in">+</button>
              <button onClick={() => globe.current?.zoomBy(0.5)} aria-label="Zoom out">−</button>
              <button onClick={() => globe.current?.fit()} aria-label="Fit to my places" title="Fit to my places">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" /><circle cx="12" cy="12" r="2.5" /></svg>
              </button>
            </div>
            <div className="legend">Click the globe to drop the pin · dots are items that already have a location</div>
          </div>

          <div className="panel placeform">
            <LocationField q={text} setQ={setText} onChoose={(r) => choose(r.lat, r.lon, true, r.name, r.label)}
              onCoords={(la, lo) => choose(la, lo, true)} />
            <div className="hint">{loc ? <>Pin: {loc.lat.toFixed(5)}, {loc.lon.toFixed(5)}</> : "No pin yet."}</div>
            <label className="field">Name <input className="input" style={{ flex: 1 }} placeholder="optional, e.g. Lisbon" maxLength={120}
              value={name} onChange={(e) => setName(e.target.value)} /></label>

            {sugg.length > 0 && (
              <div className="stack" style={{ gap: 4 }}>
                <div className="hint">Taken around the same time:</div>
                {sugg.map((s) => (
                  <button key={s.asset_id} className="chip" onClick={() => choose(s.lat, s.lon, true, s.name)}
                    title={`${s.lat.toFixed(5)}, ${s.lon.toFixed(5)}`}>
                    {s.name || `${s.lat.toFixed(3)}, ${s.lon.toFixed(3)}`} · {ago(s.dt_s)}
                  </button>
                ))}
              </div>
            )}
            {recent.length > 0 && (
              <div className="suggest" style={{ margin: 0 }}>
                <span className="hint">Recent:</span>
                {recent.map((r) => (
                  <button key={`${r.name}${r.lat}${r.lon}`} className="chip" onClick={() => choose(r.lat, r.lon, true, r.name)}
                    title={`${r.lat}, ${r.lon} · ${r.count} item${r.count === 1 ? "" : "s"}`}>
                    {r.name || `${r.lat.toFixed(3)}, ${r.lon.toFixed(3)}`}
                  </button>
                ))}
              </div>
            )}

            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
              <button className="btn primary" disabled={!loc || !n || busy} onClick={apply}>
                {n ? `Set location for ${fmtInt(n)} item${n === 1 ? "" : "s"}` : "Select items on the left"}
              </button>
              {tab === "mine" && n > 0 && <button className="btn" disabled={busy} onClick={remove}>Remove the location I added</button>}
            </div>
            {msg && (
              <div className="hint" role="status">
                {msg.text} {msg.undo && <button className="btn small" onClick={() => { const u = msg.undo!; setMsg(null); u(); }}>Undo</button>}
              </div>
            )}
          </div>
        </section>
      </div>
      {open !== null && (
        <Viewer list={items} index={open} onIndex={setOpen} onClose={() => setOpen(null)} onSimilar={() => setOpen(null)} />
      )}
    </div>
  );
}
