# Phase 1: power management, decoder threads, codec-aware routing

Date: 21–22 Nov 2026 · AC power · all runs cold · aggregate numbers only.
Code: `bench/run_phase1b.py`, flags in `bench/ingest.py` (`--throttle`, `--priority`, `--video-threads`, `--route`). Raw: `results/ingest_Ppower_*`, `ingest_Pthreads_*`, `ingest_Pcodec_*`.
Base configuration everywhere: smart sampling + NVDEC for seeks, 12 photo workers, 2 CPU + 2 NVDEC video workers.

## 1. Power: Windows plan vs process power throttling vs priority (set A, 170 files)

| config | Windows plan | process throttling (EcoQoS) | priority | n | wall s (median) | range | CPU % |
|---|---|---|---|--:|--:|--:|--:|
| A | Balanced | default | normal | 3 | 148.9 | 144.0–153.4 | 34 |
| **B** | **Balanced** | **off** | normal | 3 | **46.2** | 43.6–47.0 | 81 |
| C | Balanced | off | high | 3 | 46.0 | 45.4–50.9 | 80 |
| D | High Performance | default | normal | 6 | 109.0 | 44.7–112.7 | 33–81 |
| **E** | High Performance | **off** | normal | 3 | **49.3** | 48.3–49.7 | 82–85 |

Order: A–D interleaved per repeat (3 rounds), then D and E interleaved (3 rounds). Windows was restored to High Performance afterwards.

### Findings
1. **Process power throttling, not the power plan, is what slows ingestion.** With throttling off, the Balanced plan runs as fast as High Performance (46.2 vs 49.3 s).
2. **Throttling is intermittent even on High Performance.** 5 of 6 default runs on High Performance were throttled (105–113 s at about 34% CPU) and one was not (44.7 s at 81%).
   The trigger was not identified: the overnight runs on High Performance were never throttled. It may depend on whether the launching program is in the foreground.
3. **High priority adds nothing** (46.0 vs 46.2 s), so it is not worth making the machine less responsive.
4. **Consequence for earlier results:** the earlier "Balanced is 2.7× slower" result, the unexplained 76 s vs 97 s gap in `EXP_HYBRID.md`, and the wide ranges in some USB runs are consistent with this intermittent throttling.
   Experiments compared within the same session and interleaved stay valid, but absolute numbers from single, non-interleaved runs carry this risk.

### Decision for v0.1
**Default: opt out of power throttling (`--throttle off`), normal priority.** The user does not have to change the Windows power plan. A flag keeps the Windows default for users who want ingestion to stay in the background.

## 2. FFmpeg threads per CPU video decoder (set A, 3 repeats each)

| threads per decoder | wall s (median) | range | CPU % | summed video decode s |
|---|--:|--:|--:|--:|
| **auto (FFmpeg default)** | **45.5** | 45.4–45.6 | 80 | 90.9 |
| 8 | 50.2 | 50.0–52.5 | 57 | 98.4 |
| 4 | 68.7 | 68.3–69.9 | 34 | 127.5 |
| 2 | 90.4 | 90.0–91.0 | 25 | 163.6 |
| 1 | 127.9 | 126.2–129.8 | 17 | 219.8 |

Two CPU decoder instances plus two NVDEC instances ran concurrently in every configuration.
**No oversubscription penalty: capping threads only slows decoding.** Keep auto. (These runs were not throttled; CPU and ranges are consistent across both sessions.)

## 3. Codec-aware routing (set B folder, USB hard drive, smart + prefetch)

| routing | throttling | n | wall s | range | CPU % | summed video decode s | videos on NVDEC |
|---|---|--:|--:|--:|--:|--:|--:|
| hash (40% of videos to NVDEC) | default | 2 | 318.7 | 275.2–362.1 | 31–45 | 261–758 | 65 |
| codec (all HEVC + 40% of H.264) | default | 2 | 273.6 | 252.5–294.8 | 25–28 | 167–440 | 105 |
| hash | **off** | 2 | 244.3 | 242.0–246.6 | 44–49 | 250–268 | 65 |
| **codec** | **off** | 2 | **241.6** | 241.5–241.8 | 32 | 174–178 | 105 |

- With throttling controlled, **codec routing does not change wall time** (241.6 vs 244.3 s; within noise) but **cuts summed video decode time by about a third and CPU from about 46% to 32%.**
- Wall time does not drop because this archive is now **storage-bound**: the drive delivers 71–73 MB/s, at its measured raw ceiling (69–90 MB/s, `EXP_COLD.md`).
- The runs with default throttling are unusable for this comparison (one run of each was throttled).
- Decision: **codec routing on by default.** It is free on storage-bound runs and should help compute-bound, HEVC-heavy libraries. That second effect is not demonstrated yet: the SSD set has only 1 HEVC clip.

## New best on the set B folder
**241.6 s** (smart sampling, prefetch, codec routing, throttling off), against **540.2 s** for the original baseline: **2.24×**. The run is now at the drive's read limit.
