# Step 5: unified photo + video ingestion (`bench/ingest.py`)

Date: 21 Nov 2026 · tested on the set A (124 JPEG photos, 46 videos, 2.3 GB, internal SSD).

## What it is
One command ingests any mix of folders into a SQLite manifest (path, size, mtime, content ID, decoder used, per-stage timings, status) plus
CLIP ViT-B/32 embeddings (photos: 1 each; videos: up to 32 frames each) and photo thumbnails. Design rules, each from a measured result:

| rule | why (evidence) |
|---|---|
| Read every file **once**, into memory; content ID computed from those bytes | cold runs read files twice and often hit disk twice (`EXP_COLD.md` §4) |
| Reader threads per device: 1 for a hard drive, 4 for an SSD (`--storage`, `--readers`) | USB drive: 69 MB/s with 1 reader vs 55 with 2+; SSD: 2.3× faster with more threads |
| Photo reads bounded by a byte budget (384 MB) | stops RAM growing with archive size |
| JPEG: reduced-size decode (2× headroom); HEIC/PNG/WebP: full decode | passed the search-level gate (`EXP_IMAGES.md`) |
| Videos: separate CPU and NVDEC worker pools; decoder chosen by a hash of the file's path and size (default 40% NVDEC), no work stealing | hybrid decode was 1.5× faster than CPU-only, but dynamic routing changes pixels between runs (`EXP_HYBRID.md`) |
| Long videos: seek instead of decode-everything when total frames > 250 × targets; frame-count budget (20,000) plus a safety timeout | seek is cheaper only for sparse sampling (`EXP_B.md`); a 2-hour film has about 170,000 frames |
| GPU batches always padded to 64 rows | a row's embedding must not depend on its batch neighbours |
| File marked done only when its embeddings are committed in the same transaction | crash-safe resume |
| Optional `--video-prefetch-mb N`: videos up to N MB are read whole by the photo reader and decoded from memory | on the USB drive: 13% faster, decoders stop waiting on the drive (`EXP_HETERO.md`); output bit-identical on the set A |
| Frames converted to model inputs as they are picked | never hold 32 4K frames per video |
| Output size cap (8 GB) and a free-space guard (stops writing below 15 GB free) | disk-budget rule |

Console and report output contain aggregate numbers only (no names or paths).

## Correctness tests (`bench/verify_ingest.py`), all passed

| test | result |
|---|---|
| T1 determinism: 5 photo workers + 1 reader + 3 CPU/1 NVDEC video workers; and phased schedule with hard-drive settings | 170/170 files, **0 embedding differences** vs the default run (bit-identical) |
| T2 kill and resume: hard kill (process exit, no cleanup) after the 100th committed file; rerun | 100 committed before the kill, **100 skipped on resume, 70 processed**, final 170/170 files, **0 embedding differences** vs the uninterrupted run |
| T3 unchanged rerun | **0 files processed, 170 skipped** |
| T4 seek vs sequential (CPU only, forced strategies) | all 46 videos: **0 differences** (identical frames, identical embeddings) |

Not tested: T5 (comparing against the earlier experiment code's embeddings; batch composition differs there), HEIC/MKV/AVI inputs (first real exposure is the personal archive run), and kill points other than just after a commit.

## First full run on the set A (default settings, cold-ish cache, single run)
- 170 files in **49.4 s**: 124 photos + 46 videos, 124 photo embeddings + 318 video-frame embeddings (matches the experiment code's frame count), 0 errors.
- CPU 85%, GPU 14% busy, NVDEC engine 39%, peak RSS 3.2 GB, peak VRAM 1.5 GB (includes about 0.9 GB display use), disk 48 MB/s.
- GPU waited for input 45 of 49 s. 79% of GPU batch rows were padding, because input arrives slowly; this is harmless while the GPU is idle anyway.
- Video decode took most of the time: the 46 videos set the wall time (about 42 s for videos alone in the earlier hybrid test); photos overlapped with them.

## Limits and open items
- The deterministic routing rule is fixed (hash < 40%), not tuned. Its cost vs dynamic work stealing was not measured.
- Full-file hashing was replaced by a partial hash (size + first and last 64 KiB) for speed; edits in the middle of a file that keep size and mtime would go unnoticed.
- Duplicate files are detected only afterwards from the manifest (identical content IDs), not skipped.
- Per-video wall-clock timeout is a non-deterministic safety net; counts of timeouts are reported.
