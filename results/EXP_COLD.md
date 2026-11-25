# Step 4: cold-cache benchmarking and storage limits

Runs: `exp_storage*.jsonl`, `exp_pipeline_cold1984.jsonl`, `exp_pipeline_warm496.jsonl`, `exp_pipeline_cold496.jsonl`, `exp_hybrid_cold.jsonl`, `exp_hybrid_warm2.jsonl`
Date: 21 Nov 2026 · High Performance plan, AC. Aggregate numbers only (no file names or content from the personal set B folder are recorded here).

## Method: how "cold" was produced and verified
- Admin-free eviction: `bench/cache.py` opens each file once with `FILE_FLAG_NO_BUFFERING`, which purges it from the Windows file cache.
- **Verified by OS disk counters:** after eviction physical reads = 1.00× the bytes read (warm: 0.00×), on both a 580 MiB photo set and the 2.3 GB set A.
- Every cold run evicts every file first; every warm run reads every file once first. Disk bytes are recorded per run.

## 1. Storage devices (read throughput, whole files, N threads)

| device | files | avg file | cold, 1 thread | cold, best | cold, 16 threads |
|---|---|--:|--:|--:|--:|
| Internal NVMe SSD (D:), photos | 1,984 | 3.1 MB | 1,073 MB/s | 2,457 MB/s (16 thr) | 2,457 MB/s |
| Internal NVMe SSD (D:), set A incl. videos | 170 | 14.5 MB | 1,458 MB/s | 2,399 MB/s (16 thr) | 2,399 MB/s |
| **External USB hard drive (E:), photos (416, every 6th)** | 416 | 3.0 MB | **69 MB/s** (23 files/s) | **69 MB/s (1 thr)** | 52 MB/s |
| **External USB hard drive (E:), videos (25, every 8th)** | 25 | 117 MB | **90 MB/s** | **90 MB/s (1 thr)** | 74 MB/s |

- **On the NVMe SSD, more threads help** (1,073 → 2,457 MB/s, 2.3× from 1 to 16 threads).
- **On the USB hard drive, more threads hurt:** photos 69 → 55 MB/s from 1 to 2 threads (−20%) and 52 MB/s at 16; videos 90 → 74–78 MB/s (−15 to −18%).
  Concurrent readers force the disk head to move between files. **One sequential reader is best.**
- On the USB drive the 3 MB photos average 23 files/s even at the best setting, so **a 30,000-photo cold import is about 22 minutes of pure reading** (projection: 30,000 × 3 MB ÷ 69 MB/s).

## 2. Does cold cache change pipeline speed on the NVMe SSD?

| workload | config | warm | cold | cold vs warm |
|---|---|--:|--:|--:|
| photos, 496 | reduced-size decode, 16 workers (photos/s) | 79.1 / 82.4 / 74.7 | 92.5 / 83.0 / 85.1 | no slowdown |
| photos, 496 | exact full decode, 16 workers | 24.1 / 22.2 / 22.1 | 27.5 / 26.7 / 27.4 | no slowdown |
| photos, 1,984 | reduced-size decode, 16 workers | 93.0 (earlier run) | 88.4 / 85.2 / 88.1 | about −5% |
| videos, 46 (clips/s) | 1 CPU worker | 0.55 / 0.55 / 0.55 | 0.52 / 0.54 / 0.54 | −2 to −6% |
| videos, 46 | 4 CPU workers | 0.77 / 0.77 / 0.75 | 0.72 / 0.76 / 0.77 | 0 to −6% |
| videos, 46 | 2 CPU + 2 NVDEC workers | 1.09 / 1.10 / 1.06 | 1.08 / 1.07 / 1.06 | about 0% |

Disk throughput during the cold photo runs was 260–307 MB/s (reduced-size decode) and 84–113 MB/s (exact decode), and 23–45 MB/s for videos, all far below the SSD's 2.4 GB/s.
**On the internal NVMe SSD the pipeline is compute-bound, cold or warm.** Storage matters on the external drive, which is the typical home of large archives.

## 3. What the USB hard drive means for the pipeline (projection from the curves, not yet a pipeline run)
| | CPU/GPU capability (NVMe, measured) | USB hard drive cold read ceiling | limit |
|---|--:|--:|---|
| photos, reduced-size decode | about 90 photos/s (≈270 MB/s) | 23 photos/s (69 MB/s) | **disk, about 4× below compute** |
| photos, exact decode | about 25 photos/s (≈75 MB/s) | 23 photos/s | about equal |
| videos (this dataset's mix, 2 CPU + 2 NVDEC) | 1.08 clips/s (≈54 MB/s) | 90 MB/s | compute, but high-bitrate videos would be disk-bound |

## 4. A design flaw this exposed
- The experiment pipeline reads each file twice (full hash, then decode). Cold runs show physical reads of 1.14–1.47× the file bytes (2.0–2.4× for the serial reference),
  so the second read often came from disk, not the cache, when the data set (5.7 GiB) exceeds the file cache (about 4.5 GiB standby).
  On a 69 MB/s drive that would double read time.
- **Design rules for the unified pipeline (Step 5):**
  1. **Read each file once.** The content ID comes from the bytes already in memory (partial hash: size + first and last 64 KiB).
  2. **Reader count per device:** one reader on a hard drive, several on an SSD, as a setting (`--storage hdd|ssd`).
  3. **Order reads by path** (consecutive files in a folder tend to sit close together on disk).
  4. **Consider reading photos and videos in separate phases** on a hard drive so two kinds of traffic don't interleave. To be tested in Step 6.

## Correction to earlier results
`EXP_PIPELINE.md` called its 1,984-photo runs "warm". The set (5.7 GiB) is larger than the file cache, so those runs were partly cold. The speeds there remain valid because NVMe cold ≈ warm (section 2).

## Caveats
- USB figures are from subsets (416 of 2,379 photos, 25 of 197 videos) of one folder and one drive. Different drives or USB ports may differ. Cold-read variation between repeats was small (≤ 7%).
- NVMe cold/warm used 3 repeats; the earlier 1,984-photo warm figure is a single run.
- The pipeline itself has not yet been run on the USB drive; section 3 is a projection.
- Eviction is per file, not a full system-cache flush. Verified through disk counters, but background activity (Defender, indexing) was not controlled.
