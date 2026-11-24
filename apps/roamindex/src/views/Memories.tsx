import { useEffect, useRef, useState } from "react";
import { api, fmtInt, inTauri, type Journeys, type MemoryCard, type Memories, type World } from "../api";
import { SpinGlobe } from "../SpinGlobe";
import { MemoryPhotos, PolaroidLoader } from "./Polaroid";

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

/** The memory cards, shared across visits to Home: the last answer shows at once, then refreshes. */
let memoriesCache: Memories | null = null;
let memoriesInflight: Promise<Memories> | null = null;
function fetchMemories() {
  return (memoriesInflight ||= api.memories().then((m) => { memoriesCache = m; return m; })
    .finally(() => { memoriesInflight = null; }));
}
/** Warm the home screen as soon as the engine is up (App calls this). */
export function prefetchHome() { fetchMemories().catch(() => {}); journeysCache ||= api.journeys().catch((e) => { journeysCache = null; throw e; }); }

function present(c: MemoryCard): Card {
  if (c.kind === "trip") {
    const days = c.days ?? 1;
    return { key: c.key, kind: c.kind, title: c.title ?? "A trip", cover: c.cover, stamp: stampOf(c.from),
      sub: `${c.country && c.country !== c.title ? c.country + " · " : ""}${days} ${days === 1 ? "day" : "days"} · ${fmtInt(c.count)} items`,
      open: { from: c.from, to: c.to, label: c.title ?? "A trip" } };
  }
  const m = c.month ?? c.from;
  const n = c.years_ago ?? 0;
  return c.kind === "ago"
    ? { key: c.key, kind: c.kind, title: n === 1 ? "A year ago" : `${n} years ago`, sub: `${monthName(m)} · ${fmtInt(c.count)} items`,
        stamp: stampOf(m), cover: c.cover, open: { from: m, to: m, label: monthName(m) } }
    : { key: c.key, kind: c.kind, title: monthName(m), sub: `${fmtInt(c.count)} photos and videos`, stamp: stampOf(m),
        cover: c.cover, open: { from: m, to: m, label: monthName(m) } };
}

/** Postcards to revisit: detected trips first, then this month in earlier years, then the busiest months. The engine
 *  answers at once with covers chosen by CLIPScore, then keeps refining them (the face model looks at each card's
 *  best candidates, the first four first); while it does, this refreshes every few seconds and changed covers fade in. */
export function Memories({ onOpen }: { onOpen: (m: MemoryOpen) => void }) {
  const [data, setData] = useState<Memories | null>(memoriesCache);
  const [revealed, setRevealed] = useState(0);                     // cards whose first photos are on screen
  const veilPreview = !inTauri() && new URLSearchParams(location.search).has("veil");     // development preview

  const ticker = useRef<() => void>(() => {});
  useEffect(() => {
    let alive = true, timer = 0;
    const tick = () => { clearTimeout(timer); fetchMemories().then((m) => {
      if (!alive) return;
      setData(m);
      if (m.refining || m.provisional) timer = window.setTimeout(tick, 1500);
    }).catch(() => { if (alive) setData((d) => d ?? { cards: [], version: 0, refining: false }); }); };
    ticker.current = tick;
    tick();
    return () => { alive = false; clearTimeout(timer); };
  }, []);

  const cards = data ? data.cards.map(present).filter((c) => c.cover.length > 0) : null;
  useEffect(() => { if (revealed > 0 && revealed === Math.min(data?.cards.length ?? 0, 4)) ticker.current(); },  // ask again
    [revealed]); // eslint-disable-line react-hooks/exhaustive-deps                                               // right then
  if (cards === null || (cards.length === 0 && data?.refining)) {          // nothing settled yet: still looking
    return (
      <section className="memsection">
        <div className="memhead">
          <h2 className="display">Memories</h2>
          <span className="loadingpill" role="status"><span className="pulse" aria-hidden />Loading memories<span className="dots" aria-hidden /></span>
        </div>
        <div className="memories" aria-hidden>{Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="postcard" style={{ ["--tilt" as string]: `${[-1.6, 1.2, -0.8, 1.8][i]}deg`, ["--i" as string]: i }}>
            <PolaroidLoader index={i} />
            <div className="postmeta"><span className="ghostline" /><span className="ghostline short" /></div>
          </div>))}
        </div>
      </section>
    );
  }
  if (cards.length === 0) return null;
  // the veil: after the first photos are in, while the engine still looks for each memory's whole-group photo
  const firstIn = revealed >= Math.min(cards.length, 4);
  const veil = veilPreview || (!!data?.refining && firstIn);
  return (
    <section className="memsection">
      <h2 className="display">Memories</h2>
      <div className="memrow">
      <div className="memories">
        {cards.map((c, i) => (
          <button key={c.key} className={`postcard ${c.kind}`} style={{ ["--tilt" as string]: `${[-1.6, 1.2, -0.8, 1.8, -1.2][i % 5]}deg`, ["--i" as string]: i }}
            onClick={() => onOpen(c.open)} title={`Show ${c.open.label}`}>
            <MemoryPhotos ids={c.cover} index={i} onShown={() => setRevealed((n) => n + 1)} />
            <div className="stamp" aria-hidden><b>{c.stamp[0]}</b><span>{c.stamp[1]}</span></div>
            <div className="postmeta">
              {c.kind !== "month" && <span className={`memtag ${c.kind}`}>{c.kind === "trip" ? "Trip" : "On this month"}</span>}
              <b className="posttitle">{c.title}</b>
              <small>{c.sub}</small>
            </div>
          </button>
        ))}
      </div>
      <RefineVeil on={veil} checked={data?.checked} total={data?.total} />
      </div>
    </section>
  );
}

/** "Getting everyone in the picture": an almost transparent veil over the row while the engine checks each memory's
 *  candidates for the photo with the whole group; the improved covers then develop in place. */
function RefineVeil({ on, checked, total }: { on: boolean; checked?: number; total?: number }) {
  const line = total && checked !== undefined && checked < total
    ? `Checked ${checked} of ${total} memories for your whole group`
    : "Picking the shot where nobody's left out";
  return (
    <div className={`refineveil ${on ? "on" : ""}`} role="status" aria-hidden={!on}>
      <div className="veilchip">
        <GroupFinder />
        <div><b>Getting everyone in the picture…</b><small>{line}</small></div>
      </div>
    </div>
  );
}

/** A viewfinder: little people step into frame one by one, then the shutter flashes. */
function GroupFinder() {
  return (
    <svg className="groupfinder" viewBox="0 0 64 44" aria-hidden>
      <rect className="gf-flash" x="2" y="2" width="60" height="40" rx="6" />
      <path className="gf-frame" d="M4 13V4h9M51 4h9v9M60 31v9h-9M13 40H4v-9" />
      {[16, 27, 38, 49].map((x, k) => (
        <g key={k} transform={`translate(${x} 36)`}>
          <g className="gf-person" style={{ ["--k" as string]: k }}>
            <circle cx="0" cy="-15" r="4.2" />
            <rect x="-4.8" y="-10" width="9.6" height="12" rx="4.5" />
          </g>
        </g>
      ))}
    </svg>
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
