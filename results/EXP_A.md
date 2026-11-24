# Hypothesis A: many small files cause significant filesystem overhead

Runs: `results/exp_a_3000.jsonl`, `results/exp_a_12000.jsonl` · 21 Nov 2026 · internal NVMe SSD · High Performance plan · 3 repeats, medians.
Data (synthetic, deleted after the run): 3,000 and 12,000 JPEGs of 50–300 KB (average 122 KB) in a 3-level directory tree, and for each size the SAME total bytes as 20 large files.
Code: `bench/exp_a_smallfiles.py`, `bench/gen_synthetic.py`.

## Read speed, small files vs the same bytes in 20 large files

| files | cache | threads | small files MB/s | files/s | large files MB/s | small-file time vs large |
|--:|---|--:|--:|--:|--:|--:|
| 3,000 | cold | 1 | 492 | 3,841 | 1,739 | 3.5× slower |
| 3,000 | cold | 4 | 1,242 | 9,692 | 2,412 | 1.9× |
| 3,000 | cold | 16 | 1,133 | 8,844 | 2,434 | 2.2× |
| 3,000 | warm | 1 | 2,316 | 18,071 | 7,221 | 3.1× |
| 3,000 | warm | 4 | 1,134 | 8,847 | 17,255 | 15.2× |
| 3,000 | warm | 16 | 1,187 | 9,266 | 12,590 | 10.6× |
| 12,000 | cold | 1 | 513 | 4,001 | 1,766 | 3.4× |
| 12,000 | cold | 4 | 1,492 | 11,645 | 2,465 | 1.7× |
| 12,000 | cold | 16 | 1,273 | 9,933 | 2,447 | 1.9× |
| 12,000 | warm | 1 | 2,100 | 16,391 | 7,895 | 3.8× |
| 12,000 | warm | 4 | 1,276 | 9,955 | 19,578 | 15.4× |
| 12,000 | warm | 16 | 1,272 | 9,926 | 16,225 | 12.8× |

## Other per-file costs
| cost | 3,000 files | 12,000 files |
|---|--:|--:|
| directory listing + stat (`os.scandir`) | 5.8 µs/file (17 ms total) | 2.8 µs/file (34 ms total) |
| full hash (xxh3, warm) | 49 µs/file | 53 µs/file |
| partial hash (size + first/last 64 KiB, warm) | 38 µs/file | 42 µs/file |

## Verdict: rejected (as a pipeline bottleneck, at this scale, on an SSD)
Rule from BENCHMARK_PLAN §6: confirmed if file-system overhead exceeds 15% of end-to-end time at the pipeline's rate, or if per-file cost grows non-linearly with file count.
1. **The per-byte penalty is real but small in absolute terms.** Per small file: about 0.1 ms to open and read (about 10,000 files/s), plus about 0.04 ms to hash and 0.003–0.006 ms to list: about 0.15 ms in total.
   The photo pipeline spends about 10 ms of wall time per photo (about 100 photos/s). File-system overhead is therefore **about 1.5%** of that, far below the 15% bar. Real photos are also 25× larger than these test files, so their overhead per byte is lower still.
2. **It scales linearly:** reading 4× as many files took 4× as long (4,000 files/s cold single-threaded at both sizes). Listing cost per file did not grow (5.8 → 2.8 µs).
3. Partial hashing saves only about 10 µs/file over full hashing of small files (its benefit is for large files, where a full hash reads everything).

## Side finding
Warm small-file reads were **about 2× slower with 4–16 threads than with 1** (18,071 → 8,847–9,266 files/s), while large files scaled up (7.2 → 17.3 GB/s).
That points to a per-file lock or filter in the Windows file-open path (possibly antivirus real-time scanning; not investigated or controlled). It does not matter for the pipeline today (ratio to compute stays tiny) but would matter for much smaller files or faster decode.

## Caveats
- Synthetic files (random-content JPEGs, tiny compared with real photos). Real-time antivirus scanning was not disabled or measured.
- SSD only. **A hard drive with many small files was not tested**; seeks could dominate there (the 3 MB photos already read at only 23 files/s on the USB drive).
- Up to 12,000 files; the problem statement's 50,000+ scale was not reached (disk budget).
