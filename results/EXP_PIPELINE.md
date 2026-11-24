# Step 3: end-to-end photo pipeline with real CLIP, and overlapping GPU with CPU work

Run: `results/exp_pipeline.jsonl` · 21 Nov 2026 · High Performance plan, AC · warm cache
Data: `synthetic/photos_x16`: the 124 real photos copied 16x (1,984 files, 5.66 GiB, unique content hashes, identical pixels).
Per photo: hash + metadata + decode + thumbnail/model input + thumbnail write (CPU), then CLIP ViT-B/32 fp16 image embedding (GPU).
Serial reference ran once; the other configs ran 3 times in shuffled order. Code: `bench/exp_pipeline.py`.

## 1. Real CLIP inference speed (RTX 3050 Laptop, fp16)

| batch | upload ms/photo | inference ms/photo | photos/s | peak allocated VRAM |
|--:|--:|--:|--:|--:|
| 16 | 0.08 | 1.20 | 783 | 324 MiB |
| 32 | 0.08 | 1.01 | 921 | 346 MiB |
| 64 | 0.07 | 0.91 | 1,022 | 388 MiB |
| 128 | 0.07 | 0.89 | 1,048 | 473 MiB |

The untrained ResNet50 stand-in in the earlier baselines suggested about 12 ms/photo. **Real CLIP is about 1 ms/photo**, so the stand-in's inference numbers should not be used as an estimate.

## 2. End-to-end photo pipeline (1,984 photos)

| config (kind : decode : CPU workers : batch) | wall (median) | range | photos/s | speedup | GPU busy | GPU waiting for input | CPU % | GPU % | peak RSS | peak VRAM |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| serial reference (full decode, 1 thread) | 321.1 s | n=1 | 6.2 | 1.0x | 10.0 s | – | 15 | – | 1,959 MiB | 1,218 MiB |
| **staged : draft_m2 : 16 : 64** | **20.3 s** | 19.1–21.4 | **97.6** | **15.8x** | 2.5 s | – | 83 | 21 | 3,199 MiB | 1,255 MiB |
| **pipelined : draft_m2 : 16 : 64** | 21.3 s | 21.1–21.8 | 93.0 | 15.1x | 3.9 s | 17.4 s | 69 | 13 | **2,098 MiB** | 1,255 MiB |
| pipelined : draft_m2 : 12 : 64 | 26.5 s | 26.2–27.1 | 75.0 | 12.1x | 3.7 s | 22.8 s | 51 | 9 | 1,910 MiB | 1,255 MiB |
| pipelined : draft_m2 : 8 : 64 | 28.8 s | 28.0–29.8 | 68.9 | 11.2x | 3.9 s | 24.9 s | 44 | 9 | 1,931 MiB | 1,255 MiB |
| pipelined : full : 16 : 64 (exact output) | 74.4 s | 71.9–74.5 | 26.7 | 4.3x | 9.0 s | 65.4 s | 76 | 12 | 3,016 MiB | 1,255 MiB |

(VRAM figures include about 0.9 GB used by the display.)

Serial reference CPU time by stage: decode 159.7 s, transform 137.1 s, hash 5.4 s, write 2.4 s, metadata 0.6 s.
Best config, share of CPU-worker time: **decode 75%**, transform 18%, hash 4%, write 2%, metadata 1%.

## Correctness
- Embeddings are reproducible: every repeat of the same decode mode and batch size matches its first run (min cosine 1.000000).
- The exact path (`full`, 16 workers) matches the serial reference (min cosine 1.0000).
- `draft_m2` vs serial reference: min cosine 0.9853, the same as in `EXP_IMAGES.md`, where it passed the search-level gate.
  The similar-photo gate cannot be re-checked on this set because every photo has 15 pixel-identical copies.

## Findings
1. **End-to-end photo throughput: 15.8x (6.2 → 97.6 photos/s)** with reduced-size decode and 16 workers, real CLIP included.
   The exact-output path alone gives 4.3x.
2. **Hypothesis C confirmed: CPU work starves the GPU.** In the best overlapped config the GPU waits for input 17.4 of 21.3 s (82%), the queue never fills
   (feeder blocked 0.0 s), GPU use averages 13%, and throughput rises with CPU workers (68.9 → 75.0 → 93.0 photos/s for 8 → 12 → 16).
   This meets the rule in BENCHMARK_PLAN §6 (GPU < 50% busy, waiting > 30%, more CPU workers → more throughput).
3. **Overlapping GPU and CPU work did not make it faster** (21.3 s vs 20.3 s staged; ranges do not overlap). GPU work is only about 2.5–4 s of the run,
   so there is little to hide, and the bounded in-flight window (2 × workers) costs some CPU use (69% vs 83%).
4. **But overlapping is what keeps memory bounded:** 2.1 GB vs 3.2 GB peak RSS. The staged version holds every model input in RAM
   (0.6 MB per photo), which would need about 30 GB for 50,000 photos and break the 16 GB budget. **Use the overlapped design** and accept about 5% throughput.
5. **The remaining bottleneck is still CPU decode** (75% of worker time), even at reduced size. CPU is 69–83% busy, while the GPU sits about 80% idle.

## What this means
- At 93 photos/s, a 30,000-photo archive would take about 5.4 min of photo processing (warm cache), vs about 81 min serially. Projection, not measured at that size.
- The next lever for photos is moving JPEG decode onto the idle GPU (`torchvision.io.decode_jpeg(device="cuda")`, nvJPEG; on this consumer GPU Huffman decoding still runs on the CPU),
  or measuring cold-cache disk reads, which at about 270 MB/s may become the limit for a first import.

## Caveats
- Warm cache (files read once before timing). Cold first-import speed is still unmeasured.
- Replicated data: 16 copies of 124 photos. Decode cost per photo is realistic, but the photo variety is not.
- The serial reference ran once (about 5 min); the other configs ran 3 times.
- Videos are not included here; they use the hybrid CPU+NVDEC decoder from `EXP_HYBRID.md`, which is not yet wired into the same pipeline.
