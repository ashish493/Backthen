import { useCallback, useEffect, useState } from "react";
import { api, engineInfo, fmtInt, inTauri, restartEngine, type Asset, type EngineInfo, type Filters, type LibraryStats } from "./api";
import { Icon } from "./components";
import { useRotating } from "./nostalgia";
import icon from "./icon.png";
import { EvaluateView } from "./views/Evaluate";
import { LibraryView } from "./views/Library";
import { MapView } from "./views/MapView";
import { Onboarding } from "./views/Onboarding";
import { PlacesView } from "./views/Places";
import { SearchView, type SimilarReq } from "./views/Search";
import { TimelineView } from "./views/Timeline";

type View = "search" | "timeline" | "map" | "places" | "library" | "evaluate";
const NAV: [string, [View, string, () => JSX.Element][]][] = [
  ["Explore", [["search", "Search", Icon.search], ["timeline", "Timeline", Icon.timeline], ["map", "Map", Icon.map]]],
  ["Organise", [["places", "Add places", Icon.pin], ["library", "Library", Icon.library], ["evaluate", "Evaluate", Icon.evaluate]]],
];

// ---------------------------------------------------------------- theme: follow Windows, or a fixed light/dark choice
type Theme = "system" | "light" | "dark";
const THEME_KEY = "backthen.theme";
function readTheme(): Theme {
  try { const t = localStorage.getItem(THEME_KEY); return t === "light" || t === "dark" ? t : "system"; } catch { return "system"; }
}
function applyTheme(t: Theme) {
  if (t === "system") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
}
applyTheme(readTheme());                                // before the first paint, no flash

function ThemeSwitch() {
  const [t, setT] = useState<Theme>(readTheme);
  const pick = (v: Theme) => { setT(v); applyTheme(v); try { localStorage.setItem(THEME_KEY, v); } catch { /* storage unavailable */ } };
  const opts: [Theme, string, () => JSX.Element][] = [["system", "Follow Windows", Icon.monitor], ["light", "Light", Icon.sun], ["dark", "Dark", Icon.moon]];
  return (
    <div className="themeswitch" role="radiogroup" aria-label="Theme">
      {opts.map(([v, label, I]) => (
        <button key={v} role="radio" aria-checked={t === v} className={t === v ? "on" : ""} title={label} onClick={() => pick(v)}><I /></button>
      ))}
    </div>
  );
}

function SplashWords() {
  const { word, tick } = useRotating(undefined, 2200);
  return <div key={tick} className="nostalgia small">{word}…</div>;
}

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
  // first run: a full-screen onboarding until something is indexed. It stays up through the whole first indexing
  // run (the library stops being empty part-way) until the user moves on. ?onboarding=1 previews it (development).
  const [onboard, setOnboard] = useState<boolean | null>(() => (!inTauri() && new URLSearchParams(location.search).has("onboarding")) || null);
  useEffect(() => { if (stats && onboard === null) setOnboard(!!stats.empty); }, [stats, onboard]);
  useEffect(() => {                                   // development: ?open=<asset_id> shows a viewer via similar
    const id = !inTauri() && new URLSearchParams(location.search).get("similar");
    if (id) api.asset(id).then((a) => setSimilar({ asset: a })).catch(() => {});
  }, []);

  // "Search" in the sidebar always returns to the home screen: no query, no filters (remounting clears the box)
  const [homeKey, setHomeKey] = useState(0);
  const goHome = () => { setSimilar(null); setFilters({}); setHomeKey((k) => k + 1); };
  const goSimilar = (a: Asset, frame?: number) => { setSimilar({ asset: a, frame }); setView("search"); };
  const goMap = (a: Asset) => { setMapFocus(a); setView("map"); };

  if (engine.status !== "ready") {
    return (
      <div className="splash">
        <div className="box">
          <img className="splashlogo" src={icon} alt="" />
          <div className="splashname">Backthen</div>
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
              <div className="loadbar"><div /></div>
              <SplashWords />
              {waited > 16 && <p className="hint">Loading the index and the search model. Large libraries take a little longer the first time.</p>}
            </>
          )}
        </div>
      </div>
    );
  }

  if (onboard) {
    return <Onboarding library={stats?.library || engine.library}
      onDone={() => { refresh(); setOnboard(false); setView("search"); }}
      onSkip={() => { setOnboard(false); setView("library"); }} />;
  }

  return (
    <div className="shell">
      <nav className="sidebar">
        <div className="brand">
          <img src={icon} alt="" />
          <div><div className="brandname">Backthen</div><div className="brandtag">your photos, in your words</div></div>
        </div>
        {NAV.map(([group, items]) => (
          <div key={group} className="navgroup">
            <div className="navlabel">{group}</div>
            {items.map(([v, label, I]) => (
              <button key={v} className={`nav ${view === v ? "active" : ""}`} onClick={() => { setView(v); if (v === "search") goHome(); }}>
                <I />{label}
              </button>
            ))}
          </div>
        ))}
        <div className="spacer" />
        {stats && !stats.empty && (
          <div className="libcard">
            <div className="libnums"><b>{fmtInt(stats.photos)}</b> photos · <b>{fmtInt(stats.videos)}</b> videos</div>
            <div className="private"><Icon.lock />Private, on this computer</div>
          </div>
        )}
        <ThemeSwitch />
      </nav>
      <main className="main">
        {view === "search" && <SearchView key={homeKey} onHome={goHome} similar={similar} setSimilar={setSimilar} filters={filters} setFilters={setFilters} onShowOnMap={goMap} />}
        {view === "timeline" && <TimelineView onSimilar={goSimilar} onShowOnMap={goMap} />}
        {view === "map" && <MapView focus={mapFocus} onSimilar={goSimilar} />}
        {view === "places" && <PlacesView focus={placeFocus} />}
        {view === "library" && <LibraryView stats={stats} refresh={refresh} engine={engine} onEngine={setEngine} />}
        {view === "evaluate" && <EvaluateView />}
      </main>
    </div>
  );
}
