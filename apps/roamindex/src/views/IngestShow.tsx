import { useEffect, useRef, useState } from "react";
import { api, fmtDuration, fmtInt, mediaUrl, type IngestStatus } from "../api";
import { Icon } from "../components";
import { useRotating } from "../nostalgia";

type Glimpse = { asset_id: string; media_type: string; capture_time: string | null; capture_time_is_utc: boolean };

const SLIDE_MS = 4500;          // each photo stays this long; the crossfade takes 1.2 s of it
const REFILL_MS = 20000;        // fetch a fresh random batch (includes what was indexed since)

/** 0.4% → "0.4%", 42.7% → "42%": a decimal only while the number would otherwise sit at 0. */
const pct = (f: number) => (f > 0 && f < 0.01 ? `${(100 * f).toFixed(1)}%` : `${Math.floor(100 * f)}%`);

const when = (g: Glimpse) => {
  if (!g.capture_time) return null;
  const d = new Date(g.capture_time_is_utc ? g.capture_time : g.capture_time + "Z");
  return d.toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
};

/** Slideshow of random photos and video frames that are already indexed, shown while indexing runs,
 *  with nostalgic status words and the progress underneath. Thumbnails come from the library, so the
 *  slideshow never reads the source drive the indexer is busy with. */
export function IngestShow({ ing }: { ing: IngestStatus }) {
  const [pool, setPool] = useState<Glimpse[]>([]);
  const [slides, setSlides] = useState<{ g: Glimpse; key: number }[]>([]);
  const next = useRef(0);
  // one phrase per photo: the phrase changes exactly when the next photo fades in. Before the first photo
  // exists, the phrases run on their own timer at the same pace.
  const idle = useRotating(undefined, slides.length ? 0 : SLIDE_MS);
  const step = slides.length ? slides[slides.length - 1].key : idle.tick;
  const word = idle.wordAt(step);

  useEffect(() => {
    let alive = true;
    const load = () => api.glimpses(24).then((r) => alive && r.items.length && setPool(r.items)).catch(() => {});
    load();
    const id = setInterval(load, REFILL_MS);
    return () => { alive = false; clearInterval(id); };
  }, []);

  useEffect(() => {
    if (!pool.length) return;
    const show = () => {
      const g = pool[next.current++ % pool.length];
      const img = new Image();                       // decode first, then fade in: no half-loaded frames
      img.onload = img.onerror = () => setSlides((s) => [...s.slice(-1), { g, key: next.current }]);
      img.src = mediaUrl("thumb", g.asset_id);
    };
    if (!slides.length) show();
    const id = setInterval(show, SLIDE_MS);
    return () => clearInterval(id);
  }, [pool]); // eslint-disable-line react-hooks/exhaustive-deps

  const frac = ing.total ? Math.min(1, (ing.committed || 0) / ing.total) : 0;
  const cur = slides[slides.length - 1]?.g;
  return (
    <div className="ingestshow" aria-live="polite">
      {slides.map(({ g, key }, i) => (
        <div key={key} className={`slide ${i === slides.length - 1 ? "in" : "out"}`}>
          <img className="backdrop" src={mediaUrl("thumb", g.asset_id)} alt="" />
          <img className="photo" src={mediaUrl("thumb", g.asset_id)} alt="" />
        </div>
      ))}
      <div className="showshade" />
      {cur && (
        <div className="slidecap">
          {cur.media_type === "video" && <><Icon.play />From a video · </>}{when(cur) ?? "Undated"}
        </div>
      )}
      <div className="showinfo">
        <div className="showtop">
          <div key={step} className="nostalgia">{word}…</div>
          {ing.total != null && <div className="showpct">{pct(frac)}</div>}
        </div>
        <div className={`showbar ${ing.total == null ? "scanning" : ""}`} role="progressbar" aria-valuemin={0} aria-valuemax={100}
          aria-valuenow={ing.total != null ? Math.floor(100 * frac) : undefined}>
          <div style={ing.total != null ? { width: `${(100 * frac).toFixed(1)}%` } : undefined} />
        </div>
        <div className="showstats">
          {ing.total != null
            ? <>{fmtInt(ing.committed || 0)} of {fmtInt(ing.total)} files{ing.eta_s != null && ` · about ${fmtDuration(ing.eta_s)} left`}</>
            : "Looking through your folders"}
          {!pool.length && <span> · your photos will appear here as they are indexed</span>}
        </div>
      </div>
    </div>
  );
}
