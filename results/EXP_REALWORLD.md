# Step 8: a larger real-world archive (diverse formats, codecs, resolutions)

Date: 21 Nov 2026 · aggregate numbers only (categories, counts, sizes, timings; no names, paths, or content). Source table: `results/realworld_B_breakdown.md`.
The archive lives on the external USB hard drive and was read-only; its derived files (manifest with paths, thumbnails, embeddings) were deleted after the aggregates were computed.

## The archive (set B, a personal photo and video archive)
| | B |
|---|---|
| files processed | 2,691 (2,494 photos, 197 videos), 0 errors |
| size | 17.3 GiB |
| photo formats | 2,379 JPEG (0.85–24.6 MB), 115 HEIC |
| video containers | 162 MP4, 35 MOV |
| video codecs | 128 H.264, 68 HEVC, 1 VP9 |
| video resolutions | 27 ≤720p, 133 1080p, 37 4K |
| video durations | 83 under 10 s, 101 of 10–60 s, 13 of 1–10 min |
| wall time (cold, optimised) | 286 s (prefetch configuration, median of 3) |
| how much was read from disk | all (17.3 GiB at 62 MB/s) |

## Findings

1. **Every format in this archive worked without errors,** including HEIC photos, HEVC and VP9 video, MOV containers and PNG.
   One video that failed on the hardware decoder in the combined A+B run was redone on the CPU automatically.
2. **HEIC is the costliest photo format by far.** The 115 HEIC photos (4.6% of photos, 0.28 GiB) take a median of **1,082 ms each against 106 ms for a JPEG**, and make up **15.8% of all per-file time**.
   HEIC uses the full-size decode path (no reduced-size shortcut exists for it).
3. **HEVC costs about twice as much as H.264 per file, and hardware decoding pays off for HEVC but not for H.264:**

   | codec / decoder | videos | median ms per file | p95 ms |
   |---|--:|--:|--:|
   | H.264 on CPU | 91 | 1,216 | 6,162 |
   | H.264 on NVDEC | 37 | 1,200 | 4,511 |
   | HEVC on CPU | 41 | 4,694 | 18,481 |
   | HEVC on NVDEC | 27 | 2,019 | 10,647 |

   NVDEC matches the CPU for H.264 (consistent with `EXP_D.md`) but is **2.3× faster at the median for HEVC**. Files were routed by a hash, so the groups are similar in kind but not identical, and this is per-file latency under load, not an isolated benchmark.
   The pipeline's rule at the time (40% of all videos to NVDEC, regardless of codec) therefore left 41 HEVC files on the slow path.
4. **The big 4K and long files dominate video cost:** the 37 4K videos (19% of videos) take 24% of summed time (median 4.2 s, p95 22 s); the 13 videos of 1–10 minutes take 20%.
5. **Mid-size JPEGs make up the biggest photo share of time:** the 1,389 photos of 12–20 MP take 34% of all per-file time (the common phone resolution); photos under 8 MP take 6%.
6. **Sequential sampling decodes 64 frames per frame returned** (it walks through most of each clip to pick up to 32 frames). That was the biggest remaining waste in the video path, though experiment B showed per-frame seeking is slower for dense sampling and only wins when targets are sparse relative to keyframes.
7. **Time split:** photos 42.5% of summed per-file time (JPEG draft 26.6%, HEIC 15.8%), videos 57.5% (HEVC 35%, H.264 22%, mostly 1080p and 4K). Other formats are negligible.

## What this added to the bottleneck picture
- On the SSD, with phone-camera data like set A, CPU video decode is the limit. On the USB drive with a real archive, the drive is the limit, and after the in-memory video read change (see `EXP_HETERO.md`) video decode and HEIC are the largest remaining compute items.
- **Concrete improvements these tables suggested** (codec-aware routing and smarter sampling were built afterwards, see `PHASE1_REPORT.md`):
  1. **Codec-aware routing:** send HEVC to NVDEC, H.264 split by hash. Moves about 41 slow CPU decodes to the faster engine.
  2. **HEIC:** try a reduced-size decode (libheif thumbnail/size hint) or run HEIC on a dedicated worker pool. Needs the same search-level quality gate as JPEG.
  3. A smarter sampling rule for 10–60 s clips, where the sequential pass decodes about 64× the frames it keeps.

## Caveats
- Cost shares come from per-file times that include time waiting for the drive and CPU contention between workers, so absolute milliseconds depend on the configuration (run with prefetch).
- The archive was run once for this breakdown. The wall times in `EXP_HETERO.md` have repeats.
- The HEVC-vs-H.264 routing comparison is observational. A controlled test (same clips on both engines) is still to do.
- One archive of one person's photos; other libraries (different cameras, codecs, 8K, RAW photos) are not covered. RAW photos, PNG screenshots at scale, WebP, and 10-bit HEVC were not exercised in meaningful numbers.
