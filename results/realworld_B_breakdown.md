## Manifest breakdown (aggregate only)

2691 files: 2494 photos, 197 videos, 0 errors ({}). Summed per-file time (read+decode+transform) 1109 s: photos 42.5%, videos 57.5%.

### Photos by format and decode path

| category | files | GiB | p50 ms | p95 ms | summed s | share of summed time |
|---|--:|--:|--:|--:|--:|--:|
| .jpg / draft | 2373 | 6.81 | 106 | 262 | 294.6 | 26.6% |
| .heic / full | 115 | 0.28 | 1082 | 3326 | 175.7 | 15.8% |
| .jpeg / draft | 6 | 0.01 | 80 | 89 | 0.5 | 0.0% |

### Photos by resolution

| category | files | GiB | p50 ms | p95 ms | summed s | share of summed time |
|---|--:|--:|--:|--:|--:|--:|
| <2 MP | 108 | 0.02 | 39 | 74 | 4.6 | 0.4% |
| 2-8 MP | 751 | 1.20 | 64 | 178 | 58.4 | 5.3% |
| 8-12 MP | 198 | 0.49 | 71 | 156 | 16.6 | 1.5% |
| 12-20 MP | 1389 | 5.00 | 147 | 990 | 379.4 | 34.2% |
| >=20 MP | 48 | 0.38 | 159 | 449 | 11.7 | 1.1% |

### Videos by codec and decoder

| category | files | GiB | p50 ms | p95 ms | summed s | share of summed time |
|---|--:|--:|--:|--:|--:|--:|
| h264 / cpu | 91 | 4.18 | 1216 | 6162 | 179.8 | 16.2% |
| hevc / cpu | 41 | 3.31 | 4694 | 18481 | 232.1 | 20.9% |
| h264 / nvdec | 37 | 1.33 | 1200 | 4511 | 69.6 | 6.3% |
| hevc / nvdec | 27 | 1.41 | 2019 | 10647 | 156.0 | 14.1% |
| vp9 / cpu | 1 | 0.00 | 606 | 606 | 0.6 | 0.1% |

### Videos by container

| category | files | GiB | p50 ms | p95 ms | summed s | share of summed time |
|---|--:|--:|--:|--:|--:|--:|
| .mp4 | 162 | 8.61 | 1218 | 7654 | 471.2 | 42.5% |
| .mov | 35 | 1.62 | 4037 | 11774 | 167.0 | 15.1% |

### Videos by resolution

| category | files | GiB | p50 ms | p95 ms | summed s | share of summed time |
|---|--:|--:|--:|--:|--:|--:|
| <=720p | 27 | 0.83 | 437 | 5112 | 39.8 | 3.6% |
| 1080p | 133 | 4.99 | 1555 | 7670 | 328.2 | 29.6% |
| 4K | 37 | 4.41 | 4151 | 22054 | 270.2 | 24.4% |

### Videos by duration

| category | files | GiB | p50 ms | p95 ms | summed s | share of summed time |
|---|--:|--:|--:|--:|--:|--:|
| <10 s | 83 | 1.35 | 581 | 4649 | 88.1 | 7.9% |
| 10-60 s | 101 | 5.34 | 2519 | 7593 | 331.1 | 29.9% |
| 1-10 min | 13 | 3.54 | 8528 | 54147 | 219.0 | 19.7% |

### Videos by sampling strategy

| category | files | GiB | p50 ms | p95 ms | summed s | share of summed time |
|---|--:|--:|--:|--:|--:|--:|
| seq | 196 | 8.79 | 1627 | 8179 | 601.8 | 54.3% |
| seek | 1 | 1.45 | 36345 | 36345 | 36.3 | 3.3% |

- frames decoded per frame returned, strategy seq: 64.0x over 196 videos
- frames decoded per frame returned, strategy seek: 29.7x over 1 videos
