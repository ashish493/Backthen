import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api, fmtInt, mediaUrl, type Asset, type YearReview as YR } from "../api";
import { Icon } from "../components";
import type { MemoryOpen } from "./Memories";
import { MemoryPhotos } from "./Polaroid";
import { Viewer } from "./Viewer";

const MONTHS = Array.from({ length: 12 }, (_, m) =>
  new Date(Date.UTC(2000, m, 1)).toLocaleDateString(undefined, { month: "short", timeZone: "UTC" }));
const dayName = (d: string) => new Date(d + "T00:00:00Z").toLocaleDateString(undefined, { day: "numeric", month: "long", timeZone: "UTC" });
const range = (a: string, b: string) => a === b ? dayName(a) : `${dayName(a)} – ${dayName(b)}`;

/** One year at a glance: totals, the months, trips (each can be relived), the places, and the best photos. */
export function YearReview({ year, years, onYear, onClose, onOpen, onRelive }: {
  year: number; years: number[]; onYear: (y: number) => void; onClose: () => void;
  onOpen: (m: MemoryOpen) => void; onRelive: (start: string) => void;
}) {
  const [r, setR] = useState<YR | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [best, setBest] = useState<Asset[]>([]);
  const [open, setOpen] = useState<number | null>(null);

  useEffect(() => {
    setR(null); setErr(null); setBest([]);
    api.year(year).then((y) => {
      setR(y);
      Promise.all(y.best.map((id) => api.asset(id).catch(() => null))).then((a) => setBest(a.filter((x) => x !== null) as Asset[]));
    }).catch((e) => setErr(String(e.message || e)));
  }, [year]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape" && open === null) onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [open, onClose]);

  const pos = years.indexOf(year);
  const older = pos >= 0 && pos < years.length - 1 ? years[pos + 1] : null;
  const newer = pos > 0 ? years[pos - 1] : null;
  const peak = r ? Math.max(1, ...r.months) : 1;
  const mm = (m: number) => `${year}-${String(m + 1).padStart(2, "0")}`;

  return (
    <div className="yearpage" role="dialog" aria-label={`${year} in review`}>
      <div className="yearinner">
        <div className="yearhead">
          <button className="backbtn" onClick={onClose}><span aria-hidden>←</span> Home</button>
          <div className="yearnav">
            <button className="btn" disabled={older === null} onClick={() => older !== null && onYear(older)} aria-label="Earlier year">‹ {older ?? ""}</button>
            <button className="btn" disabled={newer === null} onClick={() => newer !== null && onYear(newer)} aria-label="Later year">{newer ?? ""} ›</button>
          </div>
        </div>
        <div className="eyebrow">Year in review</div>
        <h1 className="display yeartitle">{year}</h1>
        {err && <p className="error">{err}</p>}
        {!r && !err && <p className="hint">Looking back…</p>}
        {r && (
          <>
            <div className="worldtiles yeartiles">
              {([
                ["Photos", fmtInt(r.photos), undefined],
                ["Videos", fmtInt(r.videos), undefined],
                ["Days", fmtInt(r.days), "with photos"],
                ["Places", fmtInt(r.n_places), r.new_places ? `${r.new_places} new` : undefined],
                ["Countries", fmtInt(r.countries.length), r.new_countries.length ? `${r.new_countries.length} new` : undefined],
              ] as [string, string, string | undefined][]).map(([k, v, s], i) => (
                <div key={k} className="wtile" style={{ ["--i" as string]: i }}><div className="wv">{v}</div><div className="wk">{k}</div>{s && <div className="ws">{s}</div>}</div>
              ))}
            </div>

            <h2 className="display">Month by month</h2>
            <div className="monthbars">
              {r.months.map((c, m) => (
                <button key={m} className={`mbar ${c === peak ? "peak" : ""}`} disabled={!c} title={`${MONTHS[m]}: ${fmtInt(c)} items`}
                  onClick={() => onOpen({ from: mm(m), to: mm(m), label: `${MONTHS[m]} ${year}` })}>
                  <span className="mfill" style={{ height: `${(100 * c) / peak}%` }} />
                  <span className="mlab">{MONTHS[m]}</span>
                </button>
              ))}
            </div>
            <p className="hint">
              Busiest day: <button className="linkbtn" onClick={() => onOpen({ from: r.busiest.day, to: r.busiest.day, label: dayName(r.busiest.day) })}>
                {dayName(r.busiest.day)}</button>, {fmtInt(r.busiest.count)} photos and videos.
            </p>

            {r.trips.length > 0 && (
              <>
                <h2 className="display">Trips</h2>
                <div className="memories">
                  {r.trips.map((t, i) => (
                    <div key={t.start} className="postcard trip yeartrip" style={{ ["--tilt" as string]: `${[-1.2, 1, -0.6][i % 3]}deg`, ["--i" as string]: i }}>
                      <button className="plain" onClick={() => onOpen({ from: t.start, to: t.end, label: t.title, trip: t.start })} title={`Show ${t.title}`}>
                        <MemoryPhotos ids={t.cover} index={i} />
                        <div className="postmeta">
                          <b className="posttitle">{t.title}</b>
                          <small>{range(t.start, t.end)} · {t.days} days · {fmtInt(t.count)} items</small>
                        </div>
                      </button>
                      <button className="btn primary relivebtn" onClick={() => onRelive(t.start)}><Icon.play /> Relive</button>
                    </div>
                  ))}
                </div>
              </>
            )}

            {r.places.length > 0 && (
              <>
                <h2 className="display">Places</h2>
                <div className="yearplaces">
                  {r.places.map((p) => (
                    <div key={`${p.name}|${p.country}`} className="yplace">
                      {p.cover ? <img src={mediaUrl("thumb", p.cover)} alt="" loading="lazy" /> : <span className="noimg" />}
                      <div><b>{p.name}</b>{p.new && <span className="memtag ago">New</span>}<small>{p.country} · {fmtInt(p.count)} {p.count === 1 ? "item" : "items"}</small></div>
                    </div>
                  ))}
                </div>
              </>
            )}

            {best.length > 0 && (
              <>
                <h2 className="display">The best of {year}</h2>
                <div className="yearbest">
                  {best.map((a, i) => (
                    <button key={a.asset_id} className="plain" onClick={() => setOpen(i)} title="Open">
                      <img src={mediaUrl("thumb", a.asset_id)} alt="" loading="lazy" />
                    </button>
                  ))}
                </div>
              </>
            )}
          </>
        )}
      </div>
      {open !== null && createPortal(<Viewer list={best} index={open} onIndex={setOpen} onClose={() => setOpen(null)} onSimilar={() => setOpen(null)} />, document.body)}
    </div>
  );
}
