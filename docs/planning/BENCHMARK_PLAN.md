# RoamIngest: Benchmark Plan

Draft v0.1 · 20 November 2026 · Companion to `problem-1` (problem statement)

The aim is to find where ingestion time goes before optimising anything. This document defines the stages we time, the metrics we record, the experiments for hypotheses A–F, and how results are reported. The goal is to fix one bottleneck based on evidence.

---

## 1. Questions this plan answers

- **Primary:** On a mixed photo and video folder, how is end-to-end time split across filesystem, metadata, decode, transform, transfer, inference and write?
- **Follow-up:** Can a resource-aware, incremental pipeline cut that time with **identical output** and within **16 GB RAM / 4 GB VRAM**?

---

## 2. Dataset profile (set A, a personal photo set)

Measured on 20 Nov 2026.

| Folder     | Files | Size   |
|------------|------:|-------:|
| folder 1   | 35    | 339 MB |
| folder 2   | 45    | 728 MB |
| folder 3   | 23    | 112 MB |
| folder 4   | 34    | 431 MB |
| folder 5   | 33    | 743 MB |
| **Total**  | **170** | **2.3 GB** |

| Type | Count | Size range | Avg | Notes |
|------|------:|-----------|----:|-------|
| JPG  | 124 | 0.85–9.1 MB | ~3 MB | 104 phone (`IMG_…`), 11 Unsplash, 8 Snapchat, 1 other |
| MP4  | 43  | 2.4–335 MB  | ~47 MB | Stock clips name res/fps, e.g. `13386575_3840_2160_30fps.mp4`; phone clips `VID_…` |
| MOV  | 3   | 5.6–39 MB   | ~18 MB | iPhone `.MOV`, probably HEVC |

**Spread that matters for benchmarking**
- Resolutions: 720p, 1080p, 1440p and 4K, both **landscape and portrait** (e.g. `2160_3840`). Portrait frames test rotation metadata handling.
- Frame rates: 24, 25, 30, 50 and 60 fps.
- Video sizes span two orders of magnitude (2.4 MB to 335 MB). Short phone clips vs long 4K stock clips give a natural test of **Hypothesis E** (decoder initialisation).
- **7 byte-identical duplicate pairs** (e.g. `16734725_2160_3840_60fps.mp4` and `…(1).mp4`). These are useful for testing incremental runs and dedup.
- 6 filenames contain spaces, and some carry edit suffixes (`~2.mp4`). These are robustness cases.

**Gaps compared with the scope in `problem-1`**
- No PNG or WebP. Generate a small set by converting a subset of the JPGs.
- 170 files is far below "tens of thousands". **Hypothesis A cannot be tested on this dataset alone.** A synthetic generator is needed (§9).
- Codecs are not yet confirmed. Run `ffprobe` over every video first (milestone M1) and record codec, profile, bit depth, GOP/keyframe interval, duration and rotation.

**Reference machine (this laptop)**
- RTX 3050 Laptop GPU, 4 GB VRAM, driver 610.91 · 16 GB RAM · Python 3.14.6
- Not installed yet: ffmpeg/ffprobe, Pillow, OpenCV, PyAV, torch. Python 3.14 wheels may not exist for Decord, DALI or TorchCodec, so keep a **Python 3.12 venv** as a fallback.

---

## 3. Pipeline stage model

Every file passes through these stages. Each stage is timed separately.

```
S0 Discover → S1 Change-detect → S2 Metadata → S3 Decode → S4 Transform → S5 Transfer → S6 Inference → S7 Write
```

