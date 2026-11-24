# ADR-005: Read each file once; one sequential reader on hard drives

Status: accepted · 22 Nov 2026

**Evidence.**
- The USB hard drive reads 69 MB/s with one reader and 52–55 MB/s with two or more (`EXP_COLD.md`).
- Reading a file twice (hash, then decode) caused second disk reads when the data set exceeded the file cache.
- Reading small videos through the same single reader and decoding from memory gave 13% on its own and is part of the current best (`EXP_HETERO.md`, `EXP_SAMPLING.md`).

**Decision.**
- Photos: read whole into a bounded byte budget; the content ID is computed from those bytes.
- `--storage hdd`: one reader, and videos ≤ 256 MB prefetched by it. SSD: 4 readers, no prefetch.
- Large videos are read by the decoder with seeks.

**Revisit for** archives on network storage (not in scope) or when RoamIngest gains automatic storage detection.
