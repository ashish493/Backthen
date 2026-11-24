import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { fmtDuration, mediaUrl, type Asset, type Page } from "./api";

// ---------------------------------------------------------------- icons (inline, no icon font or network)
const P = (d: string) => () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={d} /></svg>
);
export const Icon = {
  search: P("M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4"),
  timeline: P("M4 5h16M4 12h16M4 19h10M8 3v4M16 10v4M12 17v4"),
  map: P("M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2zM9 4v14M15 6v14"),
  library: P("M4 5h6v14H4zM14 5l6 1.5-3 13L11 18M10 9h0"),
  evaluate: P("M4 19V9M10 19V5M16 19v-7M22 19H2"),
  settings: P("M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM19 12l2-1-1-3-2 .2-1.3-1.3.2-2-3-1-1 2h-1.8l-1-2-3 1 .2 2L6.9 7.2 5 7 4 10l2 1v1.8l-2 1 1 3 2-.2 1.3 1.3-.2 2 3 1 1-2h1.8l1 2 3-1-.2-2 1.3-1.3 2 .2 1-3-2-1z"),
  play: () => <svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 4v16l13-8z" /></svg>,
  folder: P("M3 7h6l2 2h10v10H3z"),
  similar: P("M4 4h7v7H4zM13 13h7v7h-7zM13 4h7v7M4 13v7h7"),
  close: P("M6 6l12 12M18 6 6 18"),
  pin: P("M12 21s-6-5.5-6-11a6 6 0 0 1 12 0c0 5.5-6 11-6 11zM12 8a2 2 0 1 0 0 4 2 2 0 0 0 0-4z"),
  refresh: P("M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"),
};

// ---------------------------------------------------------------- tile + grid
export function Tile({ a, onOpen, showScore, children, className }: {
  a: Asset; onOpen: () => void; showScore?: boolean; children?: ReactNode; className?: string;
}) {
  const [broken, setBroken] = useState(false);
  const moment = a.moments?.[0]?.timestamp_s;
  return (
    <div className={`tile ${className || ""}`} role="button" tabIndex={0} onClick={onOpen}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onOpen()}
      title={a.capture_time ? new Date(a.capture_time_is_utc ? a.capture_time : a.capture_time + "Z").toLocaleString() : "No date"}>
      {a.has_thumb && !broken
        ? <img loading="lazy" decoding="async" src={mediaUrl("thumb", a.asset_id)} onError={() => setBroken(true)} alt="" />
        : <div className="noimg">{a.format?.toUpperCase() || "no preview"}</div>}
      {a.media_type === "video" && (
        <span className="badge"><Icon.play />{moment != null && a.score !== undefined ? `at ${fmtDuration(moment)}` : fmtDuration(a.duration_s)}</span>
      )}
      {showScore && a.score !== undefined && <span className="score">{a.score.toFixed(3)}</span>}
      {children}
    </div>
  );
}

/** Paged loader with infinite scroll. `load(offset)` returns one page; `key` resets the list. */
export function usePaged(load: (offset: number) => Promise<Page>, key: string) {
  const [items, setItems] = useState<Asset[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [meta, setMeta] = useState<Page | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const gen = useRef(0);
  const loading = useRef(false);

  useEffect(() => {
    const g = ++gen.current;
    setItems([]); setTotal(null); setError(null); setMeta(null);
    loading.current = true; setBusy(true);
    load(0).then((p) => { if (g === gen.current) { setItems(p.items); setTotal(p.total); setMeta(p); } })
      .catch((e) => g === gen.current && setError(String(e.message || e)))
      .finally(() => { if (g === gen.current) { loading.current = false; setBusy(false); } });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const more = useCallback(() => {
    if (loading.current || total === null || items.length >= total) return;
    const g = gen.current;
    loading.current = true; setBusy(true);
    load(items.length).then((p) => { if (g === gen.current) setItems((xs) => [...xs, ...p.items]); })
      .catch((e) => g === gen.current && setError(String(e.message || e)))
      .finally(() => { if (g === gen.current) { loading.current = false; setBusy(false); } });
  }, [items.length, total, load]);

  return { items, total, meta, busy, error, more };
}

export function Sentinel({ onVisible }: { onVisible: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver((es) => es.some((e) => e.isIntersecting) && onVisible(), { rootMargin: "800px" });
    io.observe(el);
    return () => io.disconnect();
  }, [onVisible]);
  return <div ref={ref} className="sentinel" />;
}

export function Grid({ items, onOpen, showScore, groupByMonth }: {
  items: Asset[]; onOpen: (i: number) => void; showScore?: boolean; groupByMonth?: boolean;
}) {
  const out: ReactNode[] = [];
  let last = "";
  items.forEach((a, i) => {
    if (groupByMonth) {
      const m = a.capture_time
        ? new Date(a.capture_time_is_utc ? a.capture_time : a.capture_time + "Z").toLocaleDateString(undefined, { year: "numeric", month: "long", timeZone: "UTC" })
        : "No date";
      if (m !== last) { out.push(<div key={`h${i}`} className="monthhead">{m}</div>); last = m; }
    }
    out.push(<Tile key={a.asset_id} a={a} onOpen={() => onOpen(i)} showScore={showScore} />);
  });
  return <div className="grid">{out}</div>;
}

export function Stat({ k, v, s }: { k: string; v: ReactNode; s?: ReactNode }) {
  return <div className="card"><div className="k">{k}</div><div className="v">{v}</div>{s && <div className="s">{s}</div>}</div>;
}