| Stage | What it does | Candidate backends | Key metrics |
|-------|--------------|--------------------|-------------|
| **S0 Discover** | Walk tree, `stat` each file, classify by extension/magic | `os.scandir` (recursive), `os.walk`, `pathlib.rglob` | files/s, µs per file |
| **S1 Change-detect** | Skip unchanged files: (size, mtime) check, then content hash | size+mtime, xxhash3, BLAKE3, partial hash (head+tail+size) | MB/s, skip ratio on 2nd run |
| **S2 Metadata** | EXIF (images), container/stream info (video) | Pillow `getexif`, exifread, piexif, `ffprobe` subprocess, PyAV `container.streams` | ms per file, p95 |
| **S3 Decode** | Image to pixels; video to sampled frames | Images: Pillow, Pillow `draft()`, OpenCV, PyTurboJPEG, torchvision/nvJPEG. Video: PyAV (CPU), OpenCV, Decord, TorchCodec (CPU/CUDA), DALI, PyAV NVDEC (`h264_cuvid`/`hevc_cuvid`) | ms per image, frames/s, **decoded vs returned frames**, decoder open time |
| **S4 Transform** | Thumbnail, resize, colour convert, normalise, EXIF rotate | Pillow, OpenCV, torchvision (CPU/GPU) | ms per item |
| **S5 Transfer** | Host↔device copies | `tensor.cuda()`, pinned memory, `non_blocking=True` | MB/s, ms per batch, share of S3+S5 |
| **S6 Inference** | Embedding model (fixed for all runs, e.g. CLIP ViT-B/32 fp16) | PyTorch eager, `torch.compile` (optional) | items/s per batch size {1,8,16,32,64}, GPU util, VRAM |
| **S7 Write** | Thumbnails, sampled frames, embeddings, manifest | JPEG/WebP encode, `.npy`, SQLite (WAL), JSONL manifest | ms per file, MB written |

The model, frame-sampling policy (e.g. 1 frame per 2 s plus first/last) and thumbnail size (e.g. 512 px long edge) stay **fixed across all comparisons**. Only the variable under test changes.

---

## 4. Metrics and instrumentation

**Per stage, per file** (one JSONL row per `run_id, file, stage`):
- `active_ns`: time spent working (`time.perf_counter_ns`). On GPU stages, call `torch.cuda.synchronize()` at the span edges, or use CUDA events.
- `wait_ns`: time the item waited in the stage's input queue (shows starvation and backpressure).
- `bytes_in`, `bytes_out`, `frames_decoded`, `frames_returned`.
- `backend`, `config_hash`, `cache_regime`, `error`.

**Per run, sampled every 100 ms** (background thread):
- CPU % (total and per core), process RSS, system available RAM (`psutil`).
- GPU util, VRAM used, SM and memory clocks, temperature, power (`pynvml`).
- Disk read MB/s (`psutil.disk_io_counters`).

**Aggregates reported:**
- Throughput: files/s, MB/s, frames/s.
- Latency per stage: **p50, p95, p99**.
- **Stage time share** (% of end-to-end wall time) as the main chart.
- Peak RSS and peak VRAM compared with budget.
- Utilisation timeline (CPU vs GPU vs disk) for pipelined runs.

**Environment snapshot** with every run (`collect_env.py`): OS build, Python and library versions, GPU driver, CUDA version, Windows power plan, AC or battery, Defender real-time state, dataset manifest hash.

---

## 5. Cache regimes and repetition

| Regime | How | What it shows |
|--------|-----|---------------|
| **Cold** | Clear the Windows standby list (Sysinternals RAMMap → *Empty Standby List*, run as admin) before each run. Reboot if RAMMap is unavailable. Record the method. | Real first import, disk-bound behaviour |
| **Warm** | Run the same config again immediately | CPU/GPU-bound cost with I/O mostly removed |
| **Incremental** | Rerun on an unchanged tree, then after touching or adding 5% of files | Cost of change detection, skip correctness |
| **End-to-end** | Full pipeline, all stages concurrent | What a user actually waits for |

- Each config runs **≥ 5 times**. Report the **median and IQR**. Discard a warm-up run for GPU configs (CUDA context and cuDNN autotune).
- Run configs in randomised order to avoid thermal and order bias.

---

## 6. Experiments

Each hypothesis has a setup, a metric and a decision rule, and is then marked **confirmed / rejected / untested**.

