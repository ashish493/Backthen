# Hypothesis D: GPU→CPU transfers cancel the benefit of hardware decoding (plus E data)

Runs: `results/exp_d.jsonl` (PyAV, 46 clips × 3 repeats), `results/exp_d_ffmpeg.jsonl` (FFmpeg CLI, 8 clips × 3 repeats)
Date: 20 Nov 2026 · High Performance plan, AC · warm cache · RTX 3050 Laptop (4 GB), NVDEC via `h264_cuvid` / CUDA hwaccel.
Code: `bench/exp_d_nvdec.py`, `bench/exp_d_ffmpeg.py`.

## 1. In the real pipeline (PyAV, same sampling, S3+S4+S5), sum of per-clip medians over all 46 clips

| path | S3 decode | S4 transform | S5 upload | total | decode fps |
|---|--:|--:|--:|--:|--:|
| cpu (16 threads, `thread_type=AUTO`) | 63.8 s | 12.2 s | 0.09 s | 76.2 s | 359 |
| nvdec_host (frames returned in host memory) | 78.2 s | 12.4 s | 0.07 s | 90.8 s | 293 |

NVDEC S3 / CPU S3 ratio by resolution class: 1.64 (720p, 1 clip), 1.35 (1080p, 28 clips), 1.30 (1440p, 4 clips), 1.19 (4K, 13 clips).
NVDEC was faster than CPU on only 6 of 46 clips. Correctness: frame timestamps matched on every clip; PSNR of 224×224 model inputs vs CPU: min 46.1 dB, median 47.0 dB (threshold 40 dB).
VRAM during an NVDEC session: about 963 MiB (median, including display use).

## 2. Decomposition: pure decode, no Python, no colour conversion (FFmpeg CLI, whole clips)

| clip class | cpu fps | gpu_resident fps (no download) | gpu_download fps (every frame copied to host) |
|---|--:|--:|--:|
| 4K, 4 clips | 129 / 175 / 215 / 318 | 150 / 170 / 188 / 192 | 149 / 169 / 187 / 190 |
| 1080p, 4 clips | 515 / 834 / 907 / 993 | 533 / 579 / 613 / 631 | 523 / 578 / 608 / 629 |

- `gpu_resident` and `gpu_download` differ by about 1% on every clip. **The device→host copy costs almost nothing** (a 4K nv12 frame is 12 MB, and about 190 fps is about 2.3 GB/s, a small fraction of PCIe bandwidth).
- NVDEC itself tops out near **190 fps at 4K and 600 fps at 1080p** on this GPU, about the same as or below the 16-thread CPU decoder (CPU faster on 5 of 8 clips, NVDEC faster on 3 of 8; the NVDEC wins were 4K clips at 24 fps and the 302-frame 1080p clip).
- FFmpeg CLI wall time includes process start-up, which is larger for GPU modes (about 0.2 s extra on short clips). This penalises short clips in the table above.

## 3. Hypothesis E data (decoder init = time from starting to open the file until the first decoded frame; includes decoding that first frame)

| clip duration | clips | CPU init share of S3 | NVDEC init share of S3 | mean init per clip (CPU / NVDEC) |
|---|--:|--:|--:|--:|
| < 10 s | 20 | 14.2% | 13.1% | 69 ms / 84 ms |
| 10–60 s | 25 | 6.3% | 5.7% | 106 ms / 113 ms |
| > 60 s | 1 | 1.2% | 0.9% | 147 ms / 149 ms |

One-time costs (outside the per-clip numbers): CUDA context 0.08 s, first NVDEC session 0.11 s.

## Verdicts (against the pre-registered rules in BENCHMARK_PLAN §6)

- **D: rejected on this hardware.** The rule was "path (2) is not faster than path (1) while a GPU-resident path (3) is".
  Path (2) is indeed not faster than CPU, but the reason is not the transfer: with the copy removed (`gpu_resident`) decode is no faster either.
  There is no hardware-decode gain on this machine for the copy to cancel. The GPU-resident TorchCodec/DALI path could not be run
  (TorchCodec needs FFmpeg shared DLLs and has no CUDA wheel for Windows/Python 3.13 on the PyTorch index; DALI is not available on Windows), so a GPU-resident *transform* (S4 on the GPU) remains untested.
- **E: rejected under the 30% rule.** Init is 14% of decode time for clips under 10 s and falls with duration. It is not negligible (about 11–12% of S3+S4+S5 for short clips),
  and NVDEC init is not larger than CPU init in-process, though out-of-process GPU start-up costs about 0.2 s.

## What this means for the optimisation choice
NVDEC does not speed up a single decode stream here, but it is a second, independent decoder. The CPU decodes about 360 fps (mixed) and NVDEC about 290 fps,
so decoding different clips on both at once could raise total throughput and free CPU cores for S4. That is untested and is the next experiment.

## Caveats
- Warm cache; 3 repeats; the PyAV and FFmpeg runs used different clip subsets. Single machine, single GPU (one NVDEC engine on a laptop 3050).
- PyAV's host-memory path adds Python per-frame overhead that the FFmpeg CLI does not have, so the PyAV NVDEC numbers are pessimistic relative to the CLI numbers (CLI: parity with CPU).
- H.264 only (1 HEVC clip in the dataset, not covered by the FFmpeg run). Decoder init includes the first-frame decode, so it overstates pure init.
