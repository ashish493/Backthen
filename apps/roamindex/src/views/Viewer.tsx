import { useEffect, useRef, useState } from "react";
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener";
import { api, fmtBytes, fmtDate, fmtDuration, inTauri, mediaUrl, requestSetPlace, type Asset, type AssetDetail } from "../api";
import { Icon } from "../components";

const TIME_SOURCE: Record<string, string> = {
  exif: "camera (EXIF, with time zone)", exif_local: "camera clock (EXIF, no time zone)", sidecar: "Google Takeout sidecar",
  container: "video file",
};
const GPS_SOURCE: Record<string, string> = {
  exif: "camera (EXIF GPS)", container: "video file", sidecar_geoData: "Google Takeout sidecar",
  sidecar_geoDataExif: "Google Takeout sidecar (EXIF copy)", manual: "added by you in RoamIndex",
};

/** Formats the webview can usually play directly; others fall back to frame previews + "open in player". */
function playable(d: AssetDetail) {
  const c = (d.codec || "").toLowerCase();
  return ["h264", "vp9", "av1", "vp8"].includes(c);
}

export function Viewer({ list, index, onIndex, onClose, onSimilar, onShowOnMap }: {
  list: Asset[]; index: number; onIndex: (i: number) => void; onClose: () => void;
  onSimilar: (a: Asset, frame?: number) => void; onShowOnMap?: (a: Asset) => void;
}) {
  const a = list[index];
  const [d, setD] = useState<AssetDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [t, setT] = useState<number | null>(a?.moments?.[0]?.timestamp_s ?? null);
  const [videoFailed, setVideoFailed] = useState(false);
  const vref = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    setD(null); setErr(null); setVideoFailed(false);
    setT(a?.moments?.[0]?.timestamp_s ?? null);
    if (a) api.asset(a.asset_id).then(setD).catch((e) => setErr(String(e.message || e)));
  }, [a?.asset_id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "INPUT") return;
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight" && index < list.length - 1) onIndex(index + 1);
      else if (e.key === "ArrowLeft" && index > 0) onIndex(index - 1);
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [index, list.length, onClose, onIndex]);

  useEffect(() => {                       // seek the player when a moment is picked
    const v = vref.current;
    if (v && t != null) { try { v.currentTime = t; } catch { /* not seekable yet */ } }
  }, [t]);

  if (!a) return null;
  const isVideo = a.media_type === "video";
  const moments = a.moments?.length ? a.moments : (d?.frames || []).slice(0, 8).map((f) => ({ ...f, score: 0 }));
  const primary = d?.files.find((f) => f.status === "done");

  return (
    <div className="overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="stage" onClick={(e) => e.target === e.currentTarget && onClose()}>
        {!isVideo && <img key={a.asset_id} src={mediaUrl("preview", a.asset_id, { max: 2048 })} alt=""
          onError={(e) => ((e.target as HTMLImageElement).src = mediaUrl("thumb", a.asset_id))} />}
        {isVideo && d && playable(d) && !videoFailed && (
          <video key={a.asset_id} ref={vref} controls autoPlay muted={false}
            src={mediaUrl("media", a.asset_id) + (t != null ? `#t=${t}` : "")}
            onLoadedMetadata={(e) => { if (t != null && Math.abs(e.currentTarget.currentTime - t) > 0.5) e.currentTarget.currentTime = t; }}
            onError={() => setVideoFailed(true)} />
        )}
        {isVideo && d && (!playable(d) || videoFailed) && (
          <div style={{ textAlign: "center" }}>
            <img src={mediaUrl("frame", a.asset_id, { t: t ?? 0, max: 1600 })} alt="" />
            <div className="codec-note">
              This video ({d.codec?.toUpperCase() || "unknown codec"}) can't play inside the app. Showing the frame at {fmtDuration(t ?? 0)}.
              {inTauri() && primary && <> <button className="btn" style={{ marginLeft: 8 }} onClick={() => openPath(primary.path)}>Open in default player</button></>}
            </div>
          </div>
        )}
        {index > 0 && <button className="navbtn prev" onClick={() => onIndex(index - 1)} aria-label="Previous">‹</button>}
        {index < list.length - 1 && <button className="navbtn next" onClick={() => onIndex(index + 1)} aria-label="Next">›</button>}
        {isVideo && moments.length > 0 && (
          <div className="moments">
            {moments.map((m) => (
              <button key={m.frame_index} className={`moment ${t === m.timestamp_s ? "on" : ""}`} onClick={() => setT(m.timestamp_s)}
                title={a.moments?.length ? `match ${m.score.toFixed(3)}` : "sampled frame"}>
                <img src={mediaUrl("frame", a.asset_id, { t: m.timestamp_s ?? 0, max: 320 })} alt="" />
                <span>{fmtDuration(m.timestamp_s)}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      <aside className="info">
        <button className="btn close" onClick={onClose} aria-label="Close"><Icon.close /></button>
        <div style={{ fontWeight: 650, fontSize: 16 }}>{fmtDate(a.capture_time, a.capture_time_is_utc)}</div>
        <div className="hint">{index + 1} of {list.length}{a.score !== undefined && ` · match ${a.score.toFixed(3)}`}</div>
        {err && <p className="error">{err}</p>}
        {d && (
          <>
            <dl>
              <dt>Type</dt><dd>{isVideo ? "Video" : "Photo"} · {d.format?.toUpperCase()}</dd>
              <dt>Size</dt><dd>{d.width && d.height ? `${d.width} × ${d.height}` : "—"} · {fmtBytes(d.size)}</dd>
              {isVideo && <><dt>Length</dt><dd>{fmtDuration(d.duration_s)} · {d.codec?.toUpperCase()}</dd></>}
              <dt>Date</dt><dd>{d.capture_time_utc ? fmtDate(d.capture_time_utc) : d.capture_time_local ? `${fmtDate(d.capture_time_local, false)} (camera clock)` : "Unknown"}
                {d.capture_time_source && <div className="hint">from {TIME_SOURCE[d.capture_time_source] || d.capture_time_source}</div>}</dd>
              <dt>Place</dt><dd>{d.lat != null && d.lon != null
                ? <>{d.place_name && <b>{d.place_name} · </b>}{d.lat.toFixed(5)}, {d.lon.toFixed(5)}
                  {d.gps_source && <div className="hint">{d.gps_source === "manual" ? GPS_SOURCE.manual : `from ${GPS_SOURCE[d.gps_source] || d.gps_source}`}</div>}
                  {d.original_lat != null && <div className="hint">camera/file location: {d.original_lat.toFixed(5)}, {d.original_lon!.toFixed(5)}</div>}</>
                : "No location"}
                <div style={{ marginTop: 4, display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <button className="btn small" onClick={() => { onClose(); requestSetPlace({ ...a, lat: d.lat, lon: d.lon }); }}>
                    <Icon.pin />{d.lat != null ? "Change location" : "Add location"}</button>
                  {d.gps_source === "manual" && (
                    <button className="btn small" onClick={() => api.clearPlace([a.asset_id]).then(() => api.asset(a.asset_id)).then(setD)}>
                      Remove mine</button>
                  )}
                </div></dd>
              <dt>Copies</dt><dd>{d.files.length} file{d.files.length === 1 ? "" : "s"}</dd>
            </dl>
            <div className="actions">
              <button className="btn" onClick={() => onSimilar(a)}><Icon.similar />Find similar</button>
              {isVideo && t != null && <button className="btn" onClick={() => onSimilar(a, moments.find((m) => m.timestamp_s === t)?.frame_index)}>Similar to this moment</button>}
              {onShowOnMap && d.lat != null && <button className="btn" onClick={() => onShowOnMap(a)}><Icon.pin />Show on map</button>}
            </div>
            <h2>Files</h2>
            <div className="paths">
              {d.files.map((f) => (
                <div key={f.source_id + f.rel_path} className="row">
                  <span style={{ flex: 1 }}>{f.path}{f.status !== "done" && <span className="error"> ({f.status})</span>}</span>
                  {inTauri() && <button className="btn" title="Show in folder" onClick={() => revealItemInDir(f.path)}><Icon.folder /></button>}
                </div>
              ))}
            </div>
          </>
        )}
      </aside>
    </div>
  );
}
