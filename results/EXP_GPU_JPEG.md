# Step 7: does GPU JPEG decoding (nvJPEG) improve photo throughput?

Run: `results/exp_gpu_jpeg.jsonl`, console in `results/S7_gpu_jpeg_console.txt` · 21 Nov 2026 · High Performance plan, AC · bytes held in memory (no disk effects)
Data: the 124 real photos × 8 = 992 timed decodes per run, 3 repeats. Code: `bench/exp_gpu_jpeg.py`.
Per photo: decode → 512 px thumbnail and 224 px model input → CLIP ViT-B/32 embedding (thumbnail writing excluded for every variant).

## Variants
- `cpu_draft16`: the current pipeline: reduced-size JPEG decode in 16 CPU threads, CLIP on the GPU.
- `gpu_nvjpeg_B{8,16,32}`: batched nvJPEG decode at **full size** on the GPU (`torchvision.io.decode_jpeg(device="cuda")`), EXIF rotation, antialiased GPU resize/crop/normalise, CLIP without leaving the GPU.
- `hybrid_B16+W{8,12}`: the GPU feeder and 8 or 12 CPU threads pull from one shared photo queue.

## Throughput (photos/s, median of 3, range)

| variant | photos/s | range | vs CPU | share of photos decoded on GPU |
|---|--:|--:|--:|--:|
| **cpu_draft16 (current)** | **115.0** | 113.1–117.1 | 1.00× | – |
| gpu_nvjpeg, batch 8 | 18.7 | 18.6–18.8 | 0.16× | 100% |
| gpu_nvjpeg, batch 16 | 18.9 | 18.8–18.9 | 0.16× | 100% |
| gpu_nvjpeg, batch 32 | 18.9 | 18.9–18.9 | 0.16× | 100% |
| hybrid, 16-photo GPU batches + 8 CPU threads | 102.1 | 101.8–103.8 | 0.89× | 11% |
| hybrid, 16-photo GPU batches + 12 CPU threads | 113.4 | 112.5–115.8 | 0.99× | 10% |

Peak GPU memory allocated: 2,671 MiB (inside the 3.5 GB budget; includes the model and 32 full-size decoded photos).

## Quality (124 unique photos)
| check | result | bar |
|---|---|---|
| model-input PSNR vs exact CPU reference | min 49.1 dB, median 51.5 dB | ≥ 40 dB: pass |
| CLIP cosine vs reference | min 0.982, median 0.9987 | (reported only) |
| same top-1 neighbour | 95.2% (CPU draft: 98.4%; control: 98.4%) | within 2 points of control: **fail** |
| top-5 overlap | 95.5% (CPU draft: 97.1%; control: 97.6%) | within 2 points of control: **fail (by 0.1 point)** |

## Verdict: rejected
1. **GPU decode is about 6× slower than the current CPU path, and batch size does not change it** (18.7 → 18.9 photos/s from batch 8 to 32).
   The flat curve points to a fixed per-photo cost, not GPU compute. Likely cause (not isolated): this laptop GPU has no hardware JPEG decoder, so nvJPEG decodes the entropy-coded part on the CPU,
   about 53 ms per 12-megapixel photo (1 / 18.9 s) in a single thread, which matches the cost of one full-size CPU decode.
2. **The CPU path's advantage is the algorithm, not the processor:** libjpeg-turbo can decode directly at 1/4 size (skipping 15/16 of the pixels), which nvJPEG does not offer.
3. **Mixing both gives no gain:** the hybrid reached 99% of CPU-only at best and 89% with fewer CPU threads, and the GPU took only 10–11% of the photos.
4. **Quality is also slightly worse** under our search-level gate (marginal failure on top-5, clear on top-1).
5. Photos are therefore left on CPU reduced-size decode. The GPU stays free for embedding (about 1 ms per photo).

## Caveats
- Hardware-specific: GPUs with a hardware JPEG engine (data-centre A100/H100) may behave very differently.
- Photos held in memory, 12 MP phone JPEGs (the dataset's typical size). Smaller photos would favour the GPU less, since fixed per-photo costs dominate.
- The CPU-side cause is inferred from the flat batch curve, not profiled.
- `torch.frombuffer` copies and per-photo EXIF parsing sit in the GPU path's per-photo cost and were not separated out.
- The unrelated 10–15 s of warm-up and model load are excluded from all timings.
