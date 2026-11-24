# Step 8: larger real-world archives (diverse formats, codecs, resolutions)

Date: 21 Nov 2026 · aggregate numbers only (categories, counts, sizes, timings; no names, paths, or content). Source tables: `results/realworld_B_breakdown.md`, `results/realworld_C_breakdown.md`.
Both archives live on the external USB hard drive and were read-only; their derived files (manifest with paths, thumbnails, embeddings) were deleted after the aggregates were computed.

## The two archives
| | B: personal set B archive | C: long films and series |
|---|---|---|
| files processed | 2,691 (2,494 photos, 197 videos), 0 errors | 168 media files (22 photos, 146 videos), 1 error |
| size | 17.3 GiB | 132.5 GiB of video |
| photo formats | 2,379 JPEG (0.85–24.6 MB), 115 HEIC | 11 JPEG, 11 PNG (thumbnails and posters) |
| video containers | 162 MP4, 35 MOV | 102 MKV, 43 MP4, 1 AVI |
| video codecs | 128 H.264, 68 HEVC, 1 VP9 | 104 H.264, 40 HEVC, 1 MPEG-4 |
| video resolutions | 27 ≤720p, 133 1080p, 37 4K | 122 ≤720p, 17 1080p, 6 4K |
| video durations | 83 under 10 s, 101 of 10–60 s, 13 of 1–10 min | 140 over 10 min (up to several hours) |
| not media, skipped | none | 74 subtitles, 36 text, 8 .nfo, and one each of: partial download, PDF, ZIP, RAR |
| wall time (cold, optimised) | 286 s (prefetch configuration, median of 3) | 220 s (4 CPU workers, run once) |
| how much was read from disk | all (17.3 GiB at 62 MB/s) | about 3.8% (23 MB/s average): sparse seeks, not whole files |

## Findings

1. **Every format in these archives worked without errors,** including HEIC photos, HEVC and VP9 video, MOV/MKV/AVI containers and PNG. The one failure (C, `IndexError`) is a file with no video stream.
   One video that failed on the hardware decoder in the combined A+B run was redone on the CPU automatically.
2. **Films are cheap with seek sampling: 132 GiB in 220 s** by reading only the sampled spots (32 frames per film, 4,559 frames in total).
   Whole-file decoding would have taken hours. Seek sampling decodes about 63 frames per frame returned (about half a keyframe interval each), which is still tiny against about 170,000 frames in a 2-hour film.
3. **HEIC is the costliest photo format by far.** The 115 HEIC photos (4.6% of photos, 0.28 GiB) take a median of **1,082 ms each against 106 ms for a JPEG**, and make up **15.8% of all per-file time in B**.
   HEIC uses the full-size decode path (no reduced-size shortcut exists for it).
4. **HEVC costs about twice as much as H.264 per file, and hardware decoding pays off for HEVC but not for H.264:**

   | codec / decoder (archive B) | videos | median ms per file | p95 ms |
   |---|--:|--:|--:|
   | H.264 on CPU | 91 | 1,216 | 6,162 |
   | H.264 on NVDEC | 37 | 1,200 | 4,511 |
   | HEVC on CPU | 41 | 4,694 | 18,481 |
   | HEVC on NVDEC | 27 | 2,019 | 10,647 |

   NVDEC matches the CPU for H.264 (consistent with `EXP_D.md`) but is **2.3× faster at the median for HEVC**. Files were routed by a hash, so the groups are similar in kind but not identical, and this is per-file latency under load, not an isolated benchmark.
   The pipeline's current rule (40% of all videos to NVDEC, regardless of codec) therefore leaves 41 HEVC files on the slow path.
5. **The big 4K and long files dominate video cost:** in B the 37 4K videos (19% of videos) take 24% of summed time (median 4.2 s, p95 22 s); the 13 videos of 1–10 minutes take 20%.
   In C the 6 4K films take **28.6% of the time** (median 36.6 s each, mostly HEVC on the CPU) even with seek sampling.
6. **Mid-size JPEGs make up the biggest photo share of time in B:** the 1,389 photos of 12–20 MP take 34% of all per-file time (the common phone resolution); photos under 8 MP take 6%.
7. **Sequential sampling decodes 64 frames per frame returned in B** (it walks through most of each clip to pick up to 32 frames). That is the biggest remaining waste in the video path, though experiment B showed per-frame seeking is slower for dense sampling and only wins when targets are sparse relative to keyframes.
8. **Time split in B:** photos 42.5% of summed per-file time (JPEG draft 26.6%, HEIC 15.8%), videos 57.5% (HEVC 35%, H.264 22%, mostly 1080p and 4K).
   Other formats are negligible. In C, videos are 99.8% of time.

## What this adds to the bottleneck picture
- On the SSD, with phone-camera data like set A, CPU video decode is the limit. On the USB drive with real archives, the drive is the limit, and after the in-memory video read change (see `EXP_HETERO.md`) video decode and HEIC are the largest remaining compute items.
- **Cheap, concrete improvements suggested by these tables (not yet implemented):**
  1. **Codec-aware routing:** send HEVC to NVDEC, H.264 split by hash. Moves about 41 slow CPU decodes in B to the faster engine.
  2. **NVDEC for seek sampling** (needs a decoder flush after each seek): the long-film 4K HEVC files are currently CPU-only.
  3. **HEIC:** try a reduced-size decode (libheif thumbnail/size hint) or run HEIC on a dedicated worker pool. Needs the same search-level quality gate as JPEG.
  4. A smarter sampling rule for 10–60 s clips, where the sequential pass decodes about 64× the frames it keeps.

## Caveats
- Cost shares come from per-file times that include time waiting for the drive and CPU contention between workers, so absolute milliseconds depend on the configuration (B run with prefetch, C with 4 CPU workers).
- B and C were each run once for this breakdown. The wall times in `EXP_HETERO.md` have repeats.
- The HEVC-vs-H.264 routing comparison is observational. A controlled test (same clips on both engines) is still to do.
- One archive of one person's photos and one set of films; other libraries (different cameras, codecs, 8K, RAW photos) are not covered. RAW photos, PNG screenshots at scale, WebP, and 10-bit HEVC were not exercised in meaningful numbers.
