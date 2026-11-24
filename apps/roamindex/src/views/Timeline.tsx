import { useCallback, useEffect, useMemo, useState } from "react";
import { api, fmtInt, type Asset, type Filters } from "../api";
import { Grid, Sentinel, usePaged } from "../components";
import { Viewer } from "./Viewer";

type Month = { month: string; count: number };

export function TimelineView({ onSimilar, onShowOnMap }: { onSimilar: (a: Asset, frame?: number) => void; onShowOnMap: (a: Asset) => void }) {
  const [type, setType] = useState<"" | "photo" | "video">("");
  const [tl, setTl] = useState<{ months: Month[]; undated: number } | null>(null);
  const [sel, setSel] = useState<string | null>(null);          // 'YYYY-MM' | 'undated' | null
  const [open, setOpen] = useState<number | null>(null);

  useEffect(() => { api.timeline({ type }).then(setTl).catch(() => setTl({ months: [], undated: 0 })); }, [type]);

  // fill empty months so a bar's position is its time
  const months = useMemo(() => {
    if (!tl?.months.length) return [] as Month[];
    const have = new Map(tl.months.map((m) => [m.month, m.count]));
    const [y0, m0] = tl.months[0].month.split("-").map(Number);
    const [y1, m1] = tl.months[tl.months.length - 1].month.split("-").map(Number);
    const out: Month[] = [];
    let y = y0, m = m0;
    while (y < y1 || (y === y1 && m <= m1)) {
      const k = `${y}-${String(m).padStart(2, "0")}`;
      out.push({ month: k, count: have.get(k) || 0 });
      if (m === 12) { y++; m = 1; } else m++;
    }
    return out;
  }, [tl]);
  const max = Math.max(1, ...months.map((m) => m.count));

  const f: Filters = sel === "undated" ? { type, dated: "0" } : sel ? { type, from: sel, to: sel } : { type };
  const key = JSON.stringify(f);
  const load = useCallback((o: number) => api.browse(f, 120, o), [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const { items, total, busy, more } = usePaged(load, key);

  return (
    <div className="view">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1 style={{ margin: 0 }}>Timeline</h1>
        <div className="seg">
          {([["", "All"], ["photo", "Photos"], ["video", "Videos"]] as const).map(([v, l]) => (
            <button key={v} className={type === v ? "on" : ""} onClick={() => setType(v)}>{l}</button>
          ))}
        </div>
      </div>
      <div className="bars" role="list">
        {months.map((m) => (
          <button key={m.month} role="listitem" className={`bar ${sel === m.month ? "on" : ""}`}
            style={{ height: `${m.count ? Math.max(3, (100 * Math.sqrt(m.count)) / Math.sqrt(max)) : 0}%` }}
            title={`${m.month}: ${fmtInt(m.count)} items`} onClick={() => setSel(sel === m.month ? null : m.month)} />
        ))}
      </div>
      <div className="years">
        {months.map((m, i) => (
          <span key={m.month} style={{ flex: "1 0 8px", minWidth: 8 }}>{m.month.endsWith("-01") || i === 0 ? m.month.slice(0, 4) : ""}</span>
        ))}
      </div>
      <div className="row meta-line">
        {sel && sel !== "undated" && (
          <span>{new Date(sel + "-01T00:00:00Z").toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" })} · {fmtInt(total ?? 0)} items</span>
        )}
        {sel === "undated" && <span>{fmtInt(total ?? 0)} items without a date</span>}
        {!sel && <span>{fmtInt(total ?? 0)} items, newest first. Click a bar to open that month.</span>}
        {sel && <button className="chip" onClick={() => setSel(null)}>All months <span className="x">×</span></button>}
        {!sel && tl && tl.undated > 0 && (
          <button className="chip" onClick={() => setSel("undated")}>{fmtInt(tl.undated)} without a date</button>
        )}
        {busy && <span className="hint">Loading…</span>}
      </div>
      <Grid items={items} onOpen={setOpen} groupByMonth={sel !== "undated"} />
      <Sentinel onVisible={more} />
      {open !== null && (
        <Viewer list={items} index={open} onIndex={(i) => { setOpen(i); if (i > items.length - 10) more(); }} onClose={() => setOpen(null)}
          onSimilar={(a, fr) => { setOpen(null); onSimilar(a, fr); }} onShowOnMap={(a) => { setOpen(null); onShowOnMap(a); }} />
      )}
    </div>
  );
}
