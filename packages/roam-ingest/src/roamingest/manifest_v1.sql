-- RoamIngest manifest v1 (Contract A: RoamIngest -> RoamEngine). schema_version = 1.
-- Canonical copy; packages/roam-ingest/src/roamingest/manifest_v1.sql must stay identical (checked by a test).
--
-- Identity:  file identity    = (source_id, rel_path) in asset_files
--            content identity = asset_id (partial content ID) in assets; several files may share one asset
-- Times are ISO 8601 strings. capture_time_utc ends in 'Z'; capture_time_local has no zone.
-- Embeddings: L2-normalised float16 little-endian blobs. A photo has one frame (frame_index 0, timestamp_s NULL).
-- Derived file references (thumbnail_ref) are relative to the dataset directory.

CREATE TABLE IF NOT EXISTS meta(
  key   TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS sources(
  source_id   INTEGER PRIMARY KEY,
  root_path   TEXT NOT NULL UNIQUE,      -- absolute; re-rooting a moved archive = updating this row
  root_prefix TEXT NOT NULL,             -- root_path with a trailing separator (root_prefix || rel_path = path)
  label       TEXT,
  storage     TEXT                       -- 'ssd' | 'hdd' as configured for the last run
);

CREATE TABLE IF NOT EXISTS assets(
  asset_id            TEXT PRIMARY KEY,  -- xxh3-128 of (size, first 64 KiB, last 64 KiB)
  content_hash_full   TEXT,              -- xxh3-128 of all bytes; NULL until known
  size                INTEGER NOT NULL,
  media_type          TEXT NOT NULL,     -- 'photo' | 'video'
  format              TEXT,              -- lower-case extension without dot of the first file seen
  width               INTEGER,           -- photos: oriented size; videos: coded size (see rotation)
  height              INTEGER,
  orientation         INTEGER,           -- EXIF orientation tag (photos)
  rotation            INTEGER,           -- display rotation in degrees (videos), already applied to frames
  capture_time_utc    TEXT,
  capture_time_local  TEXT,
  tz_offset_min       INTEGER,
  capture_time_source TEXT,              -- 'exif' | 'sidecar' | 'container' | 'exif_local' | NULL
  lat                 REAL,
  lon                 REAL,
  alt                 REAL,
  gps_source          TEXT,              -- 'exif' | 'sidecar_geoData' | 'sidecar_geoDataExif' | 'container' | NULL
  codec               TEXT,
  duration_s          REAL,
  fps                 REAL,
  sampling_policy     TEXT,              -- videos: 'smart' | 'seq' | 'seek' | 'keyframe' | 'adaptive:<tau>'
  thumbnail_ref       TEXT,
  n_frames            INTEGER NOT NULL DEFAULT 0,
  first_run_id        INTEGER,
  last_run_id         INTEGER
);

CREATE TABLE IF NOT EXISTS asset_files(
  source_id      INTEGER NOT NULL REFERENCES sources(source_id),
  rel_path       TEXT NOT NULL,
  size           INTEGER NOT NULL,
  mtime_ns       INTEGER NOT NULL,
  kind           TEXT NOT NULL,          -- 'photo' | 'video' (from the extension)
  ext            TEXT NOT NULL,
  asset_id       TEXT REFERENCES assets(asset_id),   -- NULL when the file could not be read
  status         TEXT NOT NULL,          -- 'done' | 'error' | 'truncated' | 'timeout' | 'missing'
  error_code     TEXT,
  n_emb          INTEGER NOT NULL DEFAULT 0,
  sidecar_rel    TEXT,                   -- matched sidecar, relative to the source root
  last_run_id    INTEGER,
  PRIMARY KEY(source_id, rel_path)
);
CREATE INDEX IF NOT EXISTS asset_files_asset ON asset_files(asset_id);

CREATE TABLE IF NOT EXISTS frames(
  asset_id         TEXT NOT NULL REFERENCES assets(asset_id),
  frame_index      INTEGER NOT NULL,
  timestamp_s      REAL,                 -- presentation time of the picked frame (videos)
  requested_time_s REAL,                 -- target time the sampler asked for (videos)
  sampling_method  TEXT NOT NULL,        -- 'image' | 'exact' | 'keyframe'
  thumbnail_ref    TEXT,
  PRIMARY KEY(asset_id, frame_index)
);

CREATE TABLE IF NOT EXISTS models(
  model_id   TEXT PRIMARY KEY,           -- e.g. 'open_clip/ViT-B-32/laion2b_s34b_b79k/fp16'
  name       TEXT NOT NULL,
  pretrained TEXT,
  dim        INTEGER NOT NULL,
  dtype      TEXT NOT NULL,
  preprocess TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embeddings(
  asset_id    TEXT NOT NULL,
  frame_index INTEGER NOT NULL,
  model_id    TEXT NOT NULL REFERENCES models(model_id),
  vector      BLOB NOT NULL,
  PRIMARY KEY(asset_id, frame_index, model_id),
  FOREIGN KEY(asset_id, frame_index) REFERENCES frames(asset_id, frame_index)
);

CREATE TABLE IF NOT EXISTS runs(
  run_id        INTEGER PRIMARY KEY,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  code_version  TEXT,
  config_json   TEXT,
  report_json   TEXT                     -- aggregate report (same content as report.json)
);

-- Per-file performance telemetry of the last run that processed the file (not part of the engine contract).
CREATE TABLE IF NOT EXISTS file_stats(
  source_id      INTEGER NOT NULL,
  rel_path       TEXT NOT NULL,
  run_id         INTEGER,
  decoder        TEXT,
  strategy       TEXT,
  frames_decoded INTEGER,
  seeks          INTEGER,
  probes         INTEGER,
  t_read_ms      REAL,
  t_decode_ms    REAL,
  t_xform_ms     REAL,
  t_write_ms     REAL,
  PRIMARY KEY(source_id, rel_path)
);

-- Read-only views with the v0 column names, so existing benchmark tools keep working (deprecated).
CREATE VIEW IF NOT EXISTS files AS
  SELECT s.root_prefix || f.rel_path AS path, f.size, f.mtime_ns, f.kind, f.ext, f.status, f.asset_id AS cid,
         st.decoder, st.strategy, f.n_emb, f.error_code AS error, a.codec, a.width, a.height, a.duration_s AS dur_s,
         st.frames_decoded, st.t_read_ms, st.t_decode_ms, st.t_xform_ms, st.t_write_ms, st.seeks, st.probes
  FROM asset_files f JOIN sources s ON s.source_id = f.source_id
  LEFT JOIN assets a ON a.asset_id = f.asset_id
  LEFT JOIN file_stats st ON st.source_id = f.source_id AND st.rel_path = f.rel_path;

CREATE VIEW IF NOT EXISTS emb AS
  SELECT s.root_prefix || f.rel_path AS path, fr.frame_index AS idx, COALESCE(fr.timestamp_s, 0.0) AS pts,
         e.vector AS vec, CASE fr.sampling_method WHEN 'image' THEN NULL ELSE fr.sampling_method END AS method
  FROM asset_files f JOIN sources s ON s.source_id = f.source_id
  JOIN frames fr ON fr.asset_id = f.asset_id
  JOIN embeddings e ON e.asset_id = fr.asset_id AND e.frame_index = fr.frame_index
  WHERE f.status != 'error' AND f.n_emb > 0;
