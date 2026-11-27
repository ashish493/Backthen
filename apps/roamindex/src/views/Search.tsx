import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, fmtInt, mediaUrl, type Asset, type Filters } from "../api";
import { Grid, Icon, Sentinel, SkeletonGrid, usePaged } from "../components";
import { Memories, OnThisDay, WorldStrip, YearsStrip, useJourneys, type MemoryOpen } from "./Memories";
import { Relive } from "./Relive";
import { YearReview } from "./YearReview";
import { Viewer } from "./Viewer";

export type SimilarReq = { asset: Asset; frame?: number } | null;

const EXAMPLES = ["sunset over the sea", "a temple", "street food", "mountains in fog", "a train", "people dancing", "snow"];
const PAUSE_MS = 400;                                  // search when typing pauses this long
const RECENT_KEY = "backthen.recentSearches";          // kept in this computer's browser storage only
const RECENT_MAX = 8;

function loadRecent(): string[] {
  try { const v = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); return Array.isArray(v) ? v.filter((s) => typeof s === "string") : []; }
  catch { return []; }
}
function saveRecent(xs: string[]) { try { localStorage.setItem(RECENT_KEY, JSON.stringify(xs)); } catch { /* storage unavailable */ } }

const monthLabel = (m: string) => {
  const [y, mo] = m.split("-").map(Number);
  return new Date(Date.UTC(y, mo - 1, 1)).toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
};

/** Decoration for the home hero: a dotted flight route with a small plane gliding along it, over a faint map-dot field. */
function FlightPath() {
  const still = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const d = "M 40 190 C 160 60, 320 40, 430 110 S 650 170, 770 50";
  return (
    <svg className="flightpath" viewBox="0 0 800 220" preserveAspectRatio="xMaxYMid meet" aria-hidden>
      <path className="route" d={d} />
      <circle className="town" cx="40" cy="190" r="4" /><circle className="town" cx="430" cy="110" r="4" /><circle className="town" cx="770" cy="50" r="4" />
      <g className="plane">
        <path d="M14 0 L4 -2.4 L-2 -12 L-5.2 -12 L-2 -2.4 L-10 -2 L-13 -6 L-14.8 -6 L-12.8 0 L-14.8 6 L-13 6 L-10 2 L-2 2.4 L-5.2 12 L-2 12 L4 2.4 Z" />
        {!still && <animateMotion dur="14s" repeatCount="indefinite" rotate="auto" path={d} keyPoints="0;1" keyTimes="0;1" calcMode="spline" keySplines="0.45 0 0.55 1" />}
      </g>
    </svg>
  );
}

const greeting = () => {
  const h = new Date().getHours();
  return h < 5 ? "Good night" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
};

export function FilterBar({ f, setF }: { f: Filters; setF: (f: Filters) => void }) {
  const [from, setFrom] = useState(f.from || "");
  const [to, setTo] = useState(f.to || "");
  useEffect(() => { setFrom(f.from || ""); setTo(f.to || ""); }, [f.from, f.to]);
  const ok = (s: string) => !s || /^\d{4}(-\d{2}(-\d{2})?)?$/.test(s);
  const commit = () => ok(from) && ok(to) && setF({ ...f, from, to });
  return (
    <div className="filters">
      <div className="seg">
        {([["", "All"], ["photo", "Photos"], ["video", "Videos"]] as const).map(([v, l]) => (
          <button key={v} className={(f.type || "") === v ? "on" : ""} onClick={() => setF({ ...f, type: v })}>{l}</button>
        ))}
      </div>
      <label className="field">From
        <input type="text" placeholder="YYYY or YYYY-MM" value={from} onChange={(e) => setFrom(e.target.value)}
          onBlur={commit} onKeyDown={(e) => e.key === "Enter" && commit()} style={ok(from) ? {} : { borderColor: "var(--bad)" }} />
      </label>
      <label className="field">To
        <input type="text" placeholder="YYYY-MM-DD" value={to} onChange={(e) => setTo(e.target.value)}
          onBlur={commit} onKeyDown={(e) => e.key === "Enter" && commit()} style={ok(to) ? {} : { borderColor: "var(--bad)" }} />
      </label>
      <button className={`chip ${f.gps ? "on" : ""}`} onClick={() => setF({ ...f, gps: !f.gps })}><Icon.pin />Has location</button>
      {f.lat != null && (
        <button className="chip on" onClick={() => setF({ ...f, lat: undefined, lon: undefined, radius_km: undefined })}>
          Within {f.radius_km} km of {f.lat.toFixed(2)}, {f.lon?.toFixed(2)} <span className="x">×</span>
        </button>
      )}
      {(f.type || f.from || f.to || f.gps || f.lat != null) && (
        <button className="chip" onClick={() => setF({})}>Clear filters</button>
      )}
    </div>
  );
}

