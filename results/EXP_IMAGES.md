# Photo path: parallel workers × reduced-size JPEG decode

Run: `results/exp_images.jsonl` · 20 Nov 2026 · High Performance plan, AC · warm cache · 124 real photos (mostly 12 MP phone JPEGs)
4 modes × workers {1, 2, 4, 8, 10, 16} × 3 repeats, config order shuffled per repeat. Code: `bench/exp_images.py`, `bench/stages.py`.
Measured work per photo: S3 decode + S4 transform (512 px thumbnail + 224 px CLIP model input). Hashing, inference and writing are **not** included.

## Modes
| mode | what it does |
|---|---|
| `full` | reference: full-resolution decode, then resize |
| `full_rgap` | control: reference decode + Pillow's documented `reducing_gap=3.0` resize shortcut |
| `draft_m2` | reduced-size JPEG decode keeping ≥ 2x headroom (12 MP → 1/4, 1024×768), then resize from exact source regions |
| `draft` | maximum reduced-size decode (12 MP → 1/8, 512×384) |

## Speed: ms per photo, median of 3 (speedup vs `full`, 1 worker)

| mode | 1 | 2 | 4 | 8 | 10 | 16 workers |
|---|--:|--:|--:|--:|--:|--:|
| full | 158.1 (1.0x) | 89.1 (1.8x) | 57.5 (2.8x) | 49.7 (3.2x) | 39.7 (4.0x) | 37.7 (4.2x) |
| full_rgap | 119.0 (1.3x) | 69.5 (2.3x) | 45.5 (3.5x) | 36.7 (4.3x) | 34.8 (4.5x) | 35.3 (4.5x) |
| **draft_m2** | 52.0 (3.0x) | 27.7 (5.7x) | 19.3 (8.2x) | 13.1 (12.1x) | 12.3 (12.9x) | **9.8 (16.2x)** |
| draft | 38.5 (4.1x) | 20.9 (7.6x) | 15.3 (10.3x) | 9.8 (16.1x) | 8.8 (18.0x) | 7.3 (21.5x) |

Repeat spread is small (e.g. `full` at 1 worker: 155.5–162.2 ms; `draft_m2` at 16: 9.6–10.7 ms).

## Correctness

`full` with any worker count: thumbnails and model inputs **byte-identical** to the serial reference in every run.

Gate (fixed in BENCHMARK_PLAN §7 before this run): PSNR ≥ 40 dB for model inputs and thumbnails, and top-1 / top-5 similar-photo overlap
within 2 points of the `full_rgap` control.

| mode | model-input PSNR min | thumbnail PSNR min | same top-1 neighbour | top-5 overlap | CLIP cosine min / median | photos < 0.999 | gate |
|---|--:|--:|--:|--:|--:|--:|---|
| full_rgap (control) | 48.5 dB | 52.0 dB | 98.4% | 97.4% | 0.9942 / 0.99946 | 29 | reference for the gate |
| **draft_m2** | 46.1 dB | 40.5 dB | 98.4% | 96.6% | 0.9854 / 0.99944 | 43 | **PASS** |
| draft | 37.0 dB | 31.1 dB | 94.4% | 94.7% | 0.9764 / 0.99608 | 104 | FAIL |

CLIP run twice on identical input gives cosine 1.000000, so the shortfall below 0.999 is real sensitivity of the embedding to tiny pixel changes, not noise.

## Findings
1. **Exact output scales only to about 4x.** `full` flattens near 38 ms/photo from 10 workers on, with CPU at 63–78%. Each full decode makes a 36 MB RGB image,
   so memory traffic (and Python-held steps such as `convert`/`exif_transpose`/`np.asarray`) likely limit it. The cause is not isolated.
2. **Reduced-size decode removes most of that work and keeps scaling:** `draft_m2` reaches **9.8 ms/photo at 16 workers, 16.2x the serial reference**
   (about 100 photos/s, vs about 6 photos/s before), and passes the gate.
3. **Maximum reduction (`draft`) is faster still (21.5x) but fails the gate**: the thumbnail is then purely the JPEG decoder's own downscale, which looks
   measurably different (thumbnail PSNR 31 dB) and moves search results (94.4% same top-1 neighbour).
4. Earlier, an alignment bug (crop computed on the reduced image) produced a 24.5 dB outlier. Resampling exact regions of the original image fixed it (now ≥ 37 dB even for `draft`).

## What this means for the target archive (projection, not measured)
In the baseline a photo cost 179 ms: 157 ms decode + transform, plus about 9 ms hashing, 12 ms inference (untrained ResNet50 stand-in), 1 ms writing.
With `draft_m2` at 16 workers the decode + transform part drops to about 10 ms, so **hashing and GPU inference become the next limit**
(about 21 ms/photo if they stay serial). That is Step 3 of the plan: overlap inference with the CPU work and measure real CLIP throughput.

## Caveats
- Warm cache. At about 100 photos/s × 3 MB the photo path needs about 300 MB/s of disk reads, so a cold first import may become disk-bound. Not yet measured.
- The similar-photo checks use only 124 photos, several of them near-duplicate bursts, so a 1–2 point difference is a few photos. Confirm on a larger set.
- Thumbnail PSNR for `draft_m2` (40.5 dB) is just above the bar.
- Only JPEG benefits; PNG/WebP fall back to full decode (none in this dataset).
- Python 3.13 threads (GIL build). Process-based workers were not compared.
