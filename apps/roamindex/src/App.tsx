import { useCallback, useEffect, useState } from "react";
import { api, engineInfo, fmtInt, inTauri, restartEngine, type Asset, type EngineInfo, type Filters, type LibraryStats } from "./api";
import { Icon } from "./components";
import icon from "./icon.png";
import { EvaluateView } from "./views/Evaluate";
import { LibraryView } from "./views/Library";
import { MapView } from "./views/MapView";
import { PlacesView } from "./views/Places";
import { SearchView, type SimilarReq } from "./views/Search";
import { TimelineView } from "./views/Timeline";

type View = "search" | "timeline" | "map" | "places" | "library" | "evaluate";
const NAV: [View, string, () => JSX.Element][] = [
  ["search", "Search", Icon.search], ["timeline", "Timeline", Icon.timeline], ["map", "Map", Icon.map],
  ["places", "Add places", Icon.pin],
  ["library", "Library", Icon.library], ["evaluate", "Evaluate", Icon.evaluate],
];

export function App() {
  const [engine, setEngine] = useState<EngineInfo>({ status: "starting", library: "", python: "" });
  const [stats, setStats] = useState<LibraryStats | null>(null);
  // development: ?view=&q= open a view directly when the UI runs in a plain browser
  const [view, setView] = useState<View>(() => (!inTauri() && (new URLSearchParams(location.search).get("view") as View)) || "search");
  const [similar, setSimilar] = useState<SimilarReq>(null);
  const [filters, setFilters] = useState<Filters>({});
  const [mapFocus, setMapFocus] = useState<Asset | null>(null);
  const [placeFocus, setPlaceFocus] = useState<Asset | null>(null);
  useEffect(() => {                                   // "Add location" from any viewer
    const h = (e: Event) => { setPlaceFocus({ ...(e as CustomEvent<Asset>).detail }); setView("places"); };
    window.addEventListener("roam:setplace", h);
    return () => window.removeEventListener("roam:setplace", h);
  }, []);
  const [waited, setWaited] = useState(0);

  // poll the shell until the engine handshake is ready (Python import + catalogue load take a few seconds)
  useEffect(() => {
    if (engine.status === "ready") return;
    let alive = true;
    const id = setInterval(() => {
      engineInfo().then((e) => alive && setEngine(e)).catch((e) => alive && setEngine({ status: "failed", library: "", python: "", error: String(e) }));
      setWaited((w) => w + 1);
    }, 500);
    engineInfo().then((e) => alive && setEngine(e)).catch(() => {});
    return () => { alive = false; clearInterval(id); };
  }, [engine.status]);

  const refresh = useCallback(() => { api.library().then(setStats).catch(() => {}); }, []);
  useEffect(() => { if (engine.status === "ready") refresh(); }, [engine.status, engine.port, refresh]);
  useEffect(() => { if (stats?.empty) setView("library"); }, [stats?.empty]);
  useEffect(() => {                                   // development: ?open=<asset_id> shows a viewer via similar
    const id = !inTauri() && new URLSearchParams(location.search).get("similar");
    if (id) api.asset(id).then((a) => setSimilar({ asset: a })).catch(() => {});
  }, []);

  const goSimilar = (a: Asset, frame?: number) => { setSimilar({ asset: a, frame }); setView("search"); };
  const goMap = (a: Asset) => { setMapFocus(a); setView("map"); };

  if (engine.status !== "ready") {
    return (
      <div className="splash">
        <div className="box">
          {engine.status === "failed" ? (
            <>
              <h1>The search engine didn't start</h1>
              <p className="error">{engine.error}</p>
              {engine.python && <p className="hint">Python: <span className="mono">{engine.python}</span><br />Library: <span className="mono">{engine.library}</span></p>}
              {engine.log && <p className="hint">Details: <span className="mono">{engine.log}</span></p>}
              {inTauri() && <button className="btn primary" onClick={() => restartEngine().then(setEngine)}>Try again</button>}
            </>
          ) : (
            <>
              <div className="spinner" />
              <div>Opening your library…</div>
              {waited > 16 && <p className="hint">Loading the index and the search model. Large libraries take a little longer the first time.</p>}
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="shell">
      <nav className="sidebar">
        <div className="brand"><img src={icon} alt="" />RoamIndex</div>
        {NAV.map(([v, label, I]) => (
          <button key={v} className={`nav ${view === v ? "active" : ""}`} onClick={() => { setView(v); if (v === "search") setSimilar(null); }}>
            <I />{label}
          </button>
        ))}
        <div className="spacer" />
        {stats && !stats.empty && (
          <div className="libcard">
            <b>{fmtInt(stats.assets)}</b> items<br />
            {fmtInt(stats.photos)} photos · {fmtInt(stats.videos)} videos<br />
            <span className="hint">Everything stays on this computer.</span>
          </div>
        )}
      </nav>
      <main className="main">
        {view === "search" && <SearchView similar={similar} setSimilar={setSimilar} filters={filters} setFilters={setFilters} onShowOnMap={goMap} />}
        {view === "timeline" && <TimelineView onSimilar={goSimilar} onShowOnMap={goMap} />}
        {view === "map" && <MapView focus={mapFocus} onSimilar={goSimilar} />}
        {view === "places" && <PlacesView focus={placeFocus} />}
        {view === "library" && <LibraryView stats={stats} refresh={refresh} engine={engine} onEngine={setEngine} />}
        {view === "evaluate" && <EvaluateView />}
      </main>
    </div>
  );
}
