# Hypothesis B: random seeking in video causes redundant decoding

Run: `results/exp_b.jsonl` · 20 Nov 2026 · High Performance power plan, AC · warm cache · PyAV CPU, `thread_type=AUTO`
Clips: 12 of 46, every 4th by file size (0.9–25.7 s, 1080p to 4K, 24–60 fps, all H.264). N targets evenly spaced, median of 3 repeats.
Raw data: `results/exp_b.jsonl`. Code: `bench/exp_b_seek.py`.

## Equal-output strategies (identical frames, verified by PTS + pixel hash on all 24 clip×N pairs)

| N targets | strategy | frames decoded | frames returned | decoded / returned | total time | ms per returned frame |
|--:|---|--:|--:|--:|--:|--:|
| 8   | sequential | 5,119   | 96    | 53.3 | 14.0 s  | 146 |
| 8   | seek       | 6,613   | 96    | 68.9 | 23.4 s  | 244 |
| 32  | sequential | 5,670   | 384   | 14.8 | 15.5 s  | 40  |
| 32  | seek       | 28,284  | 384   | 73.7 | 99.5 s  | 259 |
| 128 | sequential | 5,803   | 1,533 | 3.8  | 16.0 s  | 10  |
| 128 | seek       | 115,084 | 1,533 | 75.1 | 401.7 s | 262 |

## Other strategies (NOT proven equal-output)

| N | keyframe-only (time) | decord `get_batch` (time, incl. open) |
|--:|--:|--:|
| 8   | 2.0 s | 14.4 s |
| 32  | 2.0 s | 21.1 s |
| 128 | 1.9 s | 32.8 s |

## Findings

1. **Seek decodes 69–75 frames per frame returned at every N.** Each seek lands on the preceding keyframe and decodes forward.
   With a median GOP of about 156 frames, that averages roughly half a GOP of throw-away frames per target.
2. **Seek is slower than sequential for the same output**: 1.7x at N=8, 6.4x at N=32, 25x at N=128 in aggregate.
   Sequential's total cost barely changes with N (it decodes the clip once); seek's grows linearly with N.
3. **Seek wins only for sparse sampling on long-GOP clips**: at N=8 it was faster on 2 of 12 clips
   (one 1080p60 phone clip 0.57x, one 4K60 clip 0.83x of sequential time).
   Rule of thumb: seek pays off only when N is below roughly twice the number of keyframes in the clip.
4. **Keyframe-only is ~7–8x faster than sequential at N=8, and 8–10x at N=128, but it is not equal output.** The 12 clips contain only 57 keyframes in total (about 5 each),
   so most targets map to the same keyframe. The "returned" counts above include those duplicates; distinct frames are at most 57.
   It gives poor temporal coverage and is not a drop-in replacement.
5. **Decord (CPU) was not faster than sequential PyAV** at any N (14.4 / 21.1 / 32.8 s vs 14.0 / 15.5 / 16.0 s).

## Verdict (against the pre-registered rule: seek decodes ≥ 2x returned frames AND a sequential/keyframe strategy is faster for the same output)

**B: confirmed for per-frame seeking.** It is redundant by about 70x and slower than a single sequential pass at N ≥ 32.

**Important consequence for the pipeline:** the baseline already samples sequentially, so B does not explain the baseline's 75% decode share.
The baseline's 72x decoded/returned ratio is inherent to sparse sampling over long GOPs: a sequential pass must decode the frames
in between. The remaining decode cost is raw H.264 decode throughput (about 370 frames/s overall on this CPU), so the next levers are hardware decode (Hypothesis D)
and a hybrid policy that picks seek only when N is small.

## Caveats
- 12 clips, warm cache, 3 repeats. No cold-cache numbers. Timings are not comparable with the earlier Balanced-plan baseline.
- Decord pixel hashes were recorded but not compared to the PyAV output, and its frame indices were derived from `round(t × fps)`, so frame equality is unverified.
- All clips are H.264. HEVC (1 clip in the dataset) was not covered.
- PyAV `seek` was used with `backward=True`. Other seek modes or `any_frame` were not tried.
- TorchCodec was not run (needs FFmpeg DLLs); it is part of experiment D.
