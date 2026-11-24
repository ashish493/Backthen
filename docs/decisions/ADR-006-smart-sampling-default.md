# ADR-006: Smart sampling by default; keyframe sampling opt-in

Status: accepted · 22 Nov 2026

**Evidence** (`EXP_SAMPLING.md`).
- **Smart** (exact frames, jumps over keyframe intervals without targets) decodes 66% fewer frames on the set B archive with bit-identical output (T5). It is 14% faster with prefetch.
- **Keyframe-only** is 1.5–3.2× faster but fails the quality gate (27–67% of videos keep median coverage ≥ 0.95). Up to 35% of requested frames collapse onto shared keyframes.
- **Adaptive** passes only with τ = 0.5 s and is then no faster than smart with prefetch.

**Decision.** Default `--video-strategy smart` with NVDEC allowed for seeks. `--video-sampling keyframe` is a documented lossy fast mode; each frame's method is recorded in the manifest.
**Revisit** with RoamEngine's labelled video queries, and for film-like libraries where adaptive with a larger τ may pass.