### Baseline (run first)
Single-threaded reference pipeline (Pillow + PyAV CPU + PyTorch), cold and warm, over the whole dataset. Produces the first **stage time share** breakdown, split by images vs videos. All later experiments are measured relative to this.

### A: Many small files cause significant filesystem overhead
- **Setup:** Synthetic sets of 1k / 10k / 50k small JPEGs (50–300 KB), nested to depth 1–4, against the same total bytes stored as a few large files. Compare `scandir` vs `walk` vs `rglob`, and full hash vs partial hash vs size+mtime only.
- **Metric:** S0+S1 µs per file. Share of end-to-end time at 50k files.
- **Confirmed if** S0+S1 makes up **> 15%** of end-to-end wall time on the 50k set (cold), or per-file overhead grows non-linearly with file count.

### B: Random seeking in video causes redundant decoding
- **Setup:** Sample N ∈ {8, 32, 128} frames per video using (i) sequential decode with drop, (ii) a seek per target frame, (iii) keyframe-only, (iv) Decord/TorchCodec batched `get_frames_at`. Use the 4K stock clips plus the long phone clips.
- **Metric:** `frames_decoded / frames_returned` (redundancy ratio) and S3 time per returned frame. Use GOP size from ffprobe to explain results.
- **Confirmed if** the seek strategy decodes **≥ 2×** the frames it returns, **and** sequential or keyframe sampling is measurably faster for the same output frames.

### C: CPU preprocessing starves the GPU
- **Setup:** Fixed model and batch size. Vary preprocessing workers (0, 1, 2, 4, 6, 8) and the decode backend. Images only, then video only.
- **Metric:** GPU util timeline, S6 `wait_ns` share (time the inference queue is empty), items/s.
- **Confirmed if** GPU util stays **< 50%** while S6 is waiting on input **> 30%** of the time, and adding CPU workers raises items/s.

### D: GPU-to-CPU transfers cancel the benefit of hardware decoding
- **Setup:** Same video set and sampling. Compare (1) CPU decode → CPU transform → upload; (2) NVDEC decode → **download** → CPU transform → upload; (3) NVDEC decode → GPU transform → inference with frames kept on GPU (TorchCodec CUDA / DALI).
- **Metric:** S3 + S4 + S5 time per frame, PCIe bytes moved, VRAM peak.
- **Confirmed if** path (2) is **not faster** than path (1) while path (3) is, which shows the round trip erases the NVDEC gain.

### E: Decoder initialisation dominates on short clips
- **Setup:** For every video, time `open` → first decoded frame separately from the rest of the decode. Bucket by duration (< 10 s, 10–60 s, > 60 s) and codec. Test CPU vs NVDEC (NVDEC usually has a larger setup cost).
- **Metric:** `init_ms / total_decode_ms` per bucket.
- **Confirmed if** init accounts for **> 30%** of decode time in the short-clip bucket. If so, test decoder reuse or pooling.

### F: Fixed worker allocation leaves stages idle on mixed workloads
- **Setup:** Mixed stream (dataset order and shuffled). Compare (1) a single fixed pool of N workers doing all stages per file against (2) per-stage worker pools with bounded queues, in fixed sizes and adaptive sizes driven by queue depth.
- **Metric:** idle % per stage, end-to-end wall time, p95 per-file latency.
- **Confirmed if** in the fixed setup any stage is idle **> 40%** of the time while another has a non-empty backlog, and the staged setup cuts wall time by **≥ 10%**.

### Extra: Image decode backends
- Pillow full decode vs Pillow `draft()` (DCT-domain downscale, which is ideal for thumbnails) vs OpenCV `IMREAD_REDUCED_*` vs PyTurboJPEG (with scaling) vs nvJPEG (`torchvision.io.decode_jpeg(device='cuda')`, batched).
- Metric: ms per image at full res and at thumbnail res, and peak RSS.

---

## 7. Correctness: output must be identical

