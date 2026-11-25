# RoamIndex: architecture assessment and staged plan

Version 0.1 · 22 Nov 2026 · Status: **Stage 1 (RoamIngest v0.1) complete, tagged `v0.1`. Stages 2–5 and 7 implemented in a first version (RoamEngine 0.1, RoamIndex 0.1); Stage 3's retrieval evaluation awaits user labels.**
Grounded in the repository at commit `2822453`. "Measured" means a number in `results/`; "projection" means an estimate not yet measured.

**Update, 22 Nov 2026:** Stage 1 is implemented (`packages/roam-ingest`), validated on the full 355 GB / 94,886-file archive, and meets every Stage 1 acceptance criterion — see `results/EXP_ARCHIVE355.md`. Package docs: `packages/roam-ingest/README.md`, `CHANGELOG.md`. Manifest schema: `shared/schemas/manifest_v1.sql` (ADR-008).

---

## 1. Current implementation inventory

### Exists and tested
| capability | where | evidence |
|---|---|---|
| Recursive discovery, media classification by extension, skip counts | `bench/ingest.py` `discover` | 3 archives, 0 discovery errors |
| Partial content ID (size + first/last 64 KiB, xxh3-128) | `cid_from_*` | used for change detection |
| Read-once photo I/O with byte budget; 1 reader on HDD, 4 on SSD | `reader`, `ByteBudget` | `EXP_COLD.md`, `EXP_HETERO.md` |
| JPEG reduced-size decode (2× headroom); HEIC/PNG/WebP full decode | `decode_photo`, `bench/stages.py` | quality gate passed, `EXP_IMAGES.md` |
| Video decode: CPU + NVDEC pools, deterministic routing (hash or codec) | `video_worker`, `video_queue` | `EXP_HYBRID.md`, `EXP_POWER.md` |
| Video sampling: seq, seek, **smart** (default), keyframe, adaptive; NVDEC seeks | `video_emits` | T4–T6, `EXP_SAMPLING.md` |
| CLIP ViT-B/32 fp16 embeddings, fixed-shape batches | `gpu_thread` | bit-identical reruns (T1) |
| SQLite manifest (`files`, `emb`), crash-safe commit, resume, unchanged-rerun skip | `writer`, `open_db` | T2, T3, T7 |
| Photo thumbnails (512 px) | `photo_worker` | – |
| Resource monitor, per-second timelines, process read bytes, power-throttling control | `Monitor`, `set_power` | `EXP_POWER.md` |
| Benchmark harness, drivers, aggregate reports | `bench/*` | `results/*` |
| Correctness tests T1–T7 | `bench/verify_ingest.py` (script, subprocess-based) | 14/14 pass |

### Partially implemented
| capability | what exists | what is missing |
|---|---|---|
| Metadata | width, height, duration, codec | **capture time, GPS, orientation, camera**; Google Takeout `.json` sidecars (24,682 in the 355 GB archive) |
| Asset identity | path is the primary key; partial content ID | stable `asset_id`; full content hash; duplicate grouping; moved-file detection |
| Video frames | embedding + timestamp + sampling method per frame | frame IDs, requested time, optional frame thumbnails, video poster thumbnail |
| Manifest | two tables, implicit schema | schema version, model registry, run records, source roots, relative paths |
| Model tracking | model hard-coded | model ID, preprocessing and dtype stored per embedding |
| Formats | JPEG, HEIC, PNG, WebP, MP4, MOV, MKV, AVI, M4V, WebM | **NEF RAW (422 files)**, GIF (15), **3GP (29)** in the target archive |
| API | CLI flags (31) | Python SDK, progress events, cancellation; the core is one 860-line function with nested closures |
| Tests | subprocess script on a personal dataset | pytest, synthetic fixtures runnable anywhere |
| Error reporting | exception class name per file | per-stage error codes, retry policy |

### Entirely new
RoamEngine (catalogue, vector index, text queries, filters, ranking, incremental index); RoamIndex desktop app; shared schemas and contracts; retrieval evaluation set and metrics.

