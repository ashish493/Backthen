# ADR-003: Decoder choice is a pure function of the file

Status: accepted · 22 Nov 2026

**Context.** CPU and NVDEC decode the same frame with tiny pixel differences (PSNR ≥ 46 dB). With dynamic work stealing, which decoder handles a clip changed between runs, so outputs were not reproducible (`EXP_HYBRID.md`).
**Decision.** Route each video by its own properties only: codec (HEVC → NVDEC) and a hash of path + size for H.264. No work stealing between decoder pools. Fallback on decoder failure is deterministic and recorded. GPU batches are always padded to a fixed shape.
**Consequence.** Bit-identical embeddings across worker counts and schedules (T1), and across resumed runs (T2, T7). The cost is some load imbalance between pools. It was not measured separately; end-to-end runs stayed within noise of dynamic routing.
