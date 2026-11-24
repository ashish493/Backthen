# Baseline, warm cache, single thread (preliminary: n=1 run)

Run: `results/baseline_warm_r1.jsonl` · 20 Nov 2026 · 170 files (2.3 GB) · env in `results/env.json`
Pipeline: Pillow + PyAV CPU, 1 frame / 2 s capped at 32 per clip, 512 px thumbnails, 224 px model input,
untrained ResNet50 fp16 stand-in (compute cost only), batch 16. Power plan was **Balanced** (not High Performance).
A cache-filling pass ran first and was discarded.

| Stage | kind | n | total s | % active | p50 ms | p95 ms | p99 ms |
|---|---|--:|--:|--:|--:|--:|--:|
| S0_discover | - | 1 | 0.00 | 0.0 | 4.1 | 4.1 | 4.1 |
| S1_hash | - | 170 | 2.34 | 0.8 | 4.9 | 56.2 | 222.6 |
| S2_metadata | - | 170 | 4.01 | 1.4 | 0.9 | 140.5 | 204.8 |
| S3_decode | image | 124 | 25.51 | 8.8 | 180.3 | 371.0 | 703.4 |
| S3_decode | video | 46 | 216.53 | 74.7 | 1260.0 | 21093.7 | 30397.0 |
| S4_transform | - | 170 | 37.87 | 13.1 | 158.9 | 540.3 | 1733.4 |
| S5_transfer | - | 28 | 0.42 | 0.1 | 10.7 | 53.9 | 64.3 |
| S6_inference | - | 28 | 2.53 | 0.9 | 75.9 | 149.9 | 234.4 |
| S7_write | - | 170 | 0.72 | 0.2 | 2.9 | 6.4 | 17.4 |

End-to-end wall **290.1 s** (0.59 files/s); peak RSS 1793 MiB; peak VRAM 1935 MiB (includes the display's baseline use).
Video decode: 72.0x frames decoded per frame returned; decoder open time is 1.6% of S3.

## What this suggests (not yet confirmed)

- **Video decode (S3) is 75% of time.** Prime suspect. Median 1.3 s per clip, but p95 21 s, so a few long 4K clips dominate.
- **Sampling policy drives the 72x ratio.** Sequential decode walks the whole clip to pick ≤32 frames. Hypothesis B must compare
  against seek/keyframe/batched strategies at equal output frames before calling this "redundant".
- **S4 (13%) and image decode (9%)** are the next costs. `Image.draft()` for thumbnails and a cheaper resize are candidates.
- **GPU work is under 1.5% of time** (S5+S6). C (CPU starves GPU) is untestable until S3/S4 are faster. D needs the NVDEC runs.
- **E looks weak in aggregate** (open = 1.6% of S3) but is not yet bucketed by clip duration.
- **A is untested**: S0+S1 are 0.8% at 170 files. It needs the 10k/50k synthetic sets.

## Caveats
- One measured run, not the planned 5. Variance unknown. Wall time ≈ sum of stage time because this baseline is single-threaded.
- The model is untrained ResNet50, so S6 only shows the order of magnitude of GPU cost.
- Warm only. No cold-cache run (needs RAMMap + admin).