---

## 2. System architecture

```
                    ┌──────────────────────────── RoamIndex (Tauri + React) ───────────────────────────┐
                    │  library setup · ingestion progress · search · gallery · map · timeline · viewer │
                    └───────────────┬───────────────────────────────────────────────┬──────────────────┘
                     task API (start/pause/progress)                    query API (search, asset, related)
                                    │                                               │
   original media      ┌────────────▼────────────┐   Contract A: dataset   ┌──────────▼──────────┐
   (read-only) ───────►│        RoamIngest       │──── manifest v1 ───────►│      RoamEngine     │
   folders, drives     │ discover·probe·plan·    │   (SQLite + derived     │ catalogue·indexes·  │
                       │ decode·sample·embed·    │    files, versioned)    │ retrieval·ranking·  │
                       │ persist·validate        │                         │ incremental updates │
                       └─────────────────────────┘                         └─────────────────────┘
                                    └──────────── shared: schemas · IDs · config · logging · benchmarks ─────┘
```

### Responsibility and dependency matrix
| component | owns | depends on | must not depend on |
|---|---|---|---|
| RoamIngest | discovery, probing, decoding, sampling, embeddings, manifest writing, its benchmarks | shared schemas, PyAV/FFmpeg, Pillow, pillow-heif, PyTorch/OpenCLIP, SQLite | RoamEngine, RoamIndex |
| RoamEngine | asset catalogue, vector/metadata/text/geo indexes, query parsing, retrieval, ranking, incremental index updates, retrieval evaluation | shared schemas, manifest v1 (as data), FAISS, SQLite (FTS5, R-Tree), same embedding model family (text encoder) | RoamIndex, RoamIngest internals |
| RoamIndex | UI, library and task management, presentation | RoamIngest and RoamEngine **public** APIs only | any internal module |
| shared | schemas, ID rules, config conventions, logging format | – | everything else |

### Data flow
```
files ─► discover (path,size,mtime) ─► probe (format, codec, duration, EXIF/sidecar time+GPS) ─► plan (route, sampling, storage)
      ─► read once (byte budget) ─► decode (reduced-size JPEG / full / CPU or NVDEC video) ─► sample frames (smart)
      ─► model inputs ─► CLIP image embeddings (fp16, fixed batches) ─► manifest v1 (assets, frames, embeddings, runs)
      ─► RoamEngine import (validate schema/model) ─► catalogue + FAISS + FTS5 + R-Tree ─► query ─► ranked results with timestamps
```

---

## 3. Manifest v1 proposal (Contract A)

**One SQLite file plus derived files, described by relative references.** SQLite is already the tested store (crash-safe commits, resume). Embeddings stay in SQLite as fp16 blobs in v1: 1 KB each, about 0.3 GB for the 355 GB archive (projection: 61k photos + about 120k frames). RoamEngine copies them into its own index.

```
dataset/
  manifest.sqlite          # tables below
  thumbnails/ab/<asset_id>.jpg
  frames/ (optional)       # selected frame JPEGs, off by default
  report.json              # last run aggregate report (same as --report today)
```