export function SearchView({ similar, setSimilar, filters, setFilters, onShowOnMap, onHome }: {
  onHome: () => void;
  similar: SimilarReq; setSimilar: (s: SimilarReq) => void; filters: Filters; setFilters: (f: Filters) => void;
  onShowOnMap: (a: Asset) => void;
}) {
  const [initial] = useState(() => {                   // development: ?q= applies to the first visit only
    const v = new URLSearchParams(location.search).get("q") || "";
    if (v) history.replaceState(null, "", location.pathname + location.search.replace(/([?&])q=[^&]*&?/, "$1"));
    return v;
  });
  const [input, setInput] = useState(initial);
  const [q, setQ] = useState(initial);
  const [open, setOpen] = useState<number | null>(null);
  const [recent, setRecent] = useState<string[]>(loadRecent);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  // Ctrl+K or "/" focuses the search box from anywhere in this view (not while typing in another field)
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (document.querySelector(".overlay")) return;            // the viewer is open
      const typing = e.target instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
      if ((e.key.toLowerCase() === "k" && (e.ctrlKey || e.metaKey)) || (e.key === "/" && !typing)) {
        e.preventDefault(); inputRef.current?.focus(); inputRef.current?.select();
      }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);

  // library-aware suggestions: queries with the strongest matches in this library (fixed examples as fallback)
  const [suggestions, setSuggestions] = useState<string[]>([]);
  useEffect(() => {
    api.suggestions(12).then((r) => setSuggestions(r.items.map((i) => i.text)))
      .catch(() => setSuggestions(EXAMPLES));
  }, []);
  useEffect(() => { if (similar) { setOpen(null); } }, [similar]);

  // search as you pause typing
  useEffect(() => {
    const t = input.trim();
    if (t === q) return;
    const id = setTimeout(() => { setSimilar(null); setQ(t); }, PAUSE_MS);
    return () => clearTimeout(id);
  }, [input]); // eslint-disable-line react-hooks/exhaustive-deps

  const remember = (t: string) => {
    if (!t) return;
    const xs = [t, ...recent.filter((r) => r.toLowerCase() !== t.toLowerCase())].slice(0, RECENT_MAX);
    setRecent(xs); saveRecent(xs);
  };
  const run = (t: string) => { setInput(t); setSimilar(null); setQ(t.trim()); remember(t.trim()); };
  const submit = () => run(input);

  const key = JSON.stringify([q, filters, similar?.asset.asset_id, similar?.frame]);
  const load = useCallback((offset: number) => {
    if (similar) return api.similar(similar.asset.asset_id, filters, 60, offset, similar.frame);
    if (q.trim()) return api.search(q, filters, 60, offset);
    return api.browse(filters, 120, offset);
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const { items, total, meta, busy, error, more } = usePaged(load, key);
  useEffect(() => {                                   // development: ?open=<index> opens the viewer
    const o = new URLSearchParams(location.search).get("open");
    if (o !== null && items.length > Number(o) && open === null) setOpen(Number(o));
  }, [items.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const journeys = useJourneys();
  const [opened, setOpened] = useState<MemoryOpen | null>(null);
  const [yearView, setYearView] = useState<{ year: number; all: number[] } | null>(null);
  const [relive, setRelive] = useState<string | null>(null);
  const openMemory = (m: MemoryOpen) => { setYearView(null); setOpened(m); setFilters({ from: m.from, to: m.to }); };
  const [moreChips, setMoreChips] = useState(false);
  const mode = similar ? "similar" : q.trim() ? "search" : "browse";
  const filtered = Object.values(filters).some((v) => v !== undefined && v !== "" && v !== false);
  const home = mode === "browse" && !filtered;
  // a Memories card opened: exactly one month, nothing else
  const datesOnly = mode === "browse"
    && Object.entries(filters).every(([k, v]) => k === "from" || k === "to" || v === undefined || v === "" || v === false);
  const memoryLabel = !datesOnly ? null
    : opened && filters.from === opened.from && filters.to === opened.to ? opened.label
    : filters.from && filters.from === filters.to && /^\d{4}-\d{2}$/.test(filters.from) ? monthLabel(filters.from) : null;
  const caption = useMemo(() => {
    if (error) return <span className="error">{error}</span>;
    if (total === null) return busy ? "Searching…" : "";
    const t = meta?.timing_ms;
    if (mode === "browse") return `${fmtInt(total)} items, newest first`;
    return <>Best matches among {fmtInt(total)} items{t && <span className="hint"> · {Math.round(t.encode + t.rank)} ms</span>}
      {meta?.text !== undefined && meta.text !== q && meta.text && <span className="hint"> · searched for "{meta.text}"</span>}</>;
  }, [error, total, busy, meta, mode, q]);

  const searchbar = (
    <div className="searchbar">
      <div className="searchbox">
        <Icon.search />
        <input ref={inputRef} value={input} aria-label="Search"
          placeholder={home ? "Describe a moment — a beach at sunset, horses in a field…" : "Describe what you're looking for — e.g. a beach at sunset, type:video year:2019"}
          onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && submit()} />
        {input
          ? <button className="clear" aria-label="Clear search" onClick={() => { setInput(""); setQ(""); setSimilar(null); }}><Icon.close /></button>
          : <kbd title="Focus search from anywhere">Ctrl K</kbd>}
      </div>
      {!home && <button className="btn primary" onClick={submit}>Search</button>}
    </div>
  );

  // on the home screen: one short row each, the rest behind "more"
  const nRecent = home && !moreChips ? 4 : RECENT_MAX, nTry = home && !moreChips ? 6 : 50;
  const hidden = Math.max(0, recent.length - nRecent) + Math.max(0, suggestions.length - nTry);
  const chips = !similar && (
    <div className="suggest-wrap">
      {recent.length > 0 && (
        <div className="suggest" aria-label="Recent searches">
          <span className="hint">Recent</span>
          {recent.slice(0, nRecent).map((e) => (
            <button key={e} className={`chip recent ${q === e ? "on" : ""}`} onClick={() => run(e)}><Icon.clock />{e}</button>
          ))}
          <button className="linkbtn" onClick={() => { setRecent([]); saveRecent([]); }}>Clear</button>
        </div>
      )}
      {suggestions.length > 0 && (
        <div className="suggest" aria-label="Suggested searches">
          <span className="hint">Try</span>
          {suggestions.slice(0, nTry).map((e) => (
            <button key={e} className={`chip ${q === e ? "on" : ""}`} onClick={() => run(e)}>{e}</button>
          ))}
          {home && (hidden > 0 || moreChips) && (
            <button className="linkbtn" onClick={() => setMoreChips(!moreChips)}>{moreChips ? "Fewer" : `+${hidden} more`}</button>
          )}
        </div>
      )}
    </div>
  );

  const results = (
    <>
      {!busy && total === 0 && (
        <div className="empty">
          {mode === "browse" && !filtered
            ? <>The library is empty. Add a folder in <b>Library</b> to start.</>
            : <>Nothing matches. Try fewer filters or other words.</>}
        </div>
      )}
      {busy && items.length === 0 ? <SkeletonGrid /> : (
        <Grid items={items} onOpen={setOpen} showScore={mode !== "browse"} groupByMonth={mode === "browse"} />
      )}
      <Sentinel onVisible={more} />
    </>
  );

  return (
    <div className="view">
      {home ? (
        <>
          <section className="hero">
            <FlightPath />
            <div className="eyebrow">{greeting()}</div>
            <h1 className="display">What would you like to find?</h1>
            <p className="lede">Search {total ? fmtInt(total) : "your"} photos and videos in your own words. Everything stays on this computer.</p>
            {searchbar}
            {chips}
          </section>
          <WorldStrip journeys={journeys} />
          <OnThisDay onOpen={openMemory} />
          <Memories onOpen={openMemory} />
          <YearsStrip onPick={(y, all) => setYearView({ year: y, all })} />
          <div className="sectionhead">
            <h2 className="display">All photos and videos</h2>
            <FilterBar f={filters} setF={setFilters} />
          </div>
          {results}
        </>
      ) : (
        <>
          {!home && (
            <div className="backrow">
              <button className="backbtn" onClick={onHome}><span aria-hidden>←</span> Home</button>
              {memoryLabel && <span className="backtitle">Memories · <b>{memoryLabel}</b></span>}
              {memoryLabel && opened?.trip && opened.from === filters.from &&
                <button className="btn primary" onClick={() => setRelive(opened.trip!)}><Icon.play /> Relive this trip</button>}
            </div>
          )}
          {searchbar}
          <FilterBar f={filters} setF={setFilters} />
          {chips}
          {similar && (
            <div className="row" style={{ margin: "8px 0" }}>
              <img src={mediaUrl("thumb", similar.asset.asset_id)}
                style={{ width: 44, height: 44, objectFit: "cover", borderRadius: 8 }} alt="" />
              <span>Similar to this {similar.asset.media_type}{similar.frame != null ? " moment" : ""}</span>
              <button className="chip" onClick={() => setSimilar(null)}>Back <span className="x">×</span></button>
            </div>
          )}
          <div className="meta-line">{caption}</div>
          {results}
        </>
      )}
      {yearView && createPortal(
        <YearReview year={yearView.year} years={yearView.all} onYear={(y) => setYearView({ ...yearView, year: y })}
          onClose={() => setYearView(null)} onOpen={openMemory} onRelive={setRelive} />, document.body)}
      {relive && createPortal(<Relive start={relive} onClose={() => setRelive(null)} />, document.body)}
      {open !== null && (
        <Viewer list={items} index={open} onIndex={(i) => { setOpen(i); if (i > items.length - 10) more(); }}
          onClose={() => setOpen(null)} onSimilar={(a, frame) => { setOpen(null); setSimilar({ asset: a, frame }); }}
          onShowOnMap={(a) => { setOpen(null); onShowOnMap(a); }} />
      )}
    </div>
  );
}
