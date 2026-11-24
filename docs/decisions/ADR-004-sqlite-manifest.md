# ADR-004: SQLite manifest with fp16 embeddings is the ingest → engine contract

Status: accepted (v1 schema to be written) · 22 Nov 2026

**Context.** The manifest must be crash-safe, resumable, local and readable by other tools.
**Decision.**
- One SQLite file (WAL mode). A file is marked done only in the same transaction as its embeddings.
- Embeddings are stored as L2-normalised fp16 blobs: 1 KB for ViT-B/32, about 0.3 GB projected for the 355 GB archive.
- Derived files are referenced by paths relative to the dataset; originals by source root + relative path.
- The schema is versioned (`meta.schema_version`).

**Consequence.** Kill-and-resume is safe (T2, T7). RoamEngine builds its own indexes (FAISS, FTS5, R-Tree) from the manifest and can always rebuild them.
**Revisit if** vectors exceed a few million or another consumer needs a columnar format. An export to `.npy`/Parquet can be added without changing the contract.