| table | key columns | notes |
|---|---|---|
| `meta` | `key`, `value` | `schema_version=1`, `roamingest_version`, `created_at` |
| `sources` | `source_id`, `root_path`, `label`, `storage` | archives are re-rooted by changing one row (moved drive letters) |
| `assets` | `asset_id` (PK), `source_id`, `rel_path`, `size`, `mtime_ns`, `content_id_partial`, `content_hash_full` (nullable), `media_type`, `format`, `status`, `error_code` | file identity = (`source_id`, `rel_path`); content identity = hashes |
| | `width`, `height`, `orientation`, `capture_time_utc`, `capture_time_source` (`exif`/`sidecar`/`container`/`mtime`), `lat`, `lon`, `alt`, `gps_source` | new: probing stage |
| | `codec`, `duration_s`, `fps`, `thumbnail_ref`, `last_run_id` | video fields null for photos |
| `frames` | `frame_id` (PK), `asset_id`, `frame_index`, `timestamp_s`, `requested_time_s`, `sampling_method` (`exact`/`keyframe`), `thumbnail_ref` (nullable) | today's `emb` rows + provenance |
| `embeddings` | `owner_type` (`asset`/`frame`), `owner_id`, `model_id`, `vector` (fp16, L2-normalised) | photos: one per asset; videos: one per frame |
| `models` | `model_id`, `name`, `pretrained`, `dim`, `dtype`, `preprocess` (224 px bicubic crop, CLIP mean/std) | engine checks compatibility on import |
| `runs` | `run_id`, `started_at`, `finished_at`, `config_json`, `code_version`, `report_json` | one row per ingest run |

**Migration from today's v0 (`files`, `emb`):** a one-off script maps `files.path` to (`source`, `rel_path`) and `emb` to `frames` + `embeddings`. It sets `model_id` to the one model used so far and `sampling_method` from `emb.method` (null means `exact`).

---

## 4. Public SDK and CLI proposal (illustrative names)

```python
from roamingest import Ingestor, IngestConfig
cfg = IngestConfig(storage="hdd", sampling="smart", max_ram_gb=6, max_vram_gb=3.5, throttle="off")
report = Ingestor(cfg).run(sources=["E:/archive"], output="D:/roam_dataset",
                           on_progress=lambda ev: ..., cancel=token)     # resumable: rerun continues
```
```
roamingest inventory SRC...                 # probe only, aggregate report, no decoding
roamingest run SRC... --out DATASET [--storage hdd|ssd] [--sampling smart|keyframe|adaptive]
roamingest status DATASET                   # counts, errors, last run
roamingest verify DATASET [--full-hash]     # schema, references, optional content re-hash
```
Progress events: `discovered`, `file_done`, `file_error`, `stage_timing`, `budget`, `finished` (counts only, no content). That is everything RoamIndex's task manager needs. Pause means cancel plus resume, which is already crash-safe.

RoamEngine (Stage 2+): `MediaIndex.open(path)`, `.import_dataset(dataset)`, `.search(query, filters, limit)` returning `asset_id`, `score`, `media_type`, `thumbnail_ref`, `original_ref`, `matched_frame.timestamp_s`.

---

## 5. Monorepo structure and migration plan

Target (trimmed to what is needed now):
```
packages/roam-ingest/src/roamingest/{discovery,probing,planning,images,videos,enrichment,persistence,scheduling,validation}/
packages/roam-ingest/tests/            # pytest, synthetic fixtures generated at test time
packages/roam-engine/                  # Stage 2
shared/schemas/manifest_v1.sql         # the contract, versioned
bench/  results/                       # kept as the research record (unchanged paths)
docs/architecture/  docs/decisions/
apps/roamindex/                        # Stage 7, not created before then
```
**Migration steps (each step keeps `verify_ingest` green):**
1. Add `packages/roam-ingest` with `pyproject.toml`; install editable into `.venv`.
2. Move pure functions first: `discover`, content IDs, `decode_photo`, `stages.py` transforms, `video_emits`, `probe_codec`. Then turn the closures in `run()` into small classes: `Reader`, `PhotoWorker`, `VideoWorker`, `Embedder`, `ManifestWriter`, `Scheduler`.
3. `bench/ingest.py` becomes a thin wrapper over the package, so every experiment script and result stays reproducible.
4. Port T1–T7 to pytest against **synthetic fixtures** (generated JPEG/HEIC/PNG and short H.264/HEVC clips). The set A becomes an optional integration suite.
5. Implement manifest v1, the migration from v0, the probing stage, RAW/GIF/3GP, and the CLI.

---

## 6. The 15 design questions

