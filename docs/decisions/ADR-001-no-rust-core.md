# ADR-001: No Rust core (yet)

Status: accepted · 22 Nov 2026

**Context.** The problem statement allowed a Rust core "only then", after the evidence was in.
**Evidence.**
- Nearly all time is spent inside native libraries (libjpeg-turbo, FFmpeg, NVDEC, CUDA).
- Every gain came from doing less work or from I/O pattern changes: reduced-size decode 3–4×, smart sampling −66% frames, prefetch 13%, throttling opt-out up to 3×. Faster orchestration contributed none of it (`results/PHASE1_REPORT.md`, `OVERNIGHT_REPORT.md`).
- The GPU waits for input 82–94% of the time, and the USB-drive runs are at the drive's read ceiling.

**Decision.** Keep the ingestion core in Python, calling native libraries.
**Revisit if:** profiling shows Python or GIL overhead taking more than about 15% of wall time in the worker pools (not yet measured), or a feature needs work that no native library provides.