- **Reference backend:** Pillow + PyAV (CPU), single thread.
- **Metadata and manifest:** canonical JSON per file, hashed. Must match the reference **exactly**.
- **Decoded frames and thumbnails:** different decoders can differ by ±1 in pixel values, so compare with **PSNR ≥ 40 dB** (or SSIM ≥ 0.99) against the reference and log the worst file.
- **Frame selection:** the set of sampled frame timestamps (PTS) must match exactly. This catches seek-accuracy bugs in Hypothesis B.
- **Embeddings:** ~~cosine similarity ≥ 0.999 with the reference.~~ Replaced on 20 Nov 2026 (decided before the full photo run): CLIP ViT-B/32 fails 0.999
  even for Pillow's near-invisible `reducing_gap=3.0` resize (29/124 photos below, PSNR ≥ 48.5 dB), so the bar measured CLIP's sensitivity rather than correctness.
  **New gate for any non-identical photo path:** (a) PSNR ≥ 40 dB for model inputs and thumbnails, and (b) top-1 and top-5 similar-photo overlap with the
  reference each within 2 percentage points of the `reducing_gap` control. Cosine statistics are still reported.
- **Incremental runs:** the second run on an unchanged tree must process **0** files and produce a byte-identical manifest. Duplicates must be detected as duplicates.
- **Orientation:** portrait videos and EXIF-rotated photos must come out with the same orientation as the reference.

---

## 8. Memory budget guard

- Budget: **16 GB RAM, 4 GB VRAM** (reference machine). Target for the pipeline itself: ≤ 6 GB RSS and ≤ 3.5 GB VRAM, leaving headroom for the OS and display.
- Every inter-stage queue is **bounded**, sized in bytes rather than item count (a 4K RGB frame is about 25 MB).
- The sampler flags any run whose peak RSS or VRAM exceeds budget. Such a run **fails**, however fast it was.
- Watch for memory growth over a run (leaks in decoder handles).

---

## 9. Harness layout (to build next)

```
bench/
  collect_env.py      # environment snapshot → env.json
  probe_dataset.py    # ffprobe + EXIF inventory → dataset_inventory.csv
  gen_synthetic.py    # small-file sets (A), PNG/WebP conversions, clip trims (E)
  instrument.py       # span timer, queue-wait tracking, 100 ms resource sampler
  stages/             # s0_discover.py … s7_write.py with a common interface
  backends/           # pluggable decoders/transforms (pillow, opencv, pyav, decord, torchcodec, dali, nvjpeg)
  configs/*.yaml      # one file per experiment (backend, workers, regime, repeats)
  runner.py           # config → runs → results/<run_id>.jsonl
  verify.py           # correctness check against the reference backend
  report.py           # JSONL → tables, stage time share charts, timelines, hypothesis tracker
results/              # raw JSONL + env.json per run (never edited by hand)
```

The original archive is read-only to the harness. Derived outputs go to a separate `out/` directory, in line with the reference-based access rule.

---

## 10. Results template

**Stage time share (fill per regime)**

| Stage | Images: ms/file p50 / p95 | Videos: ms/file p50 / p95 | % of wall time (cold) | % of wall time (warm) |
|-------|------|------|------|------|
| S0 Discover | | | | |
| S1 Change-detect | | | | |
| S2 Metadata | | | | |
| S3 Decode | | | | |
| S4 Transform | | | | |
| S5 Transfer | | | | |
| S6 Inference | | | | |
| S7 Write | | | | |
| **End-to-end** | | | 100% | 100% |

**Hypothesis tracker**