| # | question | recommendation (MVP) | later |
|---|---|---|---|
| 1 | Stable IDs across moves/renames | `asset_id` = content-derived: partial ID now, full hash when known. A move keeps the ID; the path is updated | – |
| 2 | Content vs file identity | separate: file = (`source`, `rel_path`, `size`, `mtime`); content = hashes. Several files may share one content (duplicates) | perceptual near-duplicate groups |
| 3 | Schema evolution | `meta.schema_version` + forward migrations; engine refuses unknown major versions | – |
| 4 | Embedding model compatibility | `models` table; engine indexes per `model_id`; re-embedding is a new run with a new model | multiple models side by side |
| 5 | Image/video-frame relationships | `frames.asset_id`; video results carry the frame timestamp | scene grouping |
| 6 | Timestamp and sampling provenance | `frames.timestamp_s`, `requested_time_s`, `sampling_method`; run config stored | – |
| 7 | Original vs derivative references | originals = `sources.root_path` + `rel_path` (never copied); derivatives = paths relative to the dataset | – |
| 8 | Index consistency and recovery | ingest: per-file transactions (exists). Engine: import in transactions keyed by `run_id`, rebuildable from the manifest | – |
| 9 | Deleted, moved, modified files | rerun detects: missing path → `status=missing`; same content at new path → move; size/mtime change → re-process | filesystem watching |
| 10 | Duplicates and near-duplicates | exact: same content hash → one asset, several file rows. Near: not in MVP | CLIP-similarity clusters |
| 11 | Index rebuild and migration | engine index is a cache: always rebuildable from manifest v1 | – |
| 12 | Ranking and evaluation | cosine top-k + metadata filters; Recall@K / nDCG on a labelled evaluation set | learned fusion |
| 13 | Memory and storage limits | already enforced (byte budget, output cap, free-space guard); FAISS flat index ≈ 0.4 GB for 200k vectors (projection) | IVF/PQ beyond ~1M vectors |
| 14 | Offline operation and privacy | everything local; no telemetry; reports aggregate-only by default; personal paths never leave the manifest | – |
| 15 | Cross-platform filesystems and codecs | relative paths, case-insensitive matching on Windows; codec support recorded per asset; NVDEC optional with CPU fallback (exists) | macOS/Linux validation |

---

## 7. Staged roadmap

| stage | objective | builds on | new | tests and acceptance | out of scope |
|---|---|---|---|---|---|
| **1. RoamIngest v0.1** ✅ done | package, manifest v1, probing (time/GPS/sidecars), RAW/GIF/3GP, CLI; validated on the 355 GB archive | `bench/ingest.py`, `stages.py`, `verify_ingest.py` | `packages/roam-ingest`, `shared/schemas/manifest_v1.sql`, pytest suite | pytest 38/38; full run of 67,512 files (377 GB) at 4.28 GB RAM / 1.12 GB VRAM peak; interrupt + resume gives a manifest identical to an uninterrupted reference run (0 diffs); unchanged rerun processes 0 files — all measured, `results/EXP_ARCHIVE355.md` | OCR, audio, near-duplicates |
| **2. Contracts + engine import** ✅ v0.1 (read-only catalogue, schema check, live reload) | RoamEngine reads manifest v1 into a catalogue | manifest v1 | `packages/roam-engine` (`catalogue`, `ingestion`) | import validates schema and model; re-import is idempotent | search |
| **3. Image semantic search** ✅ built; ⏳ eval needs labels (Evaluate view built). Exact search, query p95 37 ms on the 355 GB index (ADR-009) | text → CLIP text embedding → FAISS top-k → ranked assets | catalogue, embeddings | `embeddings`, `indexing`, `retrieval` | Recall@10 / nDCG@10 on labelled queries vs a random baseline; query latency p95 < 200 ms on the 355 GB index (target) | UI |
| **4. Video frames + timestamps** ✅ moments with timestamps, playback from the moment | frame-level retrieval, best-frame timestamp per video | `frames` table | frame aggregation in ranking | video queries return correct timestamps on labelled clips | scene detection |
| **5. Time, place, metadata filters** ✅ partial: dates, type, GPS radius/bbox, offline map (ADR-011); place names pending | date ranges, GPS radius, place names (offline gazetteer), media type | probing fields | R-Tree, FTS5, query parsing | filter correctness tests; combined query evaluation | online geocoding |
| **6. Ranking, evaluation, incremental updates** | add/update/delete/move without full rebuild | runs, content IDs | `incremental`, evaluation suite | after random edits, an incremental index equals a full rebuild | live file watching |
| **7. RoamIndex desktop MVP** ✅ v0.1 (Tauri; engine as local sidecar, ADR-010) | library, progress, search, gallery, viewer with timestamp jump, basic map | SDK + engine API | `apps/roamindex` (Tauri + React + TS) | end-to-end task tests; UI never imports internals | sharing, cloud |
| **8. Public-safe demo** | curated public dataset, reproducible demo index, docs | everything | demo dataset, docs | runs on a second machine from a clean checkout | – |

