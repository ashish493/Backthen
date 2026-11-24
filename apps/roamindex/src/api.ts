// Client for the RoamEngine local API. The only network peer is 127.0.0.1 (enforced by the app's CSP too).
import { invoke, isTauri } from "@tauri-apps/api/core";

export type EngineInfo = {
  status: "starting" | "ready" | "failed" | "stopped" | "";
  port?: number | null;
  token?: string | null;
  library: string;
  python: string;
  error?: string | null;
  log?: string | null;
};

export type AppConfig = { python: string; library: string };

export type Moment = { timestamp_s: number | null; frame_index: number; score: number };

export type Asset = {
  asset_id: string;
  media_type: "photo" | "video";
  format: string | null;
  width: number;
  height: number;
  rotation: number;
  capture_time: string | null;
  capture_time_is_utc: boolean;
  capture_time_source: string | null;
  lat: number | null;
  lon: number | null;
  duration_s: number | null;
  has_thumb: boolean;
  score?: number;
  moments?: Moment[];
};

export type AssetFile = { path: string; source_id: number; rel_path: string; size: number; status: string; ext: string };

export type AssetDetail = Asset & {
  codec: string | null;
  size: number;
  gps_source: string | null;
  place_name?: string | null;
  original_lat?: number | null;
  original_lon?: number | null;
  capture_time_utc: string | null;
  capture_time_local: string | null;
  frames: { frame_index: number; timestamp_s: number | null }[];
  files: AssetFile[];
};

export type Page = { total: number; offset: number; items: Asset[]; text?: string; timing_ms?: { encode: number; rank: number } };

export type LibraryStats = {
  library: string;
  empty?: boolean;
  model_id: string | null;
  assets: number;
  photos: number;
  videos: number;
  files: number;
  frames: number;
  with_time: number;
  with_gps: number;
  sources: { source_id: number; root: string; label: string | null; storage: string | null }[];
  last_run: { run_id: number; started_at: string; finished_at: string | null; code_version: string } | null;
  status: Record<string, number>;
  time_range: [string, string] | null;
};

export type IngestStatus = {
  running: boolean;
  phase?: string;
  committed?: number;
  total?: number | null;
  media?: number;
  skipped?: number;
  elapsed_s?: number;
  eta_s?: number | null;
  error?: string;
  report?: Record<string, unknown>;
};

export type Filters = {
  type?: "photo" | "video" | "";
  from?: string;
  to?: string;
  gps?: boolean | "0" | "1";
  manual?: "1";
  lat?: number;
  lon?: number;
  radius_km?: number;
  bbox?: string;
  dated?: "0" | "1";
};

let base = "";
let token = "";

/** Engine handshake: from the Tauri shell, or `?port=&token=` when the UI runs in a plain browser (development). */
export async function engineInfo(): Promise<EngineInfo> {
  if (isTauri()) {
    const info = await invoke<EngineInfo>("engine_info");
    if (info.status === "ready" && info.port && info.token) connect(info.port, info.token);
    return info;
  }
  const q = new URLSearchParams(location.search);
  const port = Number(q.get("port"));
  const tok = q.get("token") || "";
  if (!port || !tok) return { status: "failed", library: "", python: "", error: "Open this page from the RoamIndex app, or add ?port=&token= of a running `roamengine serve`." };
  connect(port, tok);
  return { status: "ready", port, token: tok, library: "", python: "" };
}

export function connect(port: number, tok: string) {
  base = `http://127.0.0.1:${port}`;
  token = tok;
}

export const inTauri = () => isTauri();

function qs(params: Record<string, unknown>) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    p.set(k, v === true ? "1" : String(v));
  }
  return p;
}

/** URL for <img>/<video> sources (the token travels as a query parameter there). */
export function mediaUrl(kind: "thumb" | "preview" | "frame" | "media", id: string, params: Record<string, unknown> = {}) {
  const p = qs(params);
  p.set("token", token);
  return `${base}/${kind}/${id}?${p}`;
}

