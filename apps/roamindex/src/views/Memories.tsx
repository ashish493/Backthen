import { useEffect, useRef, useState } from "react";
import { api, fmtInt, mediaUrl, type Asset, type Journeys, type World } from "../api";
import { SpinGlobe } from "../SpinGlobe";

export type MemoryOpen = { from: string; to: string; label: string };
type Card = { key: string; kind: "trip" | "ago" | "month"; title: string; sub: string; stamp: [string, string];
  open: MemoryOpen; cover: string[] };

const MON = (d: string) => new Date(d.length === 7 ? d + "-01T00:00:00Z" : d + "T00:00:00Z");
const monthName = (m: string) => MON(m).toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
const stampOf = (d: string): [string, string] =>
  [MON(d).toLocaleDateString(undefined, { month: "short", timeZone: "UTC" }).toUpperCase(), d.slice(0, 4)];

/** Trips and "your world" numbers, fetched once per app session (and again after a reload of the library). */
let journeysCache: Promise<Journeys> | null = null;
export function useJourneys() {
  const [j, setJ] = useState<Journeys | null>(null);
  useEffect(() => {
    let alive = true;
    (journeysCache ||= api.journeys().catch((e) => { journeysCache = null; throw e; }))
      .then((r) => alive && setJ(r)).catch(() => alive && setJ({ trips: [], world: null as unknown as World }));
    return () => { alive = false; };
  }, []);
  return j;
}
export function forgetJourneys() { journeysCache = null; }

/** Postcards to revisit: detected trips first, then this month in earlier years, then the busiest months. */
export function Memories({ journeys, onOpen }: { journeys: Journeys | null; onOpen: (m: MemoryOpen) => void }) {
  const [cards, setCards] = useState<Card[] | null>(null);

  useEffect(() => {
    if (!journeys) return;
    let alive = true;
    api.timeline({}).then(async (tl) => {
      const now = new Date();
      const thisYear = now.getFullYear(), thisMonth = now.getMonth() + 1;
      const out: (Omit<Card, "cover"> & { cover?: string[] })[] = [];
      const tripMonths = new Set<string>();
      for (const t of journeys.trips) {
        tripMonths.add(t.start.slice(0, 7));
        out.push({ key: `t${t.start}`, kind: "trip", title: t.title,
          sub: `${t.country && t.country !== t.title ? t.country + " · " : ""}${t.days} ${t.days === 1 ? "day" : "days"} · ${fmtInt(t.count)} items`,
          stamp: stampOf(t.start), open: { from: t.start, to: t.end, label: t.title }, cover: t.cover });
      }
      for (const m of [...tl.months].reverse()) {
        const [y, mo] = m.month.split("-").map(Number);
        if (mo === thisMonth && y < thisYear && !tripMonths.has(m.month)) {
          const n = thisYear - y;
          out.push({ key: `a${m.month}`, kind: "ago", title: n === 1 ? "A year ago" : `${n} years ago`,
            sub: `${monthName(m.month)} · ${fmtInt(m.count)} items`, stamp: stampOf(m.month),
            open: { from: m.month, to: m.month, label: monthName(m.month) } });
        }
      }
      const busiest = tl.months.filter((m) => m.count >= 3 && !tripMonths.has(m.month) && !out.some((c) => c.key === `a${m.month}`))
        .sort((a, b) => b.count - a.count).slice(0, Math.max(0, 10 - out.length)).sort((a, b) => b.month.localeCompare(a.month));
      for (const m of busiest) {
        out.push({ key: `m${m.month}`, kind: "month", title: monthName(m.month), sub: `${fmtInt(m.count)} photos and videos`,
          stamp: stampOf(m.month), open: { from: m.month, to: m.month, label: monthName(m.month) } });
      }
      const full = await Promise.all(out.slice(0, 12).map(async (c) => {
        if (c.cover?.length) return c as Card;
        const p = await api.browse({ from: c.open.from, to: c.open.to }, 3).catch(() => ({ items: [] as Asset[] }));
        return { ...c, cover: p.items.filter((a) => a.has_thumb).map((a) => a.asset_id) } as Card;
      }));
      if (alive) setCards(full.filter((c) => c.cover.length > 0));
    }).catch(() => alive && setCards([]));
    return () => { alive = false; };
  }, [journeys]);

  if (cards === null) {
    return (
      <section className="memsection">
        <h2 className="display">Memories</h2>
        <div className="memories" aria-hidden>{Array.from({ length: 4 }, (_, i) => <div key={i} className="postcard skeleton" />)}</div>
      </section>
    );
  }
  if (cards.length === 0) return null;
  return (
    <section className="memsection">
      <h2 className="display">Memories</h2>
      <div className="memories">
        {cards.map((c, i) => (
          <button key={c.key} className={`postcard ${c.kind}`} style={{ ["--tilt" as string]: `${[-1.6, 1.2, -0.8, 1.8, -1.2][i % 5]}deg`, ["--i" as string]: i }}
            onClick={() => onOpen(c.open)} title={`Show ${c.open.label}`}>
            <div className={`mosaic n${c.cover.length}`}>
              {c.cover.map((id) => <img key={id} src={mediaUrl("thumb", id)} alt="" loading="lazy" />)}
            </div>
            <div className="stamp" aria-hidden><b>{c.stamp[0]}</b><span>{c.stamp[1]}</span></div>
            <div className="postmeta">
              {c.kind !== "month" && <span className={`memtag ${c.kind}`}>{c.kind === "trip" ? "Trip" : "On this month"}</span>}
              <b className="posttitle">{c.title}</b>
              <small>{c.sub}</small>
            </div>
          </button>
        ))}
      </div>
    </section>
  );
}

