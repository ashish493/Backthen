import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, fmtInt, mediaUrl, type Asset, type Filters } from "../api";
import { Grid, Icon, Sentinel, usePaged } from "../components";
import { Viewer } from "./Viewer";

export type SimilarReq = { asset: Asset; frame?: number } | null;

const EXAMPLES = ["sunset over the sea", "a temple", "street food", "mountains in fog", "a train", "people dancing", "snow"];

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

export function SearchView({ similar, setSimilar, filters, setFilters, onShowOnMap }: {
  similar: SimilarReq; setSimilar: (s: SimilarReq) => void; filters: Filters; setFilters: (f: Filters) => void;
  onShowOnMap: (a: Asset) => void;
}) {
  const initial = new URLSearchParams(location.search).get("q") || "";
  const [input, setInput] = useState(initial);
  const [q, setQ] = useState(initial);
  const [open, setOpen] = useState<number | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  // library-aware suggestions: queries with the strongest matches in this library (fixed examples as fallback)
  const [suggestions, setSuggestions] = useState<string[]>([]);
  useEffect(() => {
    api.suggestions(12).then((r) => setSuggestions(r.items.map((i) => i.text)))
      .catch(() => setSuggestions(EXAMPLES));
  }, []);
  useEffect(() => { if (similar) { setOpen(null); } }, [similar]);

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

  const submit = () => { setSimilar(null); setQ(input.trim()); };
  const mode = similar ? "similar" : q.trim() ? "search" : "browse";
  const caption = useMemo(() => {
    if (error) return <span className="error">{error}</span>;
    if (total === null) return busy ? "Searching…" : "";
    const t = meta?.timing_ms;
    if (mode === "browse") return `${fmtInt(total)} items, newest first`;
    return <>Best matches among {fmtInt(total)} items{t && <span className="hint"> · {Math.round(t.encode + t.rank)} ms</span>}
      {meta?.text !== undefined && meta.text !== q && meta.text && <span className="hint"> · searched for "{meta.text}"</span>}</>;
  }, [error, total, busy, meta, mode, q]);

  return (
    <div className="view">
      <div className="searchbar">
        <div className="searchbox">
          <Icon.search />
          <input ref={inputRef} value={input} placeholder="Describe what you're looking for — e.g. a beach at sunset, type:video year:2019"
            onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && submit()} />
          {input && <button className="btn" style={{ padding: "2px 8px", border: 0 }} onClick={() => { setInput(""); setQ(""); setSimilar(null); }}>×</button>}
        </div>
        <button className="btn primary" onClick={submit}>Search</button>
      </div>
      <FilterBar f={filters} setF={setFilters} />
      {!similar && suggestions.length > 0 && (
        <div className="suggest" aria-label="Suggested searches">
          <span className="hint">Suggested for your library:</span>
          {suggestions.map((e) => (
            <button key={e} className={`chip ${q === e ? "on" : ""}`}
              onClick={() => { setInput(e); setSimilar(null); setQ(e); }}>{e}</button>
          ))}
        </div>
      )}
      {similar && (
        <div className="row" style={{ margin: "8px 0" }}>
          <img src={similar.frame != null ? mediaUrl("thumb", similar.asset.asset_id) : mediaUrl("thumb", similar.asset.asset_id)}
            style={{ width: 44, height: 44, objectFit: "cover", borderRadius: 6 }} alt="" />
          <span>Similar to this {similar.asset.media_type}{similar.frame != null ? " moment" : ""}</span>
          <button className="chip" onClick={() => setSimilar(null)}>Back <span className="x">×</span></button>
        </div>
      )}
      <div className="meta-line">{caption}</div>
      {!busy && total === 0 && (
        <div className="empty">
          {mode === "browse" && !Object.values(filters).some(Boolean)
            ? <>The library is empty. Add a folder in <b>Library</b> to start.</>
            : <>Nothing matches. Try fewer filters or other words.</>}
        </div>
      )}
      <Grid items={items} onOpen={setOpen} showScore={mode !== "browse"} groupByMonth={mode === "browse"} />
      <Sentinel onVisible={more} />
      {open !== null && (
        <Viewer list={items} index={open} onIndex={(i) => { setOpen(i); if (i > items.length - 10) more(); }}
          onClose={() => setOpen(null)} onSimilar={(a, frame) => { setOpen(null); setSimilar({ asset: a, frame }); }}
          onShowOnMap={(a) => { setOpen(null); onShowOnMap(a); }} />
      )}
    </div>
  );
}