async function call<T>(method: string, path: string, params: Record<string, unknown> = {}, body?: unknown): Promise<T> {
  const p = qs(params).toString();
  const r = await fetch(`${base}${path}${p ? "?" + p : ""}`, {
    method,
    headers: { "X-Roam-Token": token, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((data as { error?: string }).error || `HTTP ${r.status}`);
  return data as T;
}

export const api = {
  library: () => call<LibraryStats>("GET", "/api/library"),
  reload: () => call<LibraryStats>("POST", "/api/reload", {}, {}),
  search: (q: string, f: Filters, k = 60, offset = 0) => call<Page>("GET", "/api/search", { q, k, offset, ...f }),
  similar: (id: string, f: Filters, k = 60, offset = 0, frame?: number) =>
    call<Page>("GET", `/api/similar/${id}`, { k, offset, frame, ...f }),
  browse: (f: Filters, k = 120, offset = 0, order = "desc") => call<Page>("GET", "/api/browse", { k, offset, order, ...f }),
  timeline: (f: Filters) => call<{ months: { month: string; count: number }[]; undated: number }>("GET", "/api/timeline", { ...f }),
  points: (f: Filters) =>
    call<{ total: number; asset_ids: string[]; lat: number[]; lon: number[]; video: number[] }>("GET", "/api/points", { ...f }),
  asset: (id: string) => call<AssetDetail>("GET", `/api/asset/${id}`),
  suggestions: (n = 12) => call<{ items: { text: string; strength: number }[] }>("GET", "/api/suggestions", { n }),
  ingestStatus: () => call<IngestStatus>("GET", "/api/ingest"),
  ingestStart: (roots: string[], storage: "ssd" | "hdd") => call<IngestStatus>("POST", "/api/ingest", {}, { roots, storage }),
  ingestCancel: () => call<IngestStatus>("POST", "/api/ingest/cancel", {}, {}),
  queries: () => call<{ queries: { query_id: number; text: string; judged: number; relevant: number }[] }>("GET", "/api/labels/queries"),
  addQuery: (text: string) => call<{ query_id: number }>("POST", "/api/labels/queries", {}, { text }),
  deleteQuery: (id: number) => call<{ ok: boolean }>("DELETE", `/api/labels/queries/${id}`),
  judgments: (id: number) => call<{ judgments: Record<string, number> }>("GET", `/api/labels/queries/${id}/judgments`),
  judge: (query_id: number, asset_id: string, grade: number | null) =>
    call<{ ok: boolean }>("POST", "/api/labels/judge", {}, { query_id, asset_id, grade }),
  evaluate: () => call<EvalReport>("GET", "/api/eval"),
  recentPlaces: () => call<{ recent: RecentPlace[] }>("GET", "/api/places", { n: 12 }),
  setPlace: (asset_ids: string[], lat: number, lon: number, name?: string) =>
    call<{ updated: number }>("POST", "/api/places/set", {}, { asset_ids, lat, lon, name }),
  clearPlace: (asset_ids: string[]) => call<{ cleared: number }>("POST", "/api/places/clear", {}, { asset_ids }),
  geocode: (q: string, online = false) =>
    call<{ results: GeoResult[]; source: string; attribution: string }>("GET", "/api/geocode", { q, n: 8, online }),
  placeSuggestions: (id: string) => call<{ suggestions: PlaceSuggestion[] }>("GET", `/api/places/suggest/${id}`),
};

export type GeoResult = { name: string; label: string; lat: number; lon: number; population: number | null; source: "geonames" | "osm" };
export type RecentPlace = { name: string | null; lat: number; lon: number; count: number };
export type PlaceSuggestion = { asset_id: string; lat: number; lon: number; dt_s: number; source: string | null; name: string | null };

/** Ask the app to open "Add places" for this item (the viewer is used by every view, so this goes through an event). */
export function requestSetPlace(a: Asset) {
  window.dispatchEvent(new CustomEvent<Asset>("roam:setplace", { detail: a }));
}


export type EvalReport = {
  model_id: string;
  queries: number;
  assets: number;
  mean: Record<string, number | null>;
  random_recall: Record<string, number | null>;
  per_query: ({ query_id: number; relevant: number } & Record<string, number | null>)[];
};

export async function getConfig(): Promise<AppConfig | null> {
  return isTauri() ? invoke<AppConfig>("get_config") : null;
}
export async function setConfig(cfg: AppConfig): Promise<EngineInfo> {
  return invoke<EngineInfo>("set_config", { python: cfg.python, library: cfg.library });
}
export async function restartEngine(): Promise<EngineInfo> {
  return invoke<EngineInfo>("restart_engine");
}

// ---------------------------------------------------------------- formatting helpers
export function fmtDuration(s: number | null | undefined) {
  if (s === null || s === undefined || !isFinite(s)) return "";
  const t = Math.max(0, Math.round(s));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), sec = t % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

export function fmtDate(iso: string | null | undefined, isUtc = true) {
  if (!iso) return "No date";
  const d = new Date(isUtc ? iso : iso + "Z");
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    ...(isUtc ? {} : { timeZone: "UTC" }),
  });
}

export function fmtBytes(n: number) {
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1000 && i < u.length - 1) { v /= 1000; i++; }
  return `${v.toFixed(v < 10 && i ? 1 : 0)} ${u[i]}`;
}

export const fmtInt = (n: number) => n.toLocaleString();