| ID | Hypothesis | Status | Key number | Evidence (run id) |
|----|-----------|--------|-----------|-------------------|
| A | Small files → FS overhead | **rejected** (SSD, ≤12k files) | small files read 2–3.5× slower per byte than the same bytes in large files, but cost only ~0.15 ms/file (≈1.5% of the ~10 ms/photo pipeline budget); cost scales linearly. HDD with many small files untested | `results/EXP_A.md` |
| B | Seeking → redundant decode | **confirmed** (per-frame seek only) | seek decodes 69–75x frames returned; 1.7x–25x slower than sequential at N=8–128 | `results/EXP_B.md` |
| C | CPU preprocessing starves GPU | **confirmed** (photo pipeline, real CLIP) | GPU waits for input 82% of the run, 13% busy; 8 → 16 CPU workers raises 68.9 → 93.0 photos/s | `results/EXP_PIPELINE.md` |
| D | D2H transfer cancels NVDEC gain | **rejected** (on RTX 3050 Laptop) | copy costs ~1%; NVDEC itself is no faster than 16-thread CPU decode (~190 fps 4K, ~600 fps 1080p). GPU-resident S4 untested | `results/EXP_D.md` |
| E | Decoder init dominates short clips | **rejected** (30% rule) | init = 14% of S3 for <10 s clips, 6% for 10–60 s | `results/EXP_D.md` |
| F | Fixed workers leave stages idle | untested (partial evidence) | worker mix matters: 3 CPU + 1 NVDEC (61.5 s) was slower than 2+1 (55.0 s); best hybrid 2+2 = 1.98x vs serial. Stage-pool design not yet tested | `results/EXP_HYBRID.md` |

**Status, 22 Nov 2026: this benchmark round is closed.** Ingestion experiments (hypotheses A–F) are paused; findings are frozen into RoamIngest v0.1 (`packages/roam-ingest`, tagged `v0.1`) and validated end-to-end on the full 355 GB target archive — `results/EXP_ARCHIVE355.md`, Phase 2 architecture doc `docs/architecture/ROAMINDEX_ARCHITECTURE.md`. Further ingestion-speed work, if any, resumes only with new evidence; the next phase is RoamEngine (retrieval), planned separately.

---

## 11. Pitfalls to control

- **Laptop thermals and power:** stay plugged in, use the High Performance power plan, log GPU clocks and temperature, and pause between runs. Throttling shows up as rising latency within a run.
- **Windows Defender** real-time scanning inflates file open and read times. Run once with the dataset folder excluded and once without, and report both.
- **Search indexer, OneDrive and other background sync:** pause them during runs.
- **GPU warm-up:** the first CUDA call costs hundreds of ms. Exclude it from per-file stats but report it once as setup cost.
- **Python import time** for heavy libraries (torch, cv2): measure separately and keep it out of stage times.
- **Subprocess cost of `ffprobe`:** around 30–80 ms per call on Windows. Compare with in-process PyAV probing.
- **Filenames with spaces or unicode** and **duplicate files** must be handled correctly (they exist in this dataset).
- **Variable frame rate** phone clips: sample by PTS, not frame index.

---

## 12. Execution order

| # | Milestone | Output |
|---|-----------|--------|
| M0 | Environment: install ffmpeg, Pillow, OpenCV, PyAV, torch (CUDA), psutil, pynvml. Set up a 3.12 venv if wheels are missing | `env.json` |
| M1 | Dataset probe: codec, profile, GOP, duration, rotation, EXIF coverage | `dataset_inventory.csv` |
| M2 | Instrumentation and reference baseline (single thread, cold and warm) | first stage time share table |
| M3 | S0–S2 experiments, including Hypothesis A on synthetic sets | A marked |
| M4 | Image path: decode backends and thumbnailing | backend table |
| M5 | Video path: Hypotheses B, E, D | B, D, E marked |
| M6 | Inference and pipelining: Hypotheses C, F | C, F marked |
| M7 | Pick the **single largest proven bottleneck**, implement one fix, verify identical output, re-run end-to-end | before/after report |
| M8 | Publish the harness, generators and results. Only after this, assess whether a Rust core is justified | repo + report |

**Decision rule for M7:** fix the stage with the largest share of end-to-end wall time whose hypothesis is confirmed, provided the fix passes §7 correctness and §8 memory checks.
