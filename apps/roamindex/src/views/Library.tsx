import { useEffect, useRef, useState } from "react";
import { open as pickFolder } from "@tauri-apps/plugin-dialog";
import { api, fmtDuration, fmtInt, getConfig, inTauri, setConfig, type AppConfig, type EngineInfo, type IngestStatus, type LibraryStats } from "../api";
import { Icon, Stat } from "../components";
import { IngestShow } from "./IngestShow";

const pct = (a: number, b: number) => (b ? `${Math.round((100 * a) / b)}%` : "—");

export function LibraryView({ stats, refresh, engine, onEngine }: {
  stats: LibraryStats | null; refresh: () => void; engine: EngineInfo; onEngine: (e: EngineInfo) => void;
}) {
  const [ing, setIng] = useState<IngestStatus | null>(null);
  const [folder, setFolder] = useState("");
  const [storage, setStorage] = useState<"hdd" | "ssd">("hdd");
  const [err, setErr] = useState<string | null>(null);
  const wasRunning = useRef(false);
  const lastRefresh = useRef(0);

  useEffect(() => {
    let alive = true;
    const tick = () => api.ingestStatus().then((s) => {
      if (!alive) return;
      setIng(s);
      const now = Date.now();
      if ((wasRunning.current && !s.running) || (s.running && now - lastRefresh.current > 30000)) {
        lastRefresh.current = now;                                  // finished, or every 30 s while running
        refresh();
      }
      wasRunning.current = s.running;
    }).catch(() => {});
    tick();
    const id = setInterval(tick, 1000);
    return () => { alive = false; clearInterval(id); };
  }, [refresh]);

  const start = async (roots: string[]) => {
    setErr(null);
    try { setIng(await api.ingestStart(roots, storage)); wasRunning.current = true; } catch (e) { setErr(String((e as Error).message || e)); }
  };
  const browse = async () => {
    const p = await pickFolder({ directory: true, multiple: false, title: "Choose a folder of photos and videos" });
    if (typeof p === "string") setFolder(p);
  };

  const s = stats;
  const errors = s ? Object.entries(s.status).filter(([k]) => k.endsWith(":error")).reduce((n, [, v]) => n + v, 0) : 0;
  const missing = s ? Object.entries(s.status).filter(([k]) => k.endsWith(":missing")).reduce((n, [, v]) => n + v, 0) : 0;
  const running = !!ing?.running;
  const frac = ing?.total ? Math.min(1, (ing.committed || 0) / ing.total) : 0;

  return (
    <div className="view">
      <h1>Library</h1>
      {running && ing && <IngestShow ing={ing} />}
      {!running && !inTauri() && new URLSearchParams(location.search).has("ingestdemo") &&   /* development: preview the slideshow */
        <IngestShow ing={{ running: true, phase: "ingesting", total: 12480, committed: 5310, eta_s: 1820 } as IngestStatus} />}
      {s && !s.empty && (
        <div className="cards">
          <Stat k="Items" v={fmtInt(s.assets)} s={`${fmtInt(s.files)} files (${fmtInt(s.files - s.assets)} duplicate copies)`} />
          <Stat k="Photos" v={fmtInt(s.photos)} />
          <Stat k="Videos" v={fmtInt(s.videos)} s={`${fmtInt(s.frames - s.photos)} searchable video frames`} />
          <Stat k="With a date" v={pct(s.with_time, s.assets)} s={s.time_range ? `${s.time_range[0].slice(0, 4)} – ${s.time_range[1].slice(0, 4)}` : undefined} />
          <Stat k="With a location" v={pct(s.with_gps, s.assets)} s={`${fmtInt(s.with_gps)} items`} />
          <Stat k="Could not read" v={fmtInt(errors)} s={missing ? `${fmtInt(missing)} ${missing === 1 ? "file" : "files"} missing on disk` : "corrupt or unsupported files"} />
        </div>
      )}
      {s?.empty && !running && (
        <section className="welcome">
          <div className="eyebrow">Welcome to Backthen</div>
          <h2>Find any photo or video by describing it</h2>
          <p className="lede">Point Backthen at a folder or drive. It looks at every photo and video once, on this computer, so you can later
            search for "a birthday cake" or "waterfall in the mist" and jump to the exact moment in a video.</p>
          <ol className="steps">
            <li className={!running && !folder.trim() ? "now" : "done"}><span>1</span><div><b>Choose a folder</b><small>Your photos, a phone backup or a Google Takeout export.</small></div></li>
            <li className={running ? "now" : ""}><span>2</span><div><b>Let it index</b><small>Files are only read, never changed. Stop any time; it resumes.</small></div></li>
            <li><span>3</span><div><b>Search in your own words</b><small>Results in a fraction of a second, with no internet needed.</small></div></li>
          </ol>
        </section>
      )}

      {s && !s.empty && <FacesLine />}

      <h2>Add or update folders</h2>
      <div className="panel stack">
        <div className="hint">
          Indexing reads each file once and never changes it. Everything stays on this computer: the index lives in the library
          folder below. You can stop at any time — the next run continues where it left off.
        </div>
        <div className="row">
          <input className="input" style={{ flex: 1, minWidth: 260 }} placeholder="Folder with photos and videos" value={folder} onChange={(e) => setFolder(e.target.value)} />
          {inTauri() && <button className="btn" onClick={browse}><Icon.folder />Browse…</button>}
          <label className="field">Drive
            <select value={storage} onChange={(e) => setStorage(e.target.value as "hdd" | "ssd")}>
              <option value="hdd">External / hard drive</option>
              <option value="ssd">Internal SSD</option>
            </select>
          </label>
          <button className="btn primary" disabled={running || !folder.trim()} onClick={() => start([folder.trim()])}>Index folder</button>
        </div>
        {s && s.sources.length > 0 && (
          <table className="table">
            <thead><tr><th>Indexed folders</th><th>Drive</th><th /></tr></thead>
            <tbody>
              {s.sources.map((src) => (
                <tr key={src.source_id}>
                  <td className="mono">{src.root}</td>
                  <td>{src.storage === "ssd" ? "SSD" : "Hard drive"}</td>
                  <td style={{ textAlign: "right" }}>
                    <button className="btn" disabled={running} onClick={() => { setStorage(src.storage === "ssd" ? "ssd" : "hdd"); start([src.root]); }}>
                      <Icon.refresh />Update
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {err && <div className="error">{err}</div>}
        {ing && ing.phase && (
          <div>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <b>{running ? (ing.phase === "starting" ? "Starting… (scanning folders, loading the model)" : "Indexing") :
                ing.phase === "done" ? "Last indexing run finished" : ing.phase === "stopped" ? "Stopped — run again to continue" : "Indexing failed"}</b>
              {running && <button className="btn danger" onClick={() => api.ingestCancel().then(setIng)}>Stop</button>}
            </div>
            {running && ing.total != null && (
              <>
                <div className="progress"><div style={{ width: `${(100 * frac).toFixed(1)}%` }} /></div>
                <div className="hint">
                  {fmtInt(ing.committed || 0)} of {fmtInt(ing.total)} files to process{ing.skipped ? ` · ${fmtInt(ing.skipped)} already indexed` : ""}
                  {ing.elapsed_s != null && ` · ${fmtDuration(ing.elapsed_s)} elapsed`}{ing.eta_s != null && ` · about ${fmtDuration(ing.eta_s)} left`}
                </div>
              </>
            )}
            {ing.phase === "done" && ing.report && (
              <div className="hint">
                {fmtInt(Number(ing.report.files_processed || 0))} processed · {fmtInt(Number(ing.report.files_resumed_skipped || 0))} unchanged
                · {fmtInt(Number(ing.report.files_moved || 0))} moved · {fmtInt(Number(ing.report.files_missing || 0))} missing
                · {fmtDuration(Number(ing.report.wall_s || 0))}
              </div>
            )}
            {ing.error && <div className="error">{ing.error}</div>}
          </div>
        )}
      </div>

      {s?.last_run && (
        <p className="hint">Last run started {new Date(s.last_run.started_at).toLocaleString()} · RoamIngest {s.last_run.code_version} · model {s.model_id}</p>
      )}

      <Settings engine={engine} onEngine={onEngine} />
    </div>
  );
}

function Settings({ engine, onEngine }: { engine: EngineInfo; onEngine: (e: EngineInfo) => void }) {
  const [cfg, setCfg] = useState<AppConfig | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { getConfig().then(setCfg).catch(() => setCfg(null)); }, []);
  if (!inTauri()) return null;
  const save = async () => {
    if (!cfg) return;
    setMsg(null);
    try { onEngine(await setConfig(cfg)); setMsg("Saved. The engine is restarting with the new settings."); } catch (e) { setMsg(String(e)); }
  };
  const pickLib = async () => {
    const p = await pickFolder({ directory: true, multiple: false, title: "Choose the library folder (where the index is kept)" });
    if (typeof p === "string" && cfg) setCfg({ ...cfg, library: p });
  };
  return (
    <>
      <h2>Settings</h2>
      <div className="panel stack">
        {cfg && (
          <div className="form">
            <span>Library folder</span>
            <input className="input mono" value={cfg.library} onChange={(e) => setCfg({ ...cfg, library: e.target.value })} />
            <button className="btn" onClick={pickLib}><Icon.folder /></button>
            <span>Python</span>
            <input className="input mono" value={cfg.python} onChange={(e) => setCfg({ ...cfg, python: e.target.value })} />
            <span />
          </div>
        )}
        <div className="row">
          <button className="btn primary" onClick={save}>Save and restart engine</button>
          <span className="hint">Engine: {engine.status}{engine.port ? ` on 127.0.0.1:${engine.port}` : ""}</span>
        </div>
        {engine.log && <div className="hint">Engine log: <span className="mono">{engine.log}</span></div>}
        {msg && <div className="hint">{msg}</div>}
      </div>
    </>
  );
}


/** Progress of the background face pass that makes memory covers people-aware. Counts only; faces stay local. */
function FacesLine() {
  const [f, setF] = useState<Awaited<ReturnType<typeof api.facesStatus>> | null>(null);
  useEffect(() => {
    let alive = true;
    const tick = () => api.facesStatus().then((x) => alive && setF(x)).catch(() => {});
    tick();
    const id = setInterval(tick, 3000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  if (!f || f.state === "off") return null;
  const pct = f.total ? Math.floor((100 * f.done) / f.total) : 0;
  const text = f.state === "waiting" ? "Will look for the people in your photos shortly."
    : f.state === "downloading" ? "Getting the face models (a one-time 38 MB download from OpenCV)…"
    : f.state === "scanning" ? `Finding people in your photos… ${pct}% (${fmtInt(f.done)} of ${fmtInt(f.total)})`
    : f.state === "clustering" ? "Grouping faces into people…"
    : f.state === "error" ? `Could not look for people this time (${f.error ?? "error"}); it will try again later.`
    : f.people > 0
      ? `${fmtInt(f.people)} ${f.people === 1 ? "person who keeps" : "people who keep"} coming back in your photos (from ${fmtInt(f.faces)} ${f.faces === 1 ? "face" : "faces"}). Memories favour them over strangers.`
      : `No one appears often enough yet to count as one of your people (${fmtInt(f.faces)} ${f.faces === 1 ? "face" : "faces"} found). Memories use photo quality and places.`;
  return (
    <div className="facesline">
      <Icon.similar />
      <div>
        <div>{text}</div>
        {f.state === "scanning" && <div className="progress"><div style={{ width: `${pct}%` }} /></div>}
        <div className="hint">Faces are found on the thumbnails in your library folder and never leave this computer.</div>
      </div>
    </div>
  );
}
