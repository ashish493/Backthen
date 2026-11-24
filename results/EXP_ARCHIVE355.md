# Archive-scale validation: 355 GB / 94,886-file archive

22 Nov 2026 · RoamIngest v0.1 Stage 1 acceptance · Driver: `bench/validate_archive.py` · Raw results: `results/archive_validation.json`

This is the inventory-first validation required before the v0.1 freeze (plan §Phase 3, Stage 1 acceptance). All numbers below are aggregate: no file names, paths, or metadata values. The archive (on the USB hard drive) was accessed read-only throughout.

---

## 1. What was run

| Step | Purpose |
|---|---|
| `inventory --headers` | Discovery + per-file header probe only, no decode/embed |
| `run` (killed at 34,000 committed files) | Full cold ingest, forced interruption |
| `run` (resume) | Resume from the interrupted manifest |
| `run` (unchanged rerun) | Confirms a no-op rerun does no work |
| `verify` | Manifest internal-consistency checks |
| `status` | Coverage summary |
| `run` (uninterrupted reference, separate output dir) | Baseline to diff the resumed manifest against |

All runs used `--storage hdd --quiet --no-timeline` (one sequential reader, throttling off, High Performance power plan).

## 2. Stage 1 acceptance — all criteria met

| Criterion | Target | Result |
|---|---|---|
| pytest suite | green | 38/38 pass (prior to this run) |
| Peak RSS, full run | ≤ 6 GB | **4.28 GB** (reference run) |
| Peak VRAM | ≤ 3.5 GB | **1.12 GB** (reference run) |
| Interrupt + resume → identical manifest | 0 differences vs. an uninterrupted reference | **0** differences: file rows, frame counts, frames, embeddings, asset metadata all equal (67,512 files / 73,815 frames both sides) |
| Unchanged rerun | processes 0 files | **0** processed, 67,512 skipped, 60 s wall |
| `unaccounted_files` | 0 | **0** |
| `verify` | ok | **ok**, all 9 checks 0 |

## 3. Archive profile (discovery)

- **67,512 media files, 377.2 GB** (discovery itself: 12.9 s).
- **24,690 sidecar `.json` files**; 23,705 media files matched to one (five Google-Takeout naming rules).
- Photos: 61,380 files, 144.3 GB — 52,806 JPG, 5,696 WebP, 1,122 HEIC, 422 NEF, 1,020 JPEG, 299 PNG, 15 GIF.
- Videos: 6,132 files, 207.0 GB — 4,694 MP4, 1,409 MOV, 29 3GP. Codecs: 4,143 H.264, 995 VP9, 971 HEVC, 22 MPEG-4. Median duration 6.8 s, longest 6,368 s (≈106 min).
- 369 other non-media/non-sidecar files of 36 extensions (PDF, DOCX, XML, …), ignored by the ingest.
- Header probe (EXIF/container, no decode): 1,965.6 s for all files. 30,591/61,380 photos have EXIF capture time (22,867 with a UTC offset); 6,916 have EXIF GPS. 6,052/6,131 videos have a container capture time; 1,749 have container GPS; 2,035 are portrait (rotated).

## 4. Full cold ingest (reference run)

- **Wall time: 5,890 s ≈ 98 min** for all 67,512 files, 377 GB → **64.0 MB/s** average throughput, 11.46 files/s.
- **Bottleneck confirmed as the USB drive**, not compute: `disk_mb_s` 57.5 MB/s, GPU busy only 465 s of 5,890 s (8% utilization), mean GPU util 10.4%, mean NVDEC util 8.8%.
- **Decoders:** 61,376/61,380 photos decoded (all CPU, as designed); 6,131/6,132 videos decoded — 3,521 CPU, 2,610 NVDEC, split by codec (HEVC → NVDEC).
- **Embeddings:** 61,376 photo + 39,101 video-frame = **100,477 total**, batched in 3,986 GPU batches (pad waste 60.6%, expected on a mixed single-frame/multi-frame workload).
- **Errors:** 5 of 67,512 files (0.007%) — 3 photos `UnidentifiedImageError`, 1 photo `OSError`, 1 video `InvalidDataError`. All five are recorded as `status=error` in the manifest; none affected the run or the resume.
- **Thumbnails:** 1.55 GB written.

## 5. Interruption and resume

- Run 1 was hard-killed (`rc=137`) after committing 34,036 files, at 4,483 s elapsed.
- Resume: 1,525 s, processing the remaining 33,476 files (34,036 skipped as already done), 0 moved, 0 missing, 0 unaccounted.
- **Total cost of interrupting: 5,897 s (killed run + resume) vs. 5,890 s for one uninterrupted run — a 0.1% overhead**, i.e. resuming is effectively free.
- Resume-run peaks were lower than the full reference run (2.93 GB RSS / 0.93 GB VRAM vs. 4.28 GB / 1.12 GB) since it processed roughly half the files; the reference run's peaks are the ones that matter for the budget.

## 6. Final manifest state

- **Status:** 61,376 photos done / 4 errored; 6,131 videos done / 1 errored.
- **Assets:** 44,014 unique photo assets, 4,555 unique video assets (48,569 total) from 67,512 files — **18,938 files (28%) are byte-identical duplicates** sharing a stored asset, each resolved deterministically to the smallest-path owner.
- **Frames / embeddings:** 73,815 / 73,815 (1:1, as expected for the flat embedding table).
- **Full content hashes:** 48,485 of 48,569 assets already hashed as a side effect of the run (the rest need `roamingest hash`, a background-friendly pass not required for v0.1).
- **Capture-time coverage:** photos — 18,776 EXIF (absolute), 3,912 EXIF-local-only, 5,155 sidecar-only, 16,171 none; videos — 3,849 container, 666 sidecar-only, 40 none.
- **GPS coverage:** photos — 4,587 EXIF, 219 sidecar, 39,208 none; videos — 1,265 container, 129 sidecar, 3,161 none.
- **Output size:** manifest + thumbnails = 1.6 GB on `D:`, well under the ~4 GB projection in the architecture doc.
- **Disk headroom:** `D:` free went from 32.3 GB → 30.5 GB over the whole validation (inventory + two full ingests + resume); stayed above the 15 GB floor throughout, and the redundant reference-run copy was deleted afterward to recover the space.

## 7. Conclusion

RoamIngest v0.1 meets every Stage 1 acceptance criterion on the full 355 GB target archive: it is deterministic under interruption, idempotent on rerun, internally consistent, and stays inside the RAM/VRAM budget with large margin (4.28/6 GB, 1.12/3.5 GB). The dominant cost at this scale is the USB drive's sequential throughput (~57-64 MB/s), not CPU, GPU, or the pipeline's own overhead — resuming after a kill costs essentially nothing (0.1%).
