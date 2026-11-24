## Manifest breakdown (aggregate only)

168 files: 22 photos, 145 videos, 1 errors ({('video', 'IndexError'): 1}). Summed per-file time (read+decode+transform) 826 s: photos 0.2%, videos 99.8%.

### Photos by format and decode path

| category | files | GiB | p50 ms | p95 ms | summed s | share of summed time |
|---|--:|--:|--:|--:|--:|--:|
| .jpg / draft | 11 | 0.00 | 52 | 119 | 0.7 | 0.1% |
| .png / full | 11 | 0.01 | 68 | 97 | 0.7 | 0.1% |

### Photos by resolution

| category | files | GiB | p50 ms | p95 ms | summed s | share of summed time |
|---|--:|--:|--:|--:|--:|--:|
| <2 MP | 22 | 0.01 | 63 | 102 | 1.4 | 0.2% |

### Videos by codec and decoder

| category | files | GiB | p50 ms | p95 ms | summed s | share of summed time |
|---|--:|--:|--:|--:|--:|--:|
| h264 / cpu | 104 | 69.91 | 4062 | 6771 | 424.5 | 51.4% |
| hevc / cpu | 40 | 61.94 | 4712 | 37811 | 396.9 | 48.0% |
| mpeg4 / cpu | 1 | 0.69 | 3615 | 3615 | 3.6 | 0.4% |

### Videos by container

| category | files | GiB | p50 ms | p95 ms | summed s | share of summed time |
|---|--:|--:|--:|--:|--:|--:|
| .mkv | 102 | 104.63 | 4100 | 22804 | 647.5 | 78.3% |
| .mp4 | 42 | 27.22 | 4373 | 6711 | 173.9 | 21.0% |
| .avi | 1 | 0.69 | 3615 | 3615 | 3.6 | 0.4% |

### Videos by resolution

| category | files | GiB | p50 ms | p95 ms | summed s | share of summed time |
|---|--:|--:|--:|--:|--:|--:|
| <=720p | 122 | 43.54 | 3932 | 6672 | 480.0 | 58.1% |
| 1080p | 17 | 39.24 | 6164 | 8906 | 109.0 | 13.2% |
| 4K | 6 | 49.76 | 36640 | 60761 | 236.0 | 28.6% |

### Videos by duration

| category | files | GiB | p50 ms | p95 ms | summed s | share of summed time |
|---|--:|--:|--:|--:|--:|--:|
| <10 s | 3 | 0.00 | 305 | 335 | 0.9 | 0.1% |
| 1-10 min | 2 | 0.17 | 13950 | 22558 | 27.9 | 3.4% |
| >10 min | 140 | 132.36 | 4153 | 8811 | 796.2 | 96.3% |

### Videos by sampling strategy

| category | files | GiB | p50 ms | p95 ms | summed s | share of summed time |
|---|--:|--:|--:|--:|--:|--:|
| seek | 140 | 132.36 | 4153 | 8811 | 796.2 | 96.3% |
| seq | 5 | 0.18 | 338 | 19688 | 28.8 | 3.5% |

- frames decoded per frame returned, strategy seq: 44.7x over 5 videos
- frames decoded per frame returned, strategy seek: 63.2x over 140 videos
