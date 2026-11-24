import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef } from "react";
import { geoCircle, geoDistance, geoGraticule10, geoInterpolate, geoOrthographic, geoPath, type GeoPermissibleObjects } from "d3-geo";
import { feature, mesh } from "topojson-client";
import type { GeometryCollection, Topology } from "topojson-specification";
import land110 from "world-atlas/land-110m.json";
import countries110 from "world-atlas/countries-110m.json";

// An offline globe (Natural Earth outlines bundled into the app; no map tiles are fetched, so no location leaves the
// machine). Drag to turn it, wheel / touchpad pinch / touch pinch to zoom around the pointer. Pins that would overlap
// on screen merge into count bubbles. Rendered on a canvas so thousands of pins stay smooth.

type Topo = Topology<{ land?: GeometryCollection; countries?: GeometryCollection }>;
const shapes = (l: unknown, c: unknown) => {
  const lt = l as Topo, ct = c as Topo;
  return {
    land: feature(lt, lt.objects.land!) as GeoPermissibleObjects,
    borders: mesh(ct, ct.objects.countries!, (a, b) => a !== b) as GeoPermissibleObjects,
  };
};
const COARSE = shapes(land110, countries110);
let fine: ReturnType<typeof shapes> | null = null;     // the 1:50m outlines, loaded on first deep zoom
let fineLoading: Promise<void> | null = null;
function loadFine(then: () => void) {
  fineLoading ||= Promise.all([import("world-atlas/land-50m.json"), import("world-atlas/countries-50m.json")])
    .then(([l, c]) => { fine = shapes(l.default, c.default); });
  fineLoading.then(then);
}
const GRATICULE = geoGraticule10();
const SPHERE = { type: "Sphere" } as GeoPermissibleObjects;
const RAD = Math.PI / 180;
const EARTH_KM = 6371.0088;
const MAX_K = 3000;

export type GlobePoints = { asset_ids: string[]; lat: number[]; lon: number[]; video: number[] };
export type GlobePick = { lat: number; lon: number; radiusKm: number; ids?: string[] };
export type GlobeHandle = {
  fit: (animate?: boolean) => void;
  world: () => void;
  flyTo: (lat: number, lon: number, k?: number) => void;
  zoomBy: (f: number) => void;
};

type View = { lon: number; lat: number; k: number };

function css(name: string, el: Element) {
  return getComputedStyle(el).getPropertyValue(name).trim() || "#888";
}

/** View that frames these points: their spherical centre, zoomed until (almost) all fit. Spread over more than
 * ~a hemisphere -> the whole globe. */
function fitView(lat: number[], lon: number[], w: number, h: number): View {
  const n = lat.length;
  if (!n) return { lon: 20, lat: 15, k: 1 };
  let x = 0, y = 0, z = 0;
  for (let i = 0; i < n; i++) {
    const p = lat[i] * RAD, l = lon[i] * RAD;
    x += Math.cos(p) * Math.cos(l); y += Math.cos(p) * Math.sin(l); z += Math.sin(p);
  }
  const norm = Math.hypot(x, y, z) / n;
  const c: [number, number] = [Math.atan2(y, x) / RAD, Math.atan2(z, Math.hypot(x, y)) / RAD];
  if (norm < 0.2) return { lon: c[0], lat: Math.max(-60, Math.min(60, c[1])), k: 1 };
  const d = new Float64Array(n);
  for (let i = 0; i < n; i++) d[i] = geoDistance(c, [lon[i], lat[i]]);
  d.sort();
  const theta = d[Math.min(n - 1, Math.floor(n * 0.99))];       // ignore the farthest 1% (stray pins)
  if (theta > 1.0) return { lon: c[0], lat: c[1], k: 1 };       // more than ~57 deg from centre: whole globe
  const r0 = Math.min(w, h) / 2 * 0.9;
  const need = (Math.min(w, h) / 2 * 0.8) / Math.max(Math.sin(theta), 0.0004);
  return { lon: c[0], lat: c[1], k: Math.max(1, Math.min(MAX_K, need / r0)) };
}

