# ADR-009: Exact (brute-force) vector search until measurements say otherwise

**Status:** accepted, 22 Nov 2026

## Context
The architecture plan named a FAISS flat index for Stage 3 search. The real library has 73,815 frame embeddings of 512 dimensions, about 151 MB in fp32.

## Decision
Score every frame with one numpy matrix-vector product, and take each asset's maximum over its contiguous frame segment (`np.maximum.reduceat`). There is no FAISS dependency and no approximate index.

## Evidence
On the 355 GB index, ranking takes **6 ms p50**. A query takes **32 ms p50 / 37 ms p95** in total, of which the CPU text encoder is 26 ms (`roamengine bench`). That is well under the 200 ms target.

Results are exact and deterministic, so evaluation numbers are not confounded by ANN recall loss.

## Revisit when
- Ranking exceeds about 50 ms p95. That is roughly 10× more frames, about 750k: for example several archives, or denser video sampling.
- A second, larger model makes the matrix larger than about 1 GB.

At that point, consider FAISS `IndexFlatIP` on the GPU first (still exact), then HNSW or IVF behind the same gate, with Recall@K measured against exact search.
