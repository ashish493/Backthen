import { useEffect, useMemo, useRef, useState } from "react";
import { api, mediaUrl, type Relive as ReliveData, type ReliveSlide } from "../api";
import { Icon } from "../components";

const SLIDE_MS = 3800;
const still = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const src = (s: ReliveSlide) => s.media_type === "video" ? mediaUrl("thumb", s.asset_id) : mediaUrl("preview", s.asset_id, { max: 1600 });
const dayLabel = (iso: string | null) => iso
  ? new Date(iso.length === 10 ? iso + "T00:00:00Z" : iso.endsWith("Z") ? iso : iso + "Z")
    .toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" })
  : "";

/** Relive a trip: its best photos of each day, in order, full screen, with the route drawn as you go. */
export function Relive({ start, onClose }: { start: string; onClose: () => void }) {
  const [data, setData] = useState<ReliveData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [i, setI] = useState(0);
  const [prev, setPrev] = useState<number | null>(null);
  const [playing, setPlaying] = useState(true);
  const [ready, setReady] = useState(false);                 // first photo loaded (the title card shows until then)

  useEffect(() => { api.relive(start).then(setData).catch((e) => setErr(String(e.message || e))); }, [start]);
  const n = data?.slides.length ?? 0;
  const go = (j: number) => { if (!n) return; const k = Math.max(0, Math.min(n - 1, j)); if (k !== i) { setPrev(i); setI(k); } };

  useEffect(() => {                                          // preload the current and the next two
    if (!data) return;
    data.slides.slice(i, i + 3).forEach((s, k) => { const im = new Image(); if (k === 0) im.onload = () => setReady(true); im.src = src(s); });
  }, [data, i]);
  useEffect(() => {
    if (!playing || !ready || !n) return;
    if (i >= n - 1) { setPlaying(false); return; }
    const t = window.setTimeout(() => go(i + 1), SLIDE_MS);
    return () => clearTimeout(t);
  }, [playing, ready, i, n]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight") { setPlaying(false); go(i + 1); }
      else if (e.key === "ArrowLeft") { setPlaying(false); go(i - 1); }
      else if (e.key === " ") { e.preventDefault(); setPlaying((p) => !p); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  });

  const s = data?.slides[i];
  const ended = !!data && i === n - 1 && !playing;
  return (
    <div className="relive" role="dialog" aria-label={data ? `Relive ${data.title}` : "Relive a trip"}>
      {data && s && ready && (
        <div className="rlstage">
          {prev !== null && <img key={`p${prev}`} className="rlimg under" src={src(data.slides[prev])} alt="" />}
          <img key={i} className={`rlimg ${still() ? "" : "kb"}`} src={src(s)} alt="" style={{ ["--kb" as string]: `${SLIDE_MS + 1200}ms` }} />
        </div>
      )}
      {(!ready || !data) && (
        <div className="rltitle">
          {err ? <p className="error">{err}</p> : <>
            <div className="eyebrow">Relive</div>
            <h1 className="display">{data?.title ?? "…"}</h1>
            {data && <p>{data.days} days · {dayLabel(data.start)}</p>}
          </>}
        </div>
      )}
      <div className="rlbar" aria-hidden><div style={{ width: n ? `${(100 * (i + 1)) / n}%` : 0 }} /></div>
      <div className="rltop">
        {data && <div><b>{data.title}</b><span>{data.country && data.country !== data.title ? `${data.country} · ` : ""}{data.days} days</span></div>}
        <button className="rlbtn" onClick={onClose} aria-label="Close"><Icon.close /></button>
      </div>
      {data && s && ready && (
        <div key={`c${i}`} className="rlcaption">
          <div className="rlday">Day {s.day}{s.place ? ` · ${s.place}` : ""}</div>
          <div className="rldate">{dayLabel(s.capture_time)}</div>
        </div>
      )}
      {data && data.route.length > 1 && <RouteMap route={data.route} slides={data.slides} i={i} />}
      {data && (
        <div className="rlcontrols">
          <button className="rlbtn" onClick={() => { setPlaying(false); go(i - 1); }} disabled={i === 0} aria-label="Previous">‹</button>
          {ended
            ? <button className="rlbtn wide" onClick={() => { setPrev(null); setI(0); setPlaying(true); }}>Play again</button>
            : <button className="rlbtn wide" onClick={() => setPlaying((p) => !p)}>{playing ? "Pause" : "Play"}</button>}
          <button className="rlbtn" onClick={() => { setPlaying(false); go(i + 1); }} disabled={i >= n - 1} aria-label="Next">›</button>
          <span className="rlcount">{i + 1} / {n}</span>
        </div>
      )}
    </div>
  );
}

/** The trip's route in a small box (equirectangular, longitudes scaled by the cosine of the mean latitude), the part
 *  travelled so far drawn bright, and a dot where the current photo was taken (or the last place known). */
function RouteMap({ route, slides, i }: { route: [number, number][]; slides: ReliveSlide[]; i: number }) {
  const W = 180, H = 140, P = 12;
  const proj = useMemo(() => {
    const lats = route.map((p) => p[1]), lons = route.map((p) => p[0]);
    const k = Math.cos(((Math.min(...lats) + Math.max(...lats)) / 2) * Math.PI / 180);
    const x0 = Math.min(...lons) * k, x1 = Math.max(...lons) * k, y0 = Math.min(...lats), y1 = Math.max(...lats);
    const s = Math.min((W - 2 * P) / Math.max(x1 - x0, 1e-4), (H - 2 * P) / Math.max(y1 - y0, 1e-4));
    const ox = (W - (x1 - x0) * s) / 2, oy = (H - (y1 - y0) * s) / 2;
    return (lon: number, lat: number) => [ox + (lon * k - x0) * s, H - (oy + (lat - y0) * s)] as [number, number];
  }, [route]);
  const pts = route.map(([lo, la]) => proj(lo, la));
  const here = useRef<[number, number] | null>(null);
  const cur = slides.slice(0, i + 1).reverse().find((s) => s.lat != null && s.lon != null);
  here.current = cur ? proj(cur.lon!, cur.lat!) : here.current;
  // travelled: route points up to the one nearest the current position
  let upto = 0;
  if (here.current) {
    let best = Infinity;
    pts.forEach((p, j) => { const d = (p[0] - here.current![0]) ** 2 + (p[1] - here.current![1]) ** 2; if (d <= best) { best = d; upto = j; } });
  }
  const line = (a: [number, number][]) => a.map((p) => p.map((v) => v.toFixed(1)).join(",")).join(" ");
  return (
    <svg className="rlmap" viewBox={`0 0 ${W} ${H}`} aria-hidden>
      <polyline points={line(pts)} className="rlroute" />
      <polyline points={line(pts.slice(0, upto + 1))} className="rlroute done" />
      {here.current && <circle cx={here.current[0]} cy={here.current[1]} r={5} className="rlhere" />}
    </svg>
  );
}
