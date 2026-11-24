import { useEffect, useRef } from "react";
import { geoDistance, geoGraticule10, geoInterpolate, geoOrthographic, geoPath } from "d3-geo";
import { feature } from "topojson-client";
import type { GeometryCollection, Topology } from "topojson-specification";
import land110 from "world-atlas/land-110m.json";

const LAND = feature(land110 as unknown as Topology, (land110 as unknown as Topology).objects.land as GeometryCollection);
const GRAT = geoGraticule10();
type LonLat = [number, number];

/** A small decorative globe that turns by itself: pins glow on it and planes fly great-circle arcs between them.
 *  Offline (bundled Natural Earth outlines), pauses when off screen, holds still under "reduce motion". */
export function SpinGlobe({ size, pins = [], arcs = [], tilt = -18, speed = 6, start = 0 }: {
  size: number;
  pins?: LonLat[];
  arcs?: [LonLat, LonLat][];       // flown one after another, looping
  tilt?: number;                   // view latitude
  speed?: number;                  // degrees per second
  start?: number;                  // initial view longitude
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const data = useRef({ pins, arcs });
  data.current = { pins, arcs };

  useEffect(() => {
    const cv = ref.current!;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    cv.width = size * dpr; cv.height = size * dpr;
    const ctx = cv.getContext("2d")!;
    ctx.scale(dpr, dpr);
    const proj = geoOrthographic().scale(size / 2 - 2).translate([size / 2, size / 2]).clipAngle(90).precision(0.5);
    const path = geoPath(proj, ctx);
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let colors = read(cv);
    let visible = true, raf = 0, t0 = performance.now(), frames = 0;
    const io = new IntersectionObserver((es) => { visible = es.some((e) => e.isIntersecting); if (visible && !raf) raf = requestAnimationFrame(draw); });
    io.observe(cv);

    function draw(now: number) {
      raf = 0;
      if (++frames % 90 === 0) colors = read(cv);
      const s = (now - t0) / 1000;
      const { pins, arcs } = data.current;
      // with arcs, the globe turns to follow the current flight; otherwise it spins steadily
      const ARC_S = 3.2;
      const k = arcs.length ? Math.floor(s / ARC_S) % arcs.length : -1;
      const f = arcs.length ? (s % ARC_S) / ARC_S : 0;
      let lon = start - (still ? 0 : s * speed);
      if (k >= 0) {
        const mid = geoInterpolate(arcs[k][0], arcs[k][1])(0.5);
        lon = -mid[0] + Math.sin(s / 7) * 8;
      }
      const target: [number, number, number] = [lon, tilt, 0];
      const cur = proj.rotate();
      const ease = k >= 0 && !still ? 0.04 : 1;
      proj.rotate([cur[0] + angle(target[0] - cur[0]) * ease, cur[1] + (target[1] - cur[1]) * ease, 0]);
      const center: LonLat = [-proj.rotate()[0], -proj.rotate()[1]];

      ctx.clearRect(0, 0, size, size);
      const r = size / 2 - 2;
      const g = ctx.createRadialGradient(size * 0.38, size * 0.32, r * 0.1, size / 2, size / 2, r);
      g.addColorStop(0, colors.water2); g.addColorStop(1, colors.water);
      ctx.beginPath(); ctx.arc(size / 2, size / 2, r, 0, 2 * Math.PI); ctx.fillStyle = g; ctx.fill();
      ctx.beginPath(); path(GRAT); ctx.strokeStyle = colors.grid; ctx.lineWidth = 0.5; ctx.stroke();
      ctx.beginPath(); path(LAND); ctx.fillStyle = colors.land; ctx.fill();
      for (const p of pins) {
        if (geoDistance(center, p) > Math.PI / 2 - 0.05) continue;
        const [x, y] = proj(p)!;
        ctx.beginPath(); ctx.arc(x, y, 4.5, 0, 2 * Math.PI); ctx.fillStyle = colors.glow; ctx.fill();
        ctx.beginPath(); ctx.arc(x, y, 2, 0, 2 * Math.PI); ctx.fillStyle = colors.pin; ctx.fill();
      }
      if (k >= 0) {
        const ip = geoInterpolate(arcs[k][0], arcs[k][1]);
        const head = still ? 1 : Math.min(1, f * 1.25);
        ctx.setLineDash([3, 4]); ctx.lineWidth = 1.6; ctx.strokeStyle = colors.route;
        ctx.beginPath(); path({ type: "LineString", coordinates: Array.from({ length: 41 }, (_, i) => ip((i / 40) * head)) });
        ctx.stroke(); ctx.setLineDash([]);
        const hp = ip(head);
        if (geoDistance(center, hp) < Math.PI / 2) {
          const [x, y] = proj(hp)!;
          const [x2, y2] = proj(ip(Math.min(1, head + 0.01))) ?? [x + 1, y];
          plane(ctx, x, y, Math.atan2(y2 - y, x2 - x), colors.plane);
        }
      }
      ctx.beginPath(); ctx.arc(size / 2, size / 2, r, 0, 2 * Math.PI); ctx.strokeStyle = colors.rim; ctx.lineWidth = 1; ctx.stroke();
      if (visible && !still) raf = requestAnimationFrame(draw);
    }
    raf = requestAnimationFrame(draw);
    return () => { io.disconnect(); if (raf) cancelAnimationFrame(raf); };
  }, [size, tilt, speed, start]);

  return <canvas ref={ref} className="spinglobe" style={{ width: size, height: size }} aria-hidden />;
}

const angle = (d: number) => ((d + 540) % 360) - 180;

function read(el: Element) {
  const cs = getComputedStyle(el);
  const v = (n: string, f: string) => cs.getPropertyValue(n).trim() || f;
  return {
    water: v("--globe-water", "#1b5f75"), water2: v("--globe-water-2", "#2a7f99"), land: v("--globe-land", "#f3e9d6"),
    grid: v("--globe-grid", "rgba(255,255,255,0.12)"), rim: v("--globe-rim", "rgba(255,255,255,0.35)"),
    pin: v("--globe-pin", "#e46f5a"), glow: v("--globe-glow", "rgba(228,111,90,0.35)"),
    route: v("--globe-route", "#f2bd55"), plane: v("--globe-plane", "#ffffff"),
  };
}

function plane(ctx: CanvasRenderingContext2D, x: number, y: number, a: number, color: string) {
  ctx.save(); ctx.translate(x, y); ctx.rotate(a); ctx.fillStyle = color;
  ctx.shadowColor = "rgba(0,0,0,0.35)"; ctx.shadowBlur = 3;
  ctx.beginPath();                                   // simple airliner silhouette, nose to the right
  ctx.moveTo(7, 0); ctx.lineTo(2, -1.2); ctx.lineTo(-1, -6); ctx.lineTo(-2.6, -6); ctx.lineTo(-1, -1.2); ctx.lineTo(-5, -1);
  ctx.lineTo(-6.5, -3); ctx.lineTo(-7.4, -3); ctx.lineTo(-6.4, 0); ctx.lineTo(-7.4, 3); ctx.lineTo(-6.5, 3); ctx.lineTo(-5, 1);
  ctx.lineTo(-1, 1.2); ctx.lineTo(-2.6, 6); ctx.lineTo(-1, 6); ctx.lineTo(2, 1.2); ctx.closePath(); ctx.fill();
  ctx.restore();
}
