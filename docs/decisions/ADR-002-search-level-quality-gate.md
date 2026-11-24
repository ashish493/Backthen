# ADR-002: Lossy fast paths must pass a search-level quality gate, fixed before measuring

Status: accepted · 22 Nov 2026

**Context.** Several speedups change pixels or frames (reduced-size JPEG decode, NVDEC, keyframe sampling). Bit-identical output would forbid all of them. A per-embedding cosine ≥ 0.999 rule proved too strict: Pillow's own `reducing_gap` setting fails it (`EXP_IMAGES.md`).
**Decision.**
- **Photos:** PSNR ≥ 40 dB, and top-1/top-5 similar-photo overlap within 2 points of a harmless control.
- **Video sampling:** ≥ 95% of videos with median forward coverage ≥ 0.95, and macro p5 ≥ 0.90 (`bench/compare_sampling.py`).
- Gates are written down **before** the runs. A path that fails stays opt-in and labelled as lossy.

**Consequence.** Reduced-size decode at 1/4 was adopted, 1/8 rejected; smart sampling adopted, keyframe and large-τ adaptive rejected as defaults.
**Revisit when** RoamEngine's labelled retrieval evaluation exists. That becomes the real gate.
