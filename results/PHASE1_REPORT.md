# Phase 1 report: less work, better scheduling (21–22 Nov 2026)

Round goal: make ingestion faster without faster decoders, by decoding and reading less, and by removing system-level waste. Measure each lever alone, then together.
Details: `EXP_SAMPLING.md` (sampling policies), `EXP_POWER.md` (power, threads, codec routing).

## What changed and what it gave

| lever | result | verdict |
|---|---|---|
| **Smart video sampling** (exact frames, jumps over keyframe intervals without targets) | −66% frames decoded on the set B folder, output **bit-identical**; 247.6 s vs 287.4 s with prefetch (−14%) | **default** |
| Keyframe-only sampling | 1.5–3.2× faster but fails the quality gate (only 27–67% of videos keep median coverage ≥ 0.95) | opt-in lossy fast mode |
| Adaptive sampling (keyframe if within τ) | passes only at τ = 0.5 s, then no faster than smart + prefetch | kept, not default |
| **Process power throttling off** (EcoQoS) | Balanced plan: 148.9 → 46.2 s; High Performance: intermittent 105–113 s → 48–50 s | **default** |
| High priority | no gain (46.0 vs 46.2 s) | not used |
| FFmpeg threads per decoder | auto is fastest (45.5 s); caps 1–8 are slower (50–128 s) | keep auto |
| **Codec-aware routing** (HEVC → NVDEC) | same wall time on the USB drive (241.6 vs 244.3 s) but −32% video decode time, CPU 46% → 32% | **default** |
| NVDEC for seeks | same frames as CPU seeks (T6), enables hardware decode for smart sampling | **default** |
| Prefetch small videos on hard drives | already measured (13%), combines with smart | **default on `--storage hdd`** |
| Per-second timelines, process read bytes, seek counts | in every report | infrastructure |

## Headline numbers (cold cache)

| workload | original baseline | before this round | after this round |
|---|--:|--:|--:|
| set A, SSD (124 photos + 46 videos) | 77.4 s | 47.9 s | 45.5–49.3 s (smart; the SSD set gains little: short clips) |
| Set B folder, USB hard drive (2,494 photos + 197 videos, 17.3 GiB) | 540.2 s | 287.4 s | **241.6 s (2.24× vs baseline, 1.19× vs before)** |

On the USB drive the pipeline now reads at 71–73 MB/s, at the drive's measured raw ceiling: **further gains on that archive need reading fewer bytes, not more compute.**

## Correctness (`bench/verify_ingest.py`, set A): 14/14 checks pass
T1 determinism (2 configs), T2 kill + resume, T3 unchanged rerun, T4 seek == sequential, T5 smart == sequential, T6 NVDEC seek = CPU seek frames (cosine ≥ 0.985), T7 kill + resume under smart, keyframe and adaptive.

## v0.1 defaults (set in `bench/ingest.py` after the tests passed)
`--video-strategy smart --nvdec-seek --route codec --throttle off --priority normal`, video prefetch 256 MB and read budget 768 MB when `--storage hdd`.
Experiments up to commit `330c1df` ran with the earlier defaults; their scripts pass explicit flags where it mattered, and git keeps the exact code.

## Lessons for the benchmark method
1. **Uncontrolled OS power throttling can triple run times** and appears intermittently. Every future benchmark sets `--throttle off` (now the default) and interleaves the configurations it compares.
2. **Bytes read and wall time diverge on hard drives:** smart without prefetch read the fewest bytes but was slower than smart with prefetch.
3. **Quality gates fixed before the runs worked as intended:** they rejected keyframe and large-τ adaptive despite the large speed gains.

## What this round did not settle
- Whether codec routing speeds up a compute-bound, HEVC-heavy library (the SSD set has 1 HEVC clip).
- The trigger for intermittent throttling.
- Keyframe sampling's effect on actual search results (needs RoamEngine's labelled evaluation).

**Ingestion experiments are now paused** (per plan). Next: Phase 2, the architecture assessment.
