import { useEffect, useState } from "react";
import { mediaUrl } from "../api";

const still = () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

type Loaded = { url: string; w: number; h: number };

/** Load a thumbnail for sure: retries with back-off (a busy engine can drop a connection), resolving to the URL that
 *  worked and the image size, or null after the last try. Never leaves a broken-image icon on screen. */
export function loadThumb(id: string): Promise<Loaded | null> {
  const waits = [0, 500, 1500, 3500];
  return new Promise((resolve) => {
    let k = 0;
    const attempt = () => {
      const url = mediaUrl("thumb", id, k ? { r: k } : {});
      const img = new Image();
      img.decoding = "async";
      img.onload = () => resolve({ url, w: img.naturalWidth, h: img.naturalHeight });
      img.onerror = () => { k += 1; if (k < waits.length) setTimeout(attempt, waits[k]); else resolve(null); };
      img.src = url;
    };
    attempt();
  });
}

/** A memory card's photos: a polaroid camera "shooting" little travel scenes while the real photos load, then a
 *  flash and the real photos develop. Cards reveal one after another (index), so the surprise lasts a moment even
 *  when the photos are already cached. When the covers change later (refined), the new ones develop in place. */
export function MemoryPhotos({ ids, index, onShown }: { ids: string[]; index: number; onShown?: () => void }) {
  const [shown, setShown] = useState<Loaded[] | null>(null);
  const [reveal, setReveal] = useState(0);
  const key = ids.join(",");

  useEffect(() => {
    let alive = true;
    const t0 = performance.now();
    const minShow = shown || still() ? 0 : 1100 + index * 260;       // first time only: a short, staggered show
    Promise.all(ids.map(loadThumb)).then((urls) => {
      const ok = urls.filter((u): u is Loaded => !!u);
      const wait = Math.max(0, minShow - (performance.now() - t0));
      window.setTimeout(() => {
        if (!alive || !ok.length) return;
        if (!shown) onShown?.();                                     // first photos of this card are on screen
        setShown(ok); setReveal((n) => n + 1);
      }, wait);
    });
    return () => { alive = false; };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!shown) return <PolaroidLoader index={index} />;
  const [big, ...small] = shown;
  return (
    <div key={reveal} className={`pcphotos n${shown.length} develop`}>
      <BigPhoto p={big} />
      {small.map((p) => <img key={p.url} className="small" src={p.url} alt="" />)}
    </div>
  );
}

const BIG_ASPECT = 4 / 3;       // the big photo's box (full card width)
const MIN_WIDTH_SHOWN = 0.93;   // never crop away more than 7% of a photo's width: nobody at the edges gets cut

/** The postcard's main photo, showing (almost) its full width. Up to slightly wider than the box it fills the box;
 *  wider photos (3:2, panoramas, video frames) are shown whole over a soft blurred copy of themselves. Tall photos
 *  keep their full width and lose a little at the bottom (faces sit high). */
function BigPhoto({ p }: { p: Loaded }) {
  const fit = p.w / p.h > BIG_ASPECT / MIN_WIDTH_SHOWN;
  return (
    <div className={`big ${fit ? "fit" : ""}`}>
      {fit && <img className="fill" src={p.url} alt="" aria-hidden />}
      <img className="main" src={p.url} alt="" />
    </div>
  );
}

/** Four little scenes with people, shot one after another with a camera flash. */
export function PolaroidLoader({ index = 0 }: { index?: number }) {
  return (
    <div className="pcphotos polaroid-loader" style={{ ["--i" as string]: index }} role="img" aria-label="Developing photos">
      {SCENES.map((Scene, k) => <div key={k} className="shot" style={{ ["--k" as string]: k }}><Scene /></div>)}
      <div className="flash" aria-hidden />
      <div className="shutter" aria-hidden><span /></div>
    </div>
  );
}

// ---------------------------------------------------------------- scenes (flat illustrations, no photos)
const INK = "#173f4a";
function Person({ x, y, s = 1, c = INK, pose = "stand" }: { x: number; y: number; s?: number; c?: string; pose?: "stand" | "wave" | "selfie" | "pack" }) {
  return (
    <g transform={`translate(${x} ${y}) scale(${s})`} fill={c}>
      <circle cx="0" cy="-34" r="7" />
      <rect x="-8" y="-26" width="16" height="22" rx="7" />
      <rect x="-7" y="-8" width="5.5" height="16" rx="2.5" />
      <rect x="1.5" y="-8" width="5.5" height="16" rx="2.5" />
      {pose === "wave" && <rect x="6" y="-44" width="4.5" height="20" rx="2.2" transform="rotate(18 8 -24)" />}
      {pose === "selfie" && <><rect x="5" y="-40" width="4.5" height="18" rx="2.2" transform="rotate(35 7 -22)" /><rect x="14" y="-50" width="7" height="11" rx="1.5" fill="#f2bd55" /></>}
      {pose === "pack" && <rect x="-14" y="-25" width="8" height="15" rx="3" fill="#d9634f" />}
    </g>
  );
}
const Beach = () => (
  <svg viewBox="0 0 200 150" preserveAspectRatio="xMidYMid slice">
    <defs><linearGradient id="pl-sky1" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#ffd3a1" /><stop offset="1" stopColor="#fbe7d0" /></linearGradient></defs>
    <rect width="200" height="150" fill="url(#pl-sky1)" />
    <circle cx="150" cy="44" r="18" fill="#f2bd55" />
    <rect y="84" width="200" height="22" fill="#5fb4cf" /><rect y="96" width="200" height="4" fill="#8fd0e0" opacity="0.7" />
    <rect y="106" width="200" height="44" fill="#f1d9ad" />
    <Person x={72} y={134} /><Person x={98} y={136} s={0.92} pose="wave" c="#1c4d5a" /><Person x={124} y={134} s={1.05} />
  </svg>
);
const Mountains = () => (
  <svg viewBox="0 0 200 150" preserveAspectRatio="xMidYMid slice">
    <rect width="200" height="150" fill="#d5ebf0" />
    <polygon points="0,110 52,46 92,96 128,38 200,108 200,150 0,150" fill="#7895a0" />
    <polygon points="52,46 64,61 58,62 50,58 42,60" fill="#fff" /><polygon points="128,38 142,56 134,55 126,52 118,55" fill="#fff" />
    <rect y="112" width="200" height="38" fill="#a6c793" />
    <Person x={84} y={140} pose="pack" /><Person x={112} y={142} s={0.95} pose="wave" c="#1c4d5a" />
  </svg>
);
const CityNight = () => (
  <svg viewBox="0 0 200 150" preserveAspectRatio="xMidYMid slice">
    <defs><linearGradient id="pl-sky3" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#14303a" /><stop offset="1" stopColor="#2c5664" /></linearGradient></defs>
    <rect width="200" height="150" fill="url(#pl-sky3)" />
    <circle cx="40" cy="30" r="9" fill="#f6e7c1" />
    {[[10, 60, 22], [36, 44, 18], [58, 70, 20], [82, 36, 16], [102, 56, 24], [130, 48, 18], [152, 66, 22], [176, 40, 24]].map(([x, top, w], i) => (
      <g key={i}><rect x={x} y={top} width={w} height={150 - top} fill="#0f242c" />
        {Array.from({ length: 6 }, (_, j) => <rect key={j} x={x + 4 + (j % 2) * (w / 2 - 2)} y={top + 6 + Math.floor(j / 2) * 12} width="4" height="5" fill="#f2bd55" opacity={(i + j) % 3 ? 0.9 : 0.3} />)}
      </g>))}
    <rect y="122" width="200" height="28" fill="#0b1c22" />
    <Person x={88} y={146} pose="selfie" c="#e9eef0" /><Person x={108} y={147} s={0.95} c="#d6dde0" />
  </svg>
);
const Landmark = () => (
  <svg viewBox="0 0 200 150" preserveAspectRatio="xMidYMid slice">
    <rect width="200" height="150" fill="#fde5c6" />
    <rect x="46" y="34" width="108" height="9" rx="3" fill="#d9634f" /><rect x="54" y="48" width="92" height="6" fill="#d9634f" />
    <rect x="62" y="40" width="9" height="84" fill="#c0503e" /><rect x="129" y="40" width="9" height="84" fill="#c0503e" />
    <rect y="120" width="200" height="30" fill="#e8cfa6" />
    <Person x={84} y={144} c="#1c4d5a" /><Person x={104} y={146} s={0.9} pose="wave" /><Person x={122} y={144} s={1.04} c="#1c4d5a" />
  </svg>
);
const SCENES = [Beach, Mountains, CityNight, Landmark];
