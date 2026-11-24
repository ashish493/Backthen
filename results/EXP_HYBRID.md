# Hybrid CPU + NVDEC video decode (S3+S4+S5 over all 46 clips)

Run: `results/exp_hybrid.jsonl` · 20 Nov 2026 · High Performance plan, AC · warm cache · 10 configs × 3 repeats, order shuffled per repeat
Code: `bench/exp_hybrid.py`. Config "c,n" = c CPU decode workers + n NVDEC decode workers (threads) pulling clips from one shared
queue, largest clip first. Each worker does decode + transform + GPU upload per clip. "1,0" is the serial CPU baseline.

| config (cpu,nvdec) | wall s (median) | min–max | speedup vs 1,0 | clips/s | decode fps | CPU % | NVDEC engine % | clips on cpu / nvdec | peak RSS MiB | peak VRAM MiB |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| **2,2** | **49.0** | 48.4–54.9 | **1.98x** | 0.94 | 468 | 90 | 64 | 26 / 20 | 2265 | 1330 |
| 2,1 | 55.0 | 46.2–57.5 | 1.76x | 0.84 | 416 | 89 | 43 | 31 / 15 | 2583 | 1128 |
| 1,1 | 55.2 | 54.4–58.1 | 1.75x | 0.83 | 415 | 70 | 61 | 24 / 22 | 1480 | 1152 |
| 3,1 | 61.5 | 55.6–61.8 | 1.58x | 0.75 | 372 | 94 | 38 | 39 / 7 | 3075 | 1144 |
| 4,0 | 72.7 | 71.3–75.1 | 1.33x | 0.63 | 315 | 97 | 0 | 46 / 0 | 2946 | 938 |
| 3,0 | 72.9 | 68.2–76.6 | 1.33x | 0.63 | 314 | 95 | 0 | 46 / 0 | 2381 | 934 |
| 2,0 | 78.2 | 74.8–82.6 | 1.24x | 0.59 | 293 | 90 | 0 | 46 / 0 | 2214 | 953 |
| 0,2 | 81.8 | 81.2–82.4 | 1.19x | 0.56 | 280 | 25 | 91 | 0 / 46 | 1708 | 1363 |
| 1,0 (baseline) | 96.9 | 96.4–99.1 | 1.00x | 0.47 | 236 | 67 | 0 | 46 / 0 | 1115 | 937 |
| 0,1 | 96.9 | 92.1–98.1 | 1.00x | 0.47 | 236 | 23 | 76 | 0 / 46 | 891 | 1139 |

## Findings
1. **Mixing both decoders is the fastest approach: 2,2 gives 1.98x over the serial baseline and about 1.5x over the best CPU-only setup** (4,0 at 72.7 s).
   Even 1 CPU + 1 NVDEC worker (1.75x) beats 4 CPU workers (1.33x).
2. **NVDEC alone is slow but uses almost no CPU** (25% total CPU with 2 NVDEC workers). Its engine saturates near 280 fps (91% engine use at 0,2).
   That is why it helps most as an *additional* decoder rather than a replacement.
3. **CPU becomes the limit once decoders are mixed** (89–97% CPU in the top configs): the transform stage (S4) and host-side work still run on the CPU.
   The NVDEC engine sits at 38–64% in the best configs, so moving S4 to the GPU is the next lever.
4. **Fixed worker mix matters (relevant to Hypothesis F).** Adding a third CPU worker (3,1) made things *worse* than 2,1: 7 clips went to NVDEC instead of 15, because the
   busy CPU starved the NVDEC worker's transform and download steps. Oversubscription (each software decoder already spawns many threads) is a real cost.
5. **Resource budget respected:** peak RSS 3.1 GB, peak VRAM 1.36 GB (about 0.9 GB of that is the display baseline). Targets were ≤ 6 GB and ≤ 3.5 GB.

## Correctness
- All 46 clips completed in all 30 runs. No decode errors, no fallbacks. Frame timestamps matched the CPU reference on every clip.
- **PSNR ≥ 46.1 dB** of the model inputs on every NVDEC-decoded clip (threshold 40 dB). The output is equivalent within tolerance, **not bit-identical**.
- **Reproducibility problem:** with dynamic work-stealing, which decoder handles a clip changes between runs (e.g. 15 vs 20 clips on NVDEC), so pixels can differ by tiny amounts run to run.
  The project's "repeated runs give the same result" rule is only met within tolerance. Fix before shipping: make routing deterministic (assign by stable clip hash or record the chosen decoder in the manifest and reuse it).

## What the ranking does and does not show
- Clear: any hybrid config (1.58–1.98x) beats the best CPU-only config (1.33x), and the 3,1 config is worse than 2,1/1,1/2,2.
- **Not resolved:** the order among 2,2, 2,1 and 1,1 (ranges overlap, n=3; 2,1 ranged 46–57 s).

## Caveats
- Video subset only (S3+S4+S5 for 46 clips); images, hashing and inference are not in this measurement, so this is not yet an end-to-end gain.
- The serial baseline here (96.9 s) is slower than the sum of per-clip medians in `EXP_D.md` (76.2 s). Not investigated (candidates: sustained-load thermal throttling, the monitor thread, run ordering). Speedups are relative within this run only.
- Warm cache, H.264 only, one machine with one NVDEC engine. The reference-comparison used the dataset's 46 videos (the 7 duplicate files were not excluded, which slightly repeats work).
- The scheduler is naive (shared queue, no thread-count tuning, S4 still on CPU).