export const Globe = forwardRef<GlobeHandle, {
  points: GlobePoints | null;
  ring?: { lat: number; lon: number; km: number } | null;
  marker?: { lat: number; lon: number } | null;
  onPick?: (p: GlobePick) => void;
  fitKey?: string;                 // when this changes (and points are loaded), frame the points
  clusterZoom?: boolean;           // clicking a bubble zooms into it
}>(function Globe({ points, ring, marker, onPick, fitKey, clusterZoom = true }, ref) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const view = useRef<View>({ lon: 20, lat: 15, k: 1 });
  const size = useRef({ w: 1, h: 1, dpr: 1 });
  const anim = useRef<number | null>(null);
  const frame = useRef<number | null>(null);
  const clusters = useRef<{ x: number; y: number; r: number; idx: number[] }[]>([]);
  const props = useRef({ points, ring, marker });
  props.current = { points, ring, marker };
  const fitted = useRef<string | null>(null);
  const redrawRef = useRef<() => void>(() => {});

  const projection = useCallback(() => {
    const { w, h } = size.current, v = view.current;
    return geoOrthographic().translate([w / 2, h / 2]).scale(Math.min(w, h) / 2 * 0.9 * v.k)
      .rotate([-v.lon, -v.lat]).clipAngle(90).precision(0.3);
  }, []);

  // ------------------------------------------------------------------ drawing
  const draw = useCallback(() => {
    frame.current = null;
    const cv = canvas.current, el = wrap.current;
    if (!cv || !el) return;
    const { w, h, dpr } = size.current;
    const ctx = cv.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const proj = projection();
    const p = geoPath(proj, ctx);
    const v = view.current;
    const R = proj.scale(), [cx, cy] = proj.translate();
    const col = {
      water: css("--water", el), land: css("--landfill", el), line: css("--landline", el), accent: css("--accent", el),
      video: css("--warn", el), panel: css("--panel", el), text: css("--text", el), grid: css("--line", el),
    };

    // atmosphere + ocean with a soft light from the upper left: reads as a ball, not a disc
    if (R < Math.max(w, h) * 1.5) {
      const halo = ctx.createRadialGradient(cx, cy, R * 0.98, cx, cy, R * 1.08);
      halo.addColorStop(0, "rgba(95,180,207,0.25)"); halo.addColorStop(1, "rgba(95,180,207,0)");
      ctx.fillStyle = halo; ctx.beginPath(); ctx.arc(cx, cy, R * 1.08, 0, 2 * Math.PI); ctx.fill();
    }
    ctx.beginPath(); p(SPHERE); ctx.fillStyle = col.water; ctx.fill();
    ctx.beginPath(); p(GRATICULE); ctx.strokeStyle = col.grid; ctx.lineWidth = 0.6; ctx.stroke();
    if (v.k >= 5 && !fine) loadFine(() => redrawRef.current());
    const s = v.k >= 5 && fine ? fine : COARSE;
    ctx.beginPath(); p(s.land); ctx.fillStyle = col.land; ctx.fill();
    ctx.beginPath(); p(s.borders); ctx.strokeStyle = col.line; ctx.lineWidth = v.k >= 5 ? 1 : 0.7; ctx.stroke();
    if (R < Math.max(w, h) * 1.5) {
      const shade = ctx.createRadialGradient(cx - R * 0.35, cy - R * 0.4, R * 0.1, cx, cy, R);
      shade.addColorStop(0, "rgba(255,255,255,0.10)"); shade.addColorStop(0.75, "rgba(0,0,0,0)"); shade.addColorStop(1, "rgba(0,0,0,0.16)");
      ctx.beginPath(); p(SPHERE); ctx.fillStyle = shade; ctx.fill();
    }

    // pins: project the visible ones, bin them into ~34 px screen cells, one bubble per occupied cell
    const pts = props.current.points;
    const out: { x: number; y: number; r: number; idx: number[] }[] = [];
    if (pts) {
      const c: [number, number] = [v.lon, v.lat];
      const cell = 34;
      const bins = new Map<number, { sx: number; sy: number; n: number; vid: number; idx: number[] }>();
      for (let i = 0; i < pts.lat.length; i++) {
        const ll: [number, number] = [pts.lon[i], pts.lat[i]];
        if (geoDistance(c, ll) > Math.PI / 2 - 1e-3) continue;          // far side of the globe
        const xy = proj(ll);
        if (!xy || xy[0] < -20 || xy[1] < -20 || xy[0] > w + 20 || xy[1] > h + 20) continue;
        const key = Math.floor(xy[0] / cell) * 100003 + Math.floor(xy[1] / cell);
        let b = bins.get(key);
        if (!b) bins.set(key, (b = { sx: 0, sy: 0, n: 0, vid: 0, idx: [] }));
        b.sx += xy[0]; b.sy += xy[1]; b.n++; b.vid += pts.video[i]; b.idx.push(i);
      }
      // bins are a fixed grid, so two busy neighbouring cells can still draw overlapping bubbles: merge those
      const list = [...bins.values()].map((b) => ({ ...b, x: b.sx / b.n, y: b.sy / b.n }))
        .sort((a, b) => b.n - a.n);
      const rad = (n: number) => (n === 1 ? 8 : Math.min(22, 9 + 6.4 * Math.log10(n)) + 3);
      const merged: typeof list = [];
      for (const b of list) {
        const m = merged.find((o) => Math.hypot(o.x - b.x, o.y - b.y) < rad(o.n) + rad(b.n) - 4);
        if (!m) { merged.push(b); continue; }
        const n = m.n + b.n;
        m.x = (m.x * m.n + b.x * b.n) / n; m.y = (m.y * m.n + b.y * b.n) / n;
        m.n = n; m.vid += b.vid; m.idx = m.idx.concat(b.idx);
      }
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.font = "600 11px system-ui, -apple-system, 'Segoe UI', sans-serif";
      for (const b of merged) {
        const { x, y } = b;
        const allVideo = b.vid === b.n;
        if (b.n === 1) {
          ctx.beginPath(); ctx.arc(x, y, 5, 0, 2 * Math.PI);
          ctx.fillStyle = allVideo ? col.video : col.accent; ctx.fill();
          ctx.lineWidth = 1.5; ctx.strokeStyle = col.panel; ctx.stroke();
          out.push({ x, y, r: 8, idx: b.idx });
        } else {
          const r = rad(b.n) - 3;
          ctx.beginPath(); ctx.arc(x, y, r + 3, 0, 2 * Math.PI);
          ctx.fillStyle = allVideo ? "rgba(224,160,80,0.25)" : "rgba(29,111,138,0.22)"; ctx.fill();
          ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI);
          ctx.fillStyle = allVideo ? col.video : col.accent; ctx.fill();
          ctx.lineWidth = 1.5; ctx.strokeStyle = col.panel; ctx.stroke();
          ctx.fillStyle = "#fff";
          ctx.fillText(b.n >= 10000 ? `${Math.round(b.n / 1000)}k` : b.n >= 1000 ? `${(b.n / 1000).toFixed(1)}k` : String(b.n), x, y + 0.5);
          out.push({ x, y, r: r + 3, idx: b.idx });
        }
      }
    }
    clusters.current = out;
    el.dataset.zoom = v.k.toFixed(2);

    const rg = props.current.ring;
    if (rg) {
      ctx.beginPath(); p(geoCircle().center([rg.lon, rg.lat]).radius(rg.km / EARTH_KM / RAD)());
      ctx.fillStyle = "rgba(29,111,138,0.08)"; ctx.fill();
      ctx.setLineDash([5, 4]); ctx.lineWidth = 1.5; ctx.strokeStyle = col.text; ctx.stroke(); ctx.setLineDash([]);
    }
    const mk = props.current.marker;
    if (mk && geoDistance([v.lon, v.lat], [mk.lon, mk.lat]) < Math.PI / 2) {
      const xy = proj([mk.lon, mk.lat]);
      if (xy) {                                         // a map pin, tip on the spot
        const [x, y] = xy;
        ctx.beginPath(); ctx.ellipse(x, y, 5, 2, 0, 0, 2 * Math.PI); ctx.fillStyle = "rgba(0,0,0,0.25)"; ctx.fill();
        ctx.beginPath(); ctx.moveTo(x, y);
        ctx.bezierCurveTo(x - 4, y - 10, x - 11, y - 14, x - 11, y - 22);
        ctx.arc(x, y - 22, 11, Math.PI, 0);
        ctx.bezierCurveTo(x + 11, y - 14, x + 4, y - 10, x, y);
        ctx.fillStyle = "#d93a2b"; ctx.fill(); ctx.lineWidth = 1.5; ctx.strokeStyle = "#fff"; ctx.stroke();
        ctx.beginPath(); ctx.arc(x, y - 22, 4, 0, 2 * Math.PI); ctx.fillStyle = "#fff"; ctx.fill();
      }
    }
  }, [projection]);

  const redraw = useCallback(() => { if (frame.current == null) frame.current = requestAnimationFrame(draw); }, [draw]);
  redrawRef.current = redraw;
  useEffect(redraw, [points, ring, marker, redraw]);

  // ------------------------------------------------------------------ view changes
  const stop = () => { if (anim.current != null) cancelAnimationFrame(anim.current); anim.current = null; };
  const animateTo = useCallback((to: View, ms = 750) => {
    stop();
    const from = { ...view.current };
    const interp = geoInterpolate([from.lon, from.lat], [to.lon, to.lat]);
    const lk0 = Math.log(from.k), lk1 = Math.log(to.k);
    const dist = geoDistance([from.lon, from.lat], [to.lon, to.lat]);
    // zoom out a little on the way when travelling far, like a camera flying over
    const lift = Math.min(lk0, lk1, Math.log(Math.max(1, 1.6 / (dist + 1e-3))));
    const t0 = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - t0) / ms), e = t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
      const [lon, lat] = interp(e);
      const base = lk0 + (lk1 - lk0) * e;
      const dip = dist > 0.2 ? (Math.min(lk0, lk1) - lift) * Math.sin(Math.PI * e) : 0;
      view.current = { lon, lat, k: Math.exp(base - dip) };
      draw();
      anim.current = t < 1 ? requestAnimationFrame(step) : null;
    };
    anim.current = requestAnimationFrame(step);
  }, [draw]);

  const zoomAround = useCallback((f: number, sx?: number, sy?: number) => {
    const v = view.current, k = Math.max(1, Math.min(MAX_K, v.k * f));
    if (k === v.k) return;
    const proj = projection();
    const anchor = sx != null && sy != null ? proj.invert!([sx, sy]) : null;
    const onGlobe = anchor && geoDistance([v.lon, v.lat], anchor) < Math.PI / 2 - 0.01;
    view.current = { ...v, k };
    if (onGlobe) {                       // keep the spot under the pointer under the pointer
      for (let it = 0; it < 3; it++) {
        const q = projection().invert!([sx!, sy!]);
        if (!q || !isFinite(q[0])) break;
        const cur = view.current;
        let dl = anchor![0] - q[0];
        if (dl > 180) dl -= 360; else if (dl < -180) dl += 360;
        view.current = { ...cur, lon: cur.lon + dl, lat: Math.max(-89, Math.min(89, cur.lat + anchor![1] - q[1])) };
      }
    }
    redraw();
  }, [projection, redraw]);

  const fit = useCallback((animate = true) => {
    const p = props.current.points;
    const { w, h } = size.current;
    const to = fitView(p?.lat || [], p?.lon || [], w, h);
    if (animate) animateTo(to); else { view.current = to; redraw(); }
  }, [animateTo, redraw]);

  useImperativeHandle(ref, () => ({
    fit,
    world: () => animateTo({ ...view.current, k: 1 }),
    flyTo: (lat, lon, k) => animateTo({ lat, lon, k: k ?? Math.max(view.current.k, 60) }),
    zoomBy: (f) => {
      stop();
      const from = view.current.k, to = Math.max(1, Math.min(MAX_K, from * f)), t0 = performance.now();
      const step = (now: number) => {
        const t = Math.min(1, (now - t0) / 220);
        view.current = { ...view.current, k: from * (to / from) ** (1 - (1 - t) ** 2) };
        draw();
        anim.current = t < 1 ? requestAnimationFrame(step) : null;
      };
      anim.current = requestAnimationFrame(step);
    },
  }), [fit, animateTo, draw]);

  // frame the pins the first time they arrive (and when fitKey changes)
  useEffect(() => {
    const key = fitKey ?? "";
    if (!points || fitted.current === key) return;
    const first = fitted.current === null;
    fitted.current = key;
    fit(!first);
  }, [points, fitKey, fit]);

  // ------------------------------------------------------------------ size
  useEffect(() => {
    const el = wrap.current!, cv = canvas.current!;
    const ro = new ResizeObserver(() => {
      const b = el.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
      size.current = { w: Math.max(1, b.width), h: Math.max(1, b.height), dpr };
      cv.width = Math.round(b.width * dpr); cv.height = Math.round(b.height * dpr);
      cv.style.width = `${b.width}px`; cv.style.height = `${b.height}px`;
      draw();
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [draw]);

  // ------------------------------------------------------------------ input
  useEffect(() => {
    const cv = canvas.current!;
    const local = (e: { clientX: number; clientY: number }) => {
      const b = cv.getBoundingClientRect();
      return [e.clientX - b.left, e.clientY - b.top] as [number, number];
    };
    // wheel: a mouse wheel and a touchpad two-finger scroll zoom; a touchpad pinch arrives as ctrl+wheel. The
    // listener is not passive, so the webview's own page zoom (ctrl+wheel) never kicks in over the map.
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      stop();
      const unit = e.deltaMode === 1 ? 33 : e.deltaMode === 2 ? 400 : 1;
      const dy = e.deltaY * unit;
      const f = Math.exp(-dy * (e.ctrlKey ? 0.01 : 0.0022));
      const [x, y] = local(e);
      zoomAround(Math.max(0.5, Math.min(2, f)), x, y);
    };
    cv.addEventListener("wheel", onWheel, { passive: false });

    const ptrs = new Map<number, [number, number]>();
    let last: [number, number] | null = null, moved = 0, pinch = 0, vel: [number, number] = [0, 0], lastT = 0;
    const rotateBy = (dx: number, dy: number) => {
      const v = view.current, R = Math.min(size.current.w, size.current.h) / 2 * 0.9 * v.k;
      const dlat = dy / R / RAD, dlon = -dx / (R * Math.max(0.15, Math.cos(v.lat * RAD))) / RAD;
      view.current = { ...v, lon: ((v.lon + dlon + 540) % 360) - 180, lat: Math.max(-85, Math.min(85, v.lat + dlat)) };
      redraw();
    };
    const down = (e: PointerEvent) => {
      stop();
      cv.setPointerCapture(e.pointerId);
      ptrs.set(e.pointerId, local(e));
      if (ptrs.size === 1) { last = local(e); moved = 0; vel = [0, 0]; lastT = performance.now(); }
      if (ptrs.size === 2) { const [a, b] = [...ptrs.values()]; pinch = Math.hypot(a[0] - b[0], a[1] - b[1]); moved = 99; }
    };
    const move = (e: PointerEvent) => {
      if (!ptrs.has(e.pointerId)) {                  // hover: pointer cursor over a bubble
        const [x, y] = local(e);
        cv.style.cursor = clusters.current.some((c) => Math.hypot(c.x - x, c.y - y) <= c.r) ? "pointer" : "";
        return;
      }
      const p = local(e);
      ptrs.set(e.pointerId, p);
      if (ptrs.size === 2) {
        const [a, b] = [...ptrs.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        if (pinch > 0) zoomAround(d / pinch, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
        pinch = d;
        return;
      }
      if (!last) return;
      const dx = p[0] - last[0], dy = p[1] - last[1];
      moved += Math.abs(dx) + Math.abs(dy);
      if (moved > 4) {
        cv.style.cursor = "grabbing";
        rotateBy(dx, dy);
        const now = performance.now(), dt = Math.max(1, now - lastT);
        vel = [dx / dt, dy / dt]; lastT = now;
      }
      last = p;
    };
    const up = (e: PointerEvent) => {
      const p = local(e);
      ptrs.delete(e.pointerId);
      if (ptrs.size > 0) { pinch = 0; last = [...ptrs.values()][0]; return; }
      cv.style.cursor = "";
      last = null;
      if (moved <= 4) { click(p[0], p[1]); return; }
      // a little momentum after a flick, so it spins like a globe
      if (performance.now() - lastT < 60 && Math.hypot(vel[0], vel[1]) > 0.3) {
        let [vx, vy] = vel, prev = performance.now();
        const spin = (now: number) => {
          const dt = now - prev; prev = now;
          rotateBy(vx * dt, vy * dt);
          vx *= 0.92 ** (dt / 16); vy *= 0.92 ** (dt / 16);
          anim.current = Math.hypot(vx, vy) > 0.02 ? requestAnimationFrame(spin) : null;
        };
        anim.current = requestAnimationFrame(spin);
      }
    };
    const click = (x: number, y: number) => {
      const proj = projection();
      const hit = clusters.current.filter((c) => Math.hypot(c.x - x, c.y - y) <= c.r)
        .sort((a, b) => Math.hypot(a.x - x, a.y - y) - Math.hypot(b.x - x, b.y - y))[0];
      const pts = props.current.points;
      if (hit && pts && clusterZoom) {
        const lat = hit.idx.map((i) => pts.lat[i]), lon = hit.idx.map((i) => pts.lon[i]);
        const c = proj.invert!([hit.x, hit.y]);
        if (!c) return;
        let far = 0;
        for (let i = 0; i < lat.length; i++) far = Math.max(far, geoDistance(c, [lon[i], lat[i]]));
        onPickRef.current?.({ lat: c[1], lon: c[0], radiusKm: Math.max(0.3, far * EARTH_KM * 1.05),
          ids: hit.idx.map((i) => pts.asset_ids[i]) });
        if (clusterZoom && hit.idx.length > 1) {
          const { w, h } = size.current;
          const to = fitView(lat, lon, w, h);
          animateTo({ ...to, k: Math.max(to.k, view.current.k * 2.5) });
        }
        return;
      }
      const ll = proj.invert!([x, y]);
      if (!ll || geoDistance([view.current.lon, view.current.lat], ll) > Math.PI / 2) return;   // clicked space
      const kmPerPx = EARTH_KM / proj.scale();
      onPickRef.current?.({ lat: ll[1], lon: ll[0], radiusKm: Math.max(0.3, 30 * kmPerPx) });
    };
    cv.addEventListener("pointerdown", down);
    cv.addEventListener("pointermove", move);
    cv.addEventListener("pointerup", up);
    cv.addEventListener("pointercancel", up);
    return () => {
      cv.removeEventListener("wheel", onWheel);
      cv.removeEventListener("pointerdown", down);
      cv.removeEventListener("pointermove", move);
      cv.removeEventListener("pointerup", up);
      cv.removeEventListener("pointercancel", up);
    };
  }, [projection, redraw, zoomAround, animateTo, clusterZoom]);

  const onPickRef = useRef(onPick);
  onPickRef.current = onPick;

  // redraw when the colour scheme flips
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", redraw);
    return () => mq.removeEventListener("change", redraw);
  }, [redraw]);

  return <div ref={wrap} className="globe"><canvas ref={canvas} /></div>;
});
