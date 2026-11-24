# Overnight plan: Steps 4–8 (unattended run, 21–22 Nov 2026)

> **Status: all steps completed on 21 Nov 2026. Results: `results/OVERNIGHT_REPORT.md`.**
> Changes from this plan: the "scaled cap" for long videos was not used (the cap stayed at 32 frames, per your answer); HEIC, MKV and AVI support was added;
> a follow-up (single sequential reader for videos too) and Hypothesis A (small files) were added.

Goal for the morning: **a proper benchmark of multimodal ingestion** (photos + videos, cold and warm, NVMe and USB HDD) using the
optimised pipeline, plus a short list of next steps. Everything stays on this machine.

## Data

| set | location | contents | role |
|---|---|---|---|
| A: personal set | D: (NVMe) | 124 JPG, 46 MP4/MOV, 2.3 GB | known baseline; fits in cache, so warm and cold both possible |
| B: personal archive folder | E: USB HDD, read-only | 2,379 JPEG + 115 HEIC, 162 MP4 + 35 MOV, 17.3 GiB | realistic diverse personal archive (about 13 photos per video) |
| C: movies/series | E: USB HDD, read-only | 102 MKV, 43 MP4, 1 AVI (134 GiB), plus subtitles, archives, a partial download | long-video, large-file and junk-file stress test only |

## Ground rules (from your answers)
- **E: is read-only.** No writes, no copies of your media anywhere.
- **D: budget:** synthetic photo copies (5.7 GB) deleted first; derived outputs ≤ 10 GB under `out/overnight/`; all writing stops if D: falls below 15 GB free.
- **Privacy:** console output and `results/*` contain aggregate numbers only (no file names, folder names, EXIF/GPS). Per-file rows use anonymous IDs.
  Nothing is uploaded or published. **After the benchmark, everything derived from E: media (thumbnails, embeddings, manifest with paths) is deleted.**
- **Long videos:** current policy (1 frame per 2 s, capped at 32 per video); films are a stress test, not indexed for use.
  Seek-based sampling for sparse targets (identical frames to sequential, per Experiment B), plus a per-video time budget so one file cannot stall the night.
- **Power:** High Performance plan, sleep/hibernate on AC are already "never". **Please leave the laptop plugged in with the lid open.**
  GPU temperature and clocks are logged to detect throttling.

## Step 4: cold-cache benchmarking (finishing now)
- Done: eviction method verified (physical reads = 1.00x bytes after evicting); NVMe storage curve; photo and video pipelines cold vs warm on NVMe.
- Remaining: USB HDD read curve (running); photo and video pipeline on the USB HDD, cold. Write `results/EXP_COLD.md`, and correct `EXP_PIPELINE.md` (its "warm" runs were mostly cold because the 5.7 GiB set does not fit in the ~5 GiB cache).

## Step 5: unified photo + video ingestion (`bench/ingest.py`)
- **Discovery:** several roots; photos {jpg, jpeg, png, webp, heic}, videos {mp4, mov, mkv, avi}. Everything else is skipped and counted by extension.
- **Manifest:** SQLite (WAL) in `out/overnight/`. One row per file: content ID, size, mtime, status, decoder used, per-stage timings, error type.
  Content ID = partial hash (size + first/last 64 KiB). A full hash of a 19 GB film would take minutes on the USB drive (cost reported in Step 6).
- **Workers:**
  - Photo pool: reduced-size JPEG decode (`draft_m2`, passed the search-level gate); HEIC/PNG/WebP use full decode.
  - Video workers: 2 CPU + 2 NVDEC. NVDEC only for H.264/HEVC; other codecs go to CPU.
  - One GPU thread runs CLIP ViT-B/32, batch 64.
- **Bounded memory:** queues bounded by bytes; video frames are turned into model inputs as they are picked (never hold 32 4K frames).
- **Deterministic:**
  - Sorted discovery.
  - Decoder chosen by a stable rule on the content ID (and the same fallback on the same failure), recorded in the manifest.
  - GPU batches always padded to 64, so a photo's embedding does not depend on what else was in its batch.
- **Resumable:** files marked done (same size + mtime + ID) are skipped. A crash or kill loses only in-flight files.
- **Tests on set A:**
  1. Output matches the experiment code (embeddings).
  2. Two runs with different worker timing give bit-identical embeddings.
  3. Kill mid-run, resume, and compare with an uninterrupted run (same files, identical embeddings).
  4. A rerun on an unchanged tree processes 0 files.
  5. Seek-based sampling picks the same frames as sequential on set A's videos and on the first minutes of a few long files.

## Step 6: full heterogeneous benchmark
- **Unified optimised pipeline vs serial baseline** on:
  - A, cold and warm.
  - B, cold. B is too large for the cache, so warm is not possible; a warm run on a subset that fits is the fallback.
  - A + B together.
- **Reported:**
  - Files/s and MB/s per kind.
  - Per-stage time share.
  - p50/p95 per file.
  - Queue waits: does the GPU wait for the CPU, or the CPU for the disk?
  - CPU / GPU / NVDEC utilisation, disk MB/s.
  - Peak RAM/VRAM against budget.
- **Hashing cost:** full vs partial on the USB drive.
- **Baseline only on A and a B subset** (serial is about 6 photos/s).

## Step 7: GPU JPEG experiment
- `torchvision.io.decode_jpeg(device="cuda")` (nvJPEG), batched, then GPU resize, compared with CPU `draft_m2` with 16 workers, and a mix of both.
- **Correctness:** same search-level gate as photos (PSNR ≥ 40 dB, similar-photo overlap within 2 points of the control).
- **Where:** on cached data, plus on the USB drive, to see whether a faster decoder matters once the disk is the limit.

## Step 8: larger real-world archive
- Full B + C (+ A) through the unified pipeline, cold.
- **Breakdown:**
  - By format, codec, resolution, bit depth and duration.
  - Skip/error counts: HEIC, MKV/AVI, partial download, subtitles, archives.
  - Seek behaviour on long files; per-video time-budget hits.
- **Next bottleneck:** identify it at real-archive scale.

## Rough schedule (adapts to actual run times)
| time from start | work |
|---|---|
| 0:00–0:45 | finish Step 4, delete synthetic copies |
| 0:45–2:30 | Step 5: build `ingest.py`, correctness/resume tests on A |
| 2:30–4:30 | Steps 6 and 8: benchmark runs (A, B, A+B, C) |
| 4:30–5:30 | Step 7: GPU JPEG |
| 5:30–6:30 | morning report, tracker update, cleanup of derived outputs from E: media |

## What you will find in the morning (all local)
- `results/OVERNIGHT_REPORT.md`: headline multimodal ingestion benchmark, what is now the bottleneck, and recommended next steps.
- `results/EXP_COLD.md`, `EXP_UNIFIED.md`, `EXP_HETERO.md`, `EXP_GPU_JPEG.md`, `EXP_REALWORLD.md`: details per step.
- `bench/ingest.py`: the unified, resumable pipeline.
- Derived outputs from E: media deleted; only aggregate results kept.

If something fails (crash, hang, driver error), I record it, resume from the manifest, and continue with the next step rather than stopping.