---

## 8. Architecture decisions already made (records in `docs/decisions/`)
ADR-001 no Rust core yet · ADR-002 search-level quality gate for lossy fast paths · ADR-003 deterministic decoder routing · ADR-004 SQLite manifest with fp16 embeddings · ADR-005 read-once I/O and single sequential reader on hard drives · ADR-006 smart sampling by default, keyframe opt-in · ADR-007 opt out of OS power throttling by default.

## 9. Risks and unresolved questions
| risk / question | impact | mitigation or next step |
|---|---|---|
| Retrieval quality unknown: no labelled evaluation yet | engine could look fine and rank badly | Stage 3 evaluation set: labelled queries over a private photo collection (labels made by the owner; the images are not shared) |
| Sidecar `.json` format assumed to be Google Takeout (not yet inspected) | time/GPS coverage | inspect field names on a sample (aggregate) before writing the parser |
| Full content hashing on the USB drive costs a full read of 207 GiB of video (about 50 min, projection) | stable IDs | partial ID now, full hash in a low-priority background pass |
| Intermittent OS throttling (trigger unknown) | benchmark noise, user-visible slowness | default opt-out (done); keep interleaved benchmarks |
| CLIP ViT-B/32 may be too weak for fine-grained travel queries | search quality | model registry allows a second model; evaluate before switching |
| `ingest.py` closure design | hard to extend/test | migration step 2 |
| Single machine, single archive | generality | Stage 8, second machine |
| Personal data in tests | privacy | synthetic fixtures for unit tests; personal sets only for local integration runs |

## 10. Next five concrete tasks

**Stage 1 (items 1–5 below) is complete, tagged `v0.1`.** Kept here as the record of what was done; next tasks belong to Phase 4 (RoamEngine), planned separately.

1. ~~**Create `packages/roam-ingest` and move the pure functions**~~ — done; `verify_ingest` stayed 14/14 throughout the port.
2. ~~**Write `shared/schemas/manifest_v1.sql` and a v0 → v1 migration**~~ — done, with a migration test on a synthetic dataset and a real-data check against the 170-file set A.
3. ~~**Probing stage**~~ — done: EXIF capture time/GPS/orientation, video container tags, and a Google-Takeout sidecar parser (five naming rules, matching 23,705/23,468+ media files in the 355 GB archive).
4. ~~**pytest suite with synthetic fixtures**~~ — done, 38/38 pass, no personal data.
5. ~~**Inventory-first run of the 355 GB archive**~~ — done; see `results/EXP_ARCHIVE355.md`. The index is kept locally (1.6 GB).

**First tasks for Phase 4 (RoamEngine), to be detailed after planning:**
- `packages/roam-engine` skeleton: manifest-v1 reader, SQLite asset catalogue, FAISS flat index over `embeddings`.
- CLIP text encoder for natural-language search; a labelled query set (the owner's own photos, kept private) for Recall@K / nDCG@K.
