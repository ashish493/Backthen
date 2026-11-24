# Phase 1: video sampling policies (decode and read less, keep search quality)

Date: 21 Nov 2026 · High Performance plan, AC · all runs cold · aggregate numbers only.
Code: `bench/ingest.py` (`video_emits`), `bench/run_sampling.py`, `bench/compare_sampling.py`. Raw: `results/sampling_A.json`, `sampling_B.json`, `sampling_B_repeats.json`, `ingest_SA_*`, `ingest_SB_*`.
Every policy had the same decoder access (2 CPU + 2 NVDEC video workers, hash routing, NVDEC allowed for seeks), so only the sampling policy changed.

## Policies
| policy | what it decodes | frames it returns |
|---|---|---|
| exact (previous default) | sequentially from the start up to the last target | the frame at/after each target time (1 per 2 s, max 32) |
| **smart** | like exact, but a decode-free second handle finds each target's keyframe; the decoder jumps over intervals that contain no target | **identical frames to exact** |
| keyframe | one seek per target, keeps the keyframe at/before it | keyframes; targets sharing one keyframe give one frame |
| adaptive(τ) | smart, except where a jump is needed and the keyframe is within τ s of the target, it keeps that keyframe | mostly exact, some keyframes |
| `_prefetch` | the same, but each video ≤ 256 MB is read whole by the single sequential reader and decoded from memory | unchanged |

## Quality gate (fixed before the runs)
Macro-averaged per video, against exact sampling: pass if ≥ 95% of videos have median forward coverage ≥ 0.95 **and** macro p5 ≥ 0.90.
Forward coverage = for each exact frame, the best cosine similarity to any frame of the tested policy (CLIP ViT-B/32).

## Results

### set A (46 videos, internal SSD, 3 repeats)
| policy | wall s (median, range) | frames decoded | GB read | share of videos ≥ 0.95 | macro p5 | median displacement | frames kept | gate |
|---|--:|--:|--:|--:|--:|--:|--:|---|
| exact | 49.5 (49.1–49.6) | 22,908 | 2.33 | – | – | – | – | reference |
| **smart** | **45.6** (45.5–46.2) | 16,622 | 2.40 | 100% | 1.000 | 0.008 s | 100% | **pass (identical)** |
| adaptive 0.5 s | 44.9 (44.9–45.5) | 16,301 | 2.38 | 100% | 0.987 | 0.010 s | 100% | pass |
| adaptive 1 s | 44.3 (44.0–47.0) | 14,627 | 2.29 | 89% | 0.965 | 0.017 s | 100% | fail |
| adaptive 2 s | 44.2 (44.1–45.7) | 14,398 | 2.28 | 89% | 0.951 | 0.018 s | 100% | fail |
| keyframe | **15.3** (15.1–15.4) | 318 | 0.77 | 67% | 0.924 | 0.416 s | 65% | **fail** |

### Set B folder (2,494 photos + 197 videos, USB hard drive)
| policy | n | wall s (median, range) | frames decoded | GB read by the process | share ≥ 0.95 | macro p5 | gate |
|---|--:|--:|--:|--:|--:|--:|---|
| exact | 1 | 349.9 | 120,481 | 18.98 | – | – | reference |
| exact + prefetch (previous best) | 4 | 287.4 (277.3–295.9) | 120,481 | 17.24 | 100% | 1.000 | identical |
| smart | 3 | 273.4 (271.1–274.4) | 40,913 | 14.51 | 100% | 1.000 | identical |
| **smart + prefetch** | 2 | **247.6** (247.2–248.1) | 40,913 | 16.92 | 100% | 1.000 | **identical** |
| adaptive 0.5 s | 3 | 257.9 (253.1–258.7) | 34,931 | 13.89 | 95.9% | 0.929 | pass |
| adaptive 0.5 s + prefetch | 2 | 248.9 (245.0–252.8) | 34,931 | 16.85 | (as above) | | pass |
| keyframe | 1 | 191.1 | 1,906 | 8.84 | 27% | 0.863 | **fail** |

## Findings
1. **Smart sampling is a free win: identical output, a third of the decoding on real archives.**
   On the set B folder it decodes 40,913 frames instead of 120,481 (−66%). With prefetch it is **14% faster than the previous best** (247.6 vs 287.4 s; the ranges do not overlap)
   and **2.2× faster than the original baseline** (540 s, `EXP_HETERO.md`). On the set A the gain is smaller (−8%) because its short clips have targets in almost every keyframe interval.
2. **Keyframe-only sampling is the fastest by far (3.2× on the SSD, 1.5× on the USB drive vs the previous best) but fails the gate clearly:** only 67% (SSD) and 27% (USB) of videos keep
   median coverage ≥ 0.95. The keyframe is a median 0.4–0.5 s from the target and up to several seconds away, and up to 35% of requested frames collapse onto shared keyframes.
   It stays available as an opt-in "fast preview" mode, documented as lossy.
3. **Adaptive sampling passes only with a small τ (0.5 s), and then adds nothing over smart + prefetch** (248.9 vs 247.6 s). Larger τ (1–2 s) fails the gate.
   So on this data the design space collapses to two useful points: **smart (exact)** and **keyframe (lossy fast mode)**.
4. **Reading less is not automatically faster on a hard drive.** Smart without prefetch reads the fewest bytes (14.5 GB) but is slower than smart + prefetch (16.9 GB),
   because its seeks compete with the photo reader for the drive head. Prefetch reads more bytes in long sequential runs and wins. Keyframe mode reads only 8.8 GB because it
   reads a few hundred KB per seek. Bytes and wall time must be reported separately.
5. Seeks and probes: smart used 1,830 seeks + keyframe probes over 197 videos (about 9 per video). The extra container handle per video is cheap compared with the decoding it saves.

## Decision for RoamIngest v0.1
- **Default video sampling: `--video-strategy smart` with `--nvdec-seek`, plus `--video-prefetch-mb 256` on hard drives.** Exact frames, deterministic (T5 in `verify_ingest.py`).
- **Opt-in: `--video-sampling keyframe`** for a fast first pass, recorded per frame (`emb.method = 'keyframe'`), not search-equivalent.
- Adaptive stays in the code for libraries with long static scenes (more films, fewer travel clips), where it may pass with a larger τ. Not the default.

## Caveats
- One personal archive and one in-project set; the USB results are 1–4 runs per policy. Keyframe on the USB drive ran once.
- Quality is measured as CLIP embedding coverage against exact sampling, not as search results (that needs RoamEngine's labelled evaluation).
- The 0.95 / 0.90 gate is a design choice made before the runs; a different bar would change the adaptive verdicts.