function useCountUp(target: number, ms = 1200) {
  const [v, setV] = useState(0);
  const from = useRef(0);
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { setV(target); return; }
    const a = from.current, t0 = performance.now();
    let raf = 0;
    const step = (now: number) => {
      const f = Math.min(1, (now - t0) / ms), e = 1 - Math.pow(1 - f, 3);
      setV(a + (target - a) * e);
      if (f < 1) raf = requestAnimationFrame(step); else from.current = target;
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return v;
}

function Num({ n, digits = 0 }: { n: number; digits?: number }) {
  const v = useCountUp(n);
  return <>{digits ? v.toFixed(digits) : fmtInt(Math.round(v))}</>;
}

/** "Your world": a little spinning globe with your places, and the archive in a few numbers. */
export function WorldStrip({ journeys }: { journeys: Journeys | null }) {
  const w = journeys?.world;
  if (!w || !(w.photos + w.videos)) return null;
  const pins = w.pins.map((p) => [p.lon, p.lat] as [number, number]);
  const stops = [...journeys!.trips].reverse().map((t) => [t.lon, t.lat] as [number, number]);
  const arcs = stops.slice(1).map((s, i) => [stops[i], s] as [[number, number], [number, number]]);
  const span = w.years.length ? (w.years[0] === w.years[w.years.length - 1] ? `${w.years[0]}` : `${w.years[0]}–${w.years[w.years.length - 1]}`) : null;
  const tiles: [string, JSX.Element, string?][] = [
    ["Years", <Num n={w.years.length} />, span ?? undefined],
    ["Countries", <Num n={w.countries} />, w.located ? `${fmtInt(w.places)} places` : "no locations yet"],
    ["Trips", <Num n={w.trips} />, "found from dates and places"],
    ["Photos", <Num n={w.photos} />],
    ["Videos", <Num n={w.videos} />, w.video_hours ? `${w.video_hours} hours` : undefined],
  ];
  return (
    <section className="worldstrip">
      <div className="worldglobe">{pins.length > 0 || arcs.length > 0 ? <SpinGlobe size={150} pins={pins} arcs={arcs} tilt={-25} /> : <SpinGlobe size={150} />}</div>
      <div className="worldcopy">
        <div className="eyebrow">Your world</div>
        <div className="worldtiles">
          {tiles.map(([k, v, s], i) => (
            <div key={k} className="wtile" style={{ ["--i" as string]: i }}><div className="wv">{v}</div><div className="wk">{k}</div>{s && <div className="ws">{s}</div>}</div>
          ))}
        </div>
      </div>
    </section>
  );
}
