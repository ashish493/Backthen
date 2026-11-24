# Baseline, warm cache, single thread, High Performance power plan (n=1 run)

Run: `results/baseline_warm_hp_r1.jsonl` · 20 Nov 2026 · same pipeline and dataset as `BASELINE_WARM.md`, which was run on the Balanced plan.
**Supersedes `BASELINE_WARM.md` for absolute numbers.**

| Stage | kind | n | total s | % active | p50 ms | p95 ms | p99 ms |
|---|---|--:|--:|--:|--:|--:|--:|
| S0_discover | - | 1 | 0.00 | 0.0 | 0.6 | 0.6 | 0.6 |
| S1_hash | - | 170 | 1.60 | 1.5 | 8.4 | 14.7 | 55.1 |
| S2_metadata | - | 170 | 1.91 | 1.8 | 0.4 | 78.1 | 107.7 |
| S3_decode | image | 124 | 10.58 | 9.7 | 71.1 | 152.3 | 395.1 |
| S3_decode | video | 46 | 72.71 | 67.0 | 635.1 | 7109.9 | 10583.9 |
| S4_transform | - | 170 | 16.17 | 14.9 | 74.0 | 225.4 | 584.1 |
| S5_transfer | - | 28 | 0.25 | 0.2 | 7.7 | 16.4 | 17.7 |
| S6_inference | - | 28 | 5.04 | 4.6 | 176.6 | 281.1 | 316.2 |
| S7_write | - | 170 | 0.24 | 0.2 | 1.1 | 1.5 | 1.5 |

End-to-end wall **108.6 s** (1.57 files/s), peak RSS 3009 MiB, peak VRAM 1051 MiB. Video decode: 72.0x frames decoded per frame returned.

## Comparison with the Balanced-plan run
| | Balanced | High Performance |
|---|--:|--:|
| End-to-end wall | 290.1 s | 108.6 s (2.7x faster) |
| Video decode share | 74.7% | 67.0% |
| S4 transform share | 13.1% | 14.9% |
| Image decode share | 8.8% | 9.7% |
| S6 inference total | 2.53 s | 5.04 s |

Same code, data and warm cache. The power plan is the most likely cause of the 2.7x, but it is not isolated (one run per plan, no counter-balanced order).
Stage proportions are broadly stable; absolute times are not. **Always record the power plan and repeat runs.**
S6 got slower (p50 76 → 177 ms) while everything else got faster. Unexplained: not investigated (28 batches, untrained ResNet50 stand-in).

## Caveats
n=1 per plan; the planned ≥5 repeats are still outstanding. Warm only. Stand-in model.
