import { useEffect, useRef, useState } from "react";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { open as pickFolder } from "@tauri-apps/plugin-dialog";
import { api, fmtInt, inTauri, type IngestStatus } from "../api";
import { Icon } from "../components";
import icon from "../icon.png";
import { SpinGlobe } from "../SpinGlobe";
import { IngestShow } from "./IngestShow";

// decorative routes between well-known cities (no user data): Lisbon → New York → Rio → Cape Town → Delhi → Kyoto → Sydney
const CITIES: [number, number][] = [[-9.14, 38.72], [-74.0, 40.71], [-43.2, -22.9], [18.42, -33.92], [77.21, 28.61], [135.77, 35.01], [151.21, -33.87]];
const ROUTES = CITIES.map((c, i) => [c, CITIES[(i + 1) % CITIES.length]] as [[number, number], [number, number]]);

type Storage = "ssd" | "hdd";

/** First run: nothing indexed yet. One clear job: choose a folder, index it (with the slideshow), then explore. */
export function Onboarding({ library, onDone, onSkip }: { library: string; onDone: () => void; onSkip: () => void }) {
  const demo = !inTauri() && new URLSearchParams(location.search).has("ingestdemo");     // development preview
  const [folder, setFolder] = useState("");
  const [storage, setStorage] = useState<Storage>("hdd");
  const [reason, setReason] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const [typing, setTyping] = useState(false);                 // plain browser (development): type the path
  const [ing, setIng] = useState<IngestStatus | null>(demo ? { running: true, phase: "ingesting", total: 12480, committed: 3150, eta_s: 2600 } as IngestStatus : null);
  const [err, setErr] = useState<string | null>(null);
  const [counts, setCounts] = useState<{ photos: number; videos: number } | null>(null);
  const started = useRef(demo);

  const choose = async (p: string) => {
    setFolder(p); setErr(null); setReason(null);
    api.storageGuess(p).then((g) => { setStorage(g.storage); setReason(g.reason); }).catch(() => {});
  };

  // drop a folder anywhere on the window (Tauri gives real paths; a browser cannot)
  useEffect(() => {
    if (!inTauri()) return;
    let un: (() => void) | undefined;
    getCurrentWebview().onDragDropEvent((e) => {
      const p = e.payload;
      if (p.type === "enter" || p.type === "over") setDrag(true);
      else if (p.type === "leave") setDrag(false);
      else if (p.type === "drop") { setDrag(false); if (p.paths[0]) choose(p.paths[0]); }
    }).then((f) => { un = f; }).catch(() => {});
    return () => un?.();
  }, []);

  // progress while indexing; when it finishes, show what was found
  useEffect(() => {
    if (demo) return;
    let alive = true;
    const tick = () => api.ingestStatus().then((s) => {
      if (!alive) return;
      if (s.running || started.current) setIng(s);
      if (started.current && !s.running && s.phase === "done") api.library().then((l) => alive && setCounts({ photos: l.photos, videos: l.videos })).catch(() => {});
    }).catch(() => {});
    tick();
    const id = setInterval(tick, 1000);
    return () => { alive = false; clearInterval(id); };
  }, [demo]);

  const browse = async () => {
    const p = await pickFolder({ directory: true, multiple: false, title: "Choose a folder of photos and videos" });
    if (typeof p === "string") choose(p);
  };
  const start = async () => {
    setErr(null);
    try { started.current = true; setIng(await api.ingestStart([folder.trim()], storage)); }
    catch (e) { started.current = false; setErr(String((e as Error).message || e)); }
  };

  const running = !!ing?.running;
  const finished = started.current && !running && ing?.phase === "done";
  const stopped = started.current && !running && (ing?.phase === "stopped" || ing?.phase === "failed");
  const step = finished ? 3 : running ? 2 : 1;

  return (
    <div className={`onboard ${drag ? "dragging" : ""}`}>
      <header className="onboardtop">
        <div className="brand"><img src={icon} alt="" /><div><div className="brandname">Backthen</div><div className="brandtag">your photos, in your words</div></div></div>
        <ol className="onsteps" aria-label="Steps">
          {["Choose a folder", "Index", "Search"].map((l, i) => (
            <li key={l} className={i + 1 < step ? "done" : i + 1 === step ? "now" : ""}><span>{i + 1 < step ? "✓" : i + 1}</span>{l}</li>
          ))}
        </ol>
        {!running && <button className="linkbtn" onClick={onSkip}>Skip for now</button>}
      </header>

      {running || (demo && ing) ? (
        <section className="onboardrun">
          <IngestShow ing={ing!} />
          <div className="onboardhint">
            <span>Files are only read, never changed. You can stop at any time; next time it continues where it left off.</span>
            <button className="btn" onClick={() => api.ingestCancel().then(setIng)}>Stop</button>
          </div>
        </section>
      ) : finished ? (
        <section className="onboardhero">
          <SpinGlobe size={260} arcs={ROUTES} />
          <div className="onboardcopy">
            <div className="eyebrow">All set</div>
            <h1 className="display">Your library is ready</h1>
            <p className="lede">{counts ? <>{fmtInt(counts.photos)} photos and {fmtInt(counts.videos)} videos, searchable in your own words.</> : "Everything is indexed and searchable in your own words."}</p>
            <button className="btn primary big" onClick={onDone}><Icon.search />Start exploring</button>
          </div>
        </section>
      ) : (
        <section className="onboardhero">
          <SpinGlobe size={360} arcs={ROUTES} />
          <div className="onboardcopy">
            <div className="eyebrow">Welcome</div>
            <h1 className="display">Where have your photos been?</h1>
            <p className="lede">Point Backthen at your photos and videos. It looks at each one once, on this computer, so you can later
              search for <i>"a birthday cake"</i> or <i>"waterfall in the mist"</i> and jump to the exact moment in a video.</p>
            {stopped && <p className="warnline">{ing?.phase === "failed" ? "Indexing stopped with an error." : "Indexing was stopped."} Start again to continue where it left off.</p>}
            {stopped && ing?.phase === "failed" && ing.error && <p className="error">{ing.error}</p>}
            {!folder ? (
              <div className="dropzone">
                {typing
                  ? <input className="input" autoFocus placeholder="Folder with photos and videos" onKeyDown={(e) => e.key === "Enter" && choose((e.target as HTMLInputElement).value)} />
                  : <button className="btn primary big" onClick={inTauri() ? browse : () => setTyping(true)}><Icon.folder />Choose a folder</button>}
                <span className="hint">{typing ? "type a folder path and press Enter" : "or drop a folder anywhere on this window"}</span>
              </div>
            ) : (
              <div className="pickedcard">
                <div className="row" style={{ gap: 8 }}><Icon.folder /><span className="mono picked">{folder}</span>
                  <button className="linkbtn" onClick={() => setFolder("")}>Change</button></div>
                <div className="seg" role="radiogroup" aria-label="Drive type">
                  <button className={storage === "hdd" ? "on" : ""} onClick={() => setStorage("hdd")}>External / hard drive</button>
                  <button className={storage === "ssd" ? "on" : ""} onClick={() => setStorage("ssd")}>Internal SSD</button>
                </div>
                <span className="hint">{reason ? `Detected: ${reason}. ` : ""}{storage === "hdd" ? "Reads one file at a time, in disk order: fastest for hard drives." : "Reads many files at once: fastest for SSDs."}</span>
                <button className="btn primary big" onClick={start}>Start indexing<span aria-hidden> →</span></button>
              </div>
            )}
            {err && <p className="error">{err}</p>}
            <p className="hint">Nothing is uploaded. The index is kept in <span className="mono">{library || "the library folder"}</span>.</p>
          </div>
        </section>
      )}
      {drag && <div className="dropveil"><Icon.folder />Drop the folder to index it</div>}
    </div>
  );
}
