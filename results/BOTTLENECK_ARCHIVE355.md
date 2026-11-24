# Why the 355 GB ingest took 98 minutes, and how to make it faster

22 Nov 2026 · Source: the reference run in `results/archive_validation.json` plus per-file telemetry (`file_stats`) in the archive manifest, aggregated by size class. Aggregate numbers only.
"Measured" numbers come from these runs. "Projection" means an estimate that has not been measured yet.

## 1. The short answer
The run was **limited by the USB hard drive, not by the CPU, the GPU or the pipeline**.
- **Single reader busy 90% of the time:** it spent about 5,310 s of the 5,890 s wall time reading.
  - Photos: 3,438 s.
  - Videos of 256 MB or less: 1,876 s.
- **GPU nearly idle:** busy 465 s, 8% of the run.
- **CPU:** mean load 22%.
- **Peak memory:** 4.3 GB of 16 GB.

The time went to two things:

| Cost | Measured | Share of wall time |
|---|---|---|
| **Streaming bytes** at the drive's ceiling: files of 10–256 MB read at 90–99 MB/s | ~2,950 s | ~50% |
| **Per-file latency on small files:** 34,937 photos under 1 MB (2% of the bytes) read at only 7.5 MB/s, about 29 ms of seek and file-open overhead each | 1,018 s | 17% |
| **Files of 1–10 MB:** 22,972 photos and 2,538 videos, read at 45–47 MB/s, half the drive's speed, for the same per-file overhead | ~1,820 s | 31% |

**Floor for this approach:** reading all 337 GB at the drive's ~95 MB/s sequential speed takes about **59 minutes**. The gap from 59 to 98 minutes is the per-file overhead.

**Not included in the 98 minutes:** the separate `inventory --headers` pass (33 min). It ran only to report metadata coverage. A normal `roamingest run` probes metadata from bytes already in memory and does not need it.

## 2. Where the wasted work is

| Waste | Measured size | Why it happens |
|---|---|---|
| **Byte-identical duplicates read and decoded again** | 18,938 files: 17,362 photos (20.6 GB) and 1,576 videos (32.4 GB). **53 GB, 14% of all bytes** | The reader reads the whole file before computing the content ID. Deduplication happens only after the bytes are already read. |
| **Small files read in directory order** | 34,937 files under 1 MB, at 7.5 MB/s | Each file costs an NTFS lookup plus a seek. Directory order is not on-disk order, so the head jumps around. |
| **Videos decoded 20.7 frames for each frame kept** | 808,013 decoded, 39,101 kept | Exact sampling decodes from the keyframe up to each target time. Not on the critical path today (decode runs in parallel with reading), but it would be on a fast drive. |
| **GPU idle 92% of the time** | 465 s busy of 5,890 s | Not waste as such: spare compute that a stronger model could use without adding wall time. |

## 3. How to make it faster

> **Update:** items 1 and 2 are now built and measured: **98.2 → 87.1 min with output bit-identical**, and both are the defaults. Duplicate skip saved the projected 53 GB. Disk order gained less than projected (small files 29 → 25 ms). See `EXP_SPEED_DEDUP_ORDER.md`.

Ranked by expected gain on this archive and drive. Each item is a projection to measure, with output-equality checks as in Phase 1, before it becomes a default.

| # | Change | Projected gain | Risk and check |
|---|---|---|---|
| 1 | **Check for duplicates before the full read.** When a file's size matches an asset already seen, read only its first and last 64 KiB (the content ID). If the ID is already known, link the file without reading or decoding the rest. | **−53 GB read, about −15 min (−15%).** Benefits Takeout-style archives most, with their many album copies. | Requires the decoder route to depend on content, not path, so every copy would embed identically. Check: the manifest is identical to a full read, apart from timing. |
| 2 | **Read in on-disk order.** Sort files by the physical start of their data (`FSCTL_GET_RETRIEVAL_POINTERS`, which needs no admin rights) instead of directory order. | Small-file latency from ~29 ms to perhaps 10–15 ms: **about −8 to −15 min.** | Measure on a sample first. Determinism is unaffected, because output does not depend on read order. |
| 3 | **Two outstanding reads for small files only.** Lets the drive reorder requests (NCQ over UAS). One reader stays for large files, which measured slower with several readers. | Unknown, to measure. | Phase 1 found concurrent readers slower on large files; this would apply only to files under 1–2 MB. |
| 4 | **Faster storage.** An external NVMe/USB-C SSD reads at 400–1,000 MB/s. | The run becomes CPU-bound: about 7,300 s of total CPU stage time over about 12 effective cores gives **15–20 min (projection)**. | A hardware choice. Copying to the SSD first does not help a single pass, because the bytes are still read once from the hard drive. |
| 5 | **Change detection from the NTFS change journal** (USN journal) instead of checking every file's size and modification time. | Unchanged rerun from 60 s to a few seconds. | Needs a fallback when the journal has wrapped around. |
| 6 | **Use the idle GPU for a stronger model** (ViT-L/14 or SigLIP). | No wall-time cost while the drive is the bottleneck. **The gain is search quality, not speed.** | Only once the retrieval evaluation exists (Phase 4) to show it is better. Re-embedding needs no re-read if decoded frames are cached, otherwise it costs one full read. |
| 7 | **Keyframe or adaptive sampling** for long videos. | Matters only once the drive is no longer the limit. | Already measured: failed the coverage gate (`results/EXP_SAMPLING.md`). Stays opt-in. |

**Combining items 1 and 2 on the same drive:** about 98 min → **65–75 min (projection)**. The 59-minute floor can only be beaten by reading fewer bytes (item 1) or by faster storage (item 4).

## 4. What is not worth doing (evidence already in `results/`)
- **Rust or C++ rewrite:** the pipeline's own overhead is not the limit (ADR-001). The drive is.
- **Faster JPEG decoding:** photo decode is 2,156 s of CPU time spread over 12 workers, hidden behind the reader. GPU JPEG decoding (nvJPEG) was 6× slower anyway.
- **Bigger GPU batches or less padding:** the GPU is idle 92% of the time.
