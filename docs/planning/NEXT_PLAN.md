# Next plan: faster ingestion through less work and better scheduling, not faster decoders

> **Status: completed 22 Nov 2026. Results: `results/PHASE1_REPORT.md`.** Measurement refinements from `next-plan-response` were applied
> (macro-averaged per-video gate, displacement, retention, bidirectional coverage, the separate power configurations A–E, and the 1/2/4/8 thread sweep).
> The quality gate actually used is the stricter macro version written into `bench/compare_sampling.py` before the runs.

Date: 21 Nov 2026 · follows `results/OVERNIGHT_REPORT.md`.

## Why this direction
The decoders (libjpeg-turbo, FFmpeg, NVDEC) run at their hardware speed. What the system controls is **how much work it asks for, and when**:
- **Video:** the default sequential sampling decodes about **64 frames for every frame kept** (set B archive), and on a hard drive it **reads the whole clip** (10.2 GiB of videos) to keep 32 frames each.
- **Photos:** each 12 MP photo is read in full (about 3 MB) to produce a 224 px model input. Embedded EXIF previews are big enough for the model in only **29% of JPEGs** (and the HEIC files have none), so photo previews are a minor lever here.
- **Waste in timing:** CPU 50–60% and GPU about 7% busy on the hard drive. Some of the remaining time is spent waiting, not working (timelines will show where).
- **The power plan alone changed speed 2.7×.** A normal user will not switch Windows to High Performance.

## Levers, ranked by expected effect

| # | lever | what changes | expected effect | output change? |
|---|---|---|---|---|
| 1 | **Keyframe sampling for video** (`--video-sampling keyframe`) | for each target time, seek to the nearest preceding keyframe and decode just that one frame | far fewer frames decoded (≈1 per target instead of ≈64) and, by reading via seeks, **far fewer bytes read** from the drive | yes: the frame used is the keyframe at or before the target. Gate: frame coverage vs exact sampling (below) |
| 2 | **Codec-aware routing + NVDEC for seeks** (`--route codec`) | HEVC always to the hardware decoder; H.264 split by hash as now; hardware decoder also used for seek and keyframe sampling | HEVC files 2.3× faster each (observed); long 4K HEVC films no longer CPU-only | pixels: NVDEC vs CPU differ slightly (PSNR ≥ 46 dB measured); deterministic |
| 3 | **Utilisation timelines** in every report | per-second CPU, disk, GPU, NVDEC, queue depths, byte budget | shows which resource idles when; guides 4–6 | none |
| 4 | **Thread budgeting** (`--video-threads`) | cap FFmpeg threads per video decoder | less oversubscription (12 photo threads + 2 video decoders × 16 threads) | none |
| 5 | **Power throttling off for the process** | ask Windows not to throttle this process (EcoQoS off), high priority, on the Balanced plan | could recover much of the 2.7× gap without the user changing plans | none |
| 6 | Photo previews where big enough | use the EXIF preview for the model input when ≥ 224 px | reads and decodes less for ~29% of JPEGs | yes, gated like the reduced-size decode |
| 7 | Duplicate skip (content ID seen before) | skip decoding exact duplicates | small here (a few duplicates) | none |
| 8 | Read order by on-disk position (hard drives) | sort reads by physical location | fewer head movements | none |

**Implemented and measured in this round:** 1–5. 6–8 stay on the list with the numbers above.

## Quality gate for keyframe sampling
For each video, compare its keyframe-sampled embeddings with the exact-sampled embeddings:
- **Coverage:** for every exact frame, the best cosine similarity to any keyframe frame. Report median and 5th percentile per video and overall.
- **Time offset:** how far, in seconds, the chosen keyframe is from the target time.
- **Frames per video:** keyframe sampling can return fewer distinct frames when targets fall inside the same keyframe interval.
Pass if median coverage ≥ 0.95 and 5th percentile ≥ 0.90 on both the set A and set B videos. These thresholds are set now, before the runs.

## Benchmarks (aggregate output only; E: read-only; derived files deleted)
- set A (SSD) and set B archive (USB, cold): current best vs keyframe sampling vs keyframe + codec routing (+ thread budget).
- Film folder: keyframe + NVDEC seeks vs the 220 s from last night.
- Balanced plan with throttling off vs Balanced plain vs High Performance (set A).
- Correctness tests again: determinism, kill/resume, rerun, and NVDEC-seek vs CPU-seek frame equality.

## Still on the list after this round
Hypothesis F (per-stage adaptive pools), a larger photo quality set, 50,000+ files, RAW/WebP/8K/10-bit, a second drive and laptop, time-to-first-search (index cheap metadata first, refine later).
