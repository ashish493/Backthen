# Overnight report: multimodal ingestion benchmark (21 Nov 2026)

Everything here ran on this laptop (RTX 3050 4 GB, 16 GB RAM, internal NVMe SSD, external USB hard drive). Aggregate numbers only: no file names, paths or content from your personal archives appear anywhere in `results/`.

## 1. Headline

| workload | before (baseline) | after (optimised) | speedup |
|---|--:|--:|--:|
| Photos only: 1,984 photos (SSD, real CLIP embeddings) | 6.2 photos/s | **93.0 photos/s** (overlapped design; 97.6 when run in stages) | **15.1×** (15.8× staged; exact-output variant: 4.3×) |
| set A: 124 photos + 46 videos, 2.3 GB (SSD, cold = warm) | 77.4 s | **47.9 s** | **1.62×** |
| Your set B archive: 2,494 photos + 197 videos, 17.3 GiB (USB hard drive, cold) | 540 s | **286 s** | **1.89×** |
| Your film folder: 146 long videos, 132.5 GiB (USB hard drive, cold) | – | **220 s** (reads only 3.8% of the bytes) | – |

"Baseline" is the original structure: exact full-size photo decode, one reader, one worker per kind, CPU-only sequential video decode.
The mixed-archive gains are smaller than the photos-only gain because videos dominate the wall time and the hard drive caps reading speed.

**What gave the gains** (each measured separately in the experiment files):
1. Reduced-size JPEG decoding for photos (decode at 1/4 size when only a 512 px thumbnail and 224 px model input are needed): the big photo win.
2. Parallel photo workers, bounded in memory by a byte budget.
3. Hybrid video decoding: CPU and the GPU's hardware decoder (NVDEC) working on different clips at once (1.5× over CPU-only).
4. Reading every small file, videos included, with one sequential reader on a hard drive, then decoding from memory (13% faster on the USB drive).
5. Seek-based sampling for long videos (only where sampling is sparse), so a film is not decoded end to end.

**What did not help (tested and rejected):** GPU hardware decoding as a faster single decoder for H.264; GPU JPEG decoding (6× slower); overlapping GPU and CPU work for speed (it saves memory, not time); phased scheduling; more readers on a hard drive.

## 2. Where the time goes now

| situation | limit | evidence |
|---|---|---|
| SSD, video-heavy | CPU video decode (CPU 82% busy) | video decode about 75% of worker time |
| USB hard drive, real archive | the drive: 62 MB/s achieved vs 69–90 MB/s raw ceiling; about 24% above the pure read-time floor | CPU 62%, GPU 14% |
| any | **not the GPU**: CLIP embedding takes about 1 ms per photo; the GPU is busy about 7% of the time | all runs |

Costliest formats in your set B archive (per file, median):
- HEIC photo: **1,082 ms**, versus 106 ms for a JPEG (HEIC is 4.6% of photos but 15.8% of per-file time).
- HEVC video on the CPU: **4,694 ms**, versus 1,216 ms for H.264. NVDEC does HEVC in 2,019 ms but is no faster than the CPU for H.264.
- Large videos: the 37 4K videos (19% of videos) take 42% of video time; the 13 videos of 1–10 minutes (7%) take 34%.

## 3. Hypotheses (BENCHMARK_PLAN §5)

| | hypothesis | verdict | key number |
|---|---|---|---|
| A | many small files → filesystem overhead | **rejected** (SSD, ≤12k files) | ≈0.15 ms/file, about 1.5% of the per-photo budget; linear |
| B | random seeking in video → redundant decode | **confirmed** (for per-frame seeking) | seek decodes 69–75× the frames it returns; slower than one sequential pass at 32+ frames |
| C | CPU preprocessing starves the GPU | **confirmed** | GPU waits for input 82–94% of the time |
| D | GPU→CPU copies cancel hardware decode's benefit | **rejected** | copy costs ~1%; NVDEC itself is no faster than 16-thread CPU decode for H.264 |
| E | decoder start-up dominates short clips | **rejected** (30% rule) | 14% of decode time for clips under 10 s |
| F | fixed worker allocation leaves stages idle | **untested** (partial evidence) | worker mix matters (3 CPU + 1 NVDEC slower than 2 + 1); no stage-pool design tested |

## 4. Success criteria from the problem statement

| criterion | status |
|---|---|
| Identify ≥1 reproducible bottleneck and explain it | **done**: full-size photo decode; decoding whole clips for sparse sampling; hard-drive access pattern; HEIC/HEVC cost |
| Optimisation with a measurable end-to-end gain **and identical output** | **partly**: end-to-end gains measured (1.6–15.1×). Output is bit-identical only for the exact path (photos 4.3×). The faster paths change pixels slightly (model-input PSNR ≥ 46 dB for photos and video; search-level gate on similar-photo results passed for photos) and are reproducible run to run |
| Publish the harness, datasets (or generators) and results | **not yet**: harness, generators and results are in `d:\Roam`. Your real photos and videos cannot be shared, and nothing has been published |

## 5. The pipeline that now exists (`bench/ingest.py`)
- Photos (JPEG, HEIC, PNG, WebP) and videos (MP4, MOV, MKV, AVI, H.264, HEVC, VP9, MPEG-4) in one run; CLIP ViT-B/32 embeddings, photo thumbnails, a SQLite manifest.
- **Resumable:** tested with a hard kill (resumed run redid only unfinished files; final result identical to an uninterrupted run). A rerun on an unchanged tree processes 0 files.
- **Reproducible:** different worker counts and schedules give bit-identical embeddings. Video decoder choice depends only on the file, never on timing.
- **Bounded:** peak RAM 3.2–4.7 GB (target ≤ 6 GB), peak VRAM 1.4–1.5 GB including the display (target ≤ 3.5 GB). Output size capped; free-space guard on D:.
- Not tested: kill points other than just after a commit; RAW photos; 8K; 50,000+ files.

## 6. Recommended next steps (in order)
1. **Codec-aware video routing**: send HEVC to the hardware decoder (2.3× faster per file), split H.264 as now. Also allow hardware decoding for seek sampling (long 4K HEVC films are CPU-only today: 6 files = 29% of film-folder time). Expected: moderate, mostly for HEVC-heavy libraries; needs a controlled test.
2. **HEIC reduced-size decode** (or a dedicated HEIC worker pool): 10× the cost of JPEG; needs the same search-level quality gate.
3. **Per-second utilisation timelines** inside the pipeline, to see exactly when the drive and CPU idle; then close the remaining 24% gap to the drive's read floor (for example, order reads by on-disk position, read ahead for large videos).
4. **A larger photo quality check** (the gate used 124 photos, many near-duplicates). Also decide how strict "identical output" must be.
5. **Test hypothesis F properly** (per-stage worker pools that adapt to queue depth), and a hard drive with many small files.
6. **Scale and robustness tests:** 50,000+ files, RAW and WebP photos, 8K and 10-bit video, a second drive and a second laptop.
7. **Rust core: not justified by the evidence.** Nearly all time is in native libraries (libjpeg-turbo, FFmpeg, the GPU decoder), and the gains came from doing less work and from I/O patterns, not from Python overhead. Reconsider only if profiling shows Python or GIL overhead in the thread pools (not yet measured).

## 7. Housekeeping and open choices for you
- **Power plan:** I switched Windows to *High Performance*. To restore: `powercfg /setactive 381b4222-f694-41f0-9685-ff5bb260df2e` (Balanced). Plan matters a lot: the same job ran about 2.7× slower on Balanced.
- **Your external drive:** only read. No file was written or changed on it. The derived files made from it (manifests with paths, thumbnails, embeddings, frames) were deleted after each run; only aggregate numbers are kept. Nothing was uploaded.
- **Disk:** D: has 32.3 GB free (same as at the start); `.venv` is 4.8 GB.
- Software installed into `d:\Roam\.venv` only: PyTorch (CUDA), open_clip, PyAV, Pillow, OpenCV, pillow-heif, Decord, TorchCodec (CPU build, unused), imageio-ffmpeg and a few small libraries.
- Choices that are yours: whether to publish the harness (without any personal data); how strict output equivalence must be; whether HEVC routing and HEIC work come before more scale tests.

## 8. Where everything is
- Plan and tracker: `BENCHMARK_PLAN.md`, `OVERNIGHT_PLAN.md`
- Code: `bench/` (`ingest.py` is the pipeline; `verify_ingest.py` the correctness tests; `overnight.py` the driver; `summarize.py`, `analyze_manifest.py` the reports)
- Results: `results/EXP_COLD.md`, `EXP_UNIFIED.md`, `EXP_HETERO.md`, `EXP_GPU_JPEG.md`, `EXP_REALWORLD.md`, `EXP_A.md`, plus the earlier `EXP_B.md`, `EXP_D.md`, `EXP_HYBRID.md`, `EXP_IMAGES.md`, `EXP_PIPELINE.md`, baselines, and raw aggregate JSON/JSONL files
- Reproduce a run: `.venv\Scripts\python.exe -m bench.ingest <folder> --out-dir out\x --storage ssd|hdd --report results\x.json`
