# Changelog

## 0.1.1
- **Duplicate skip, on by default (`--dedup skip`).** A file whose size and head/tail content ID match a committed asset is linked without a full read or decode. It is linked only when it is not the duplicate set's owner (the smallest path), so the manifest is identical to `--dedup off`.
- **Disk-order reads (`--read-order auto|path|disk`).** `auto` uses physical on-disk order with `--storage hdd`, with owners first within same-size groups.
- **Optional `--route-key content`.** No longer implied by dedup.
- **355 GB archive:** 98.2 → 87.1 min, 48 GB less read, every row and embedding identical (`results/EXP_SPEED_DEDUP_ORDER.md`).

## 0.1.0 (Phase 3)
Migrated from `bench/ingest.py` (commit f071ae3), which is now a thin wrapper.

Validated against that implementation on the 170-file set A:
- **Photos:** all 124 embeddings bit-identical.
- **Unrotated videos:** identical.
- **Wall time:** 50.1 s, against 49.3 s for the old implementation.

**Manifest v1**
- Tables: `sources`, `assets` (content identity), `asset_files` (file identity), `frames` (with sampling provenance), `embeddings`, `models`, `runs`, `file_stats`.
- Read-only views `files`/`emb` in the v0 layout.
- `roamingest migrate` converts v0 manifests and keeps a backup.

**Probing**
- EXIF capture time, offset, GPS and orientation.
- Video `creation_time` and ISO 6709 location.
- Google Takeout sidecars (`photoTakenTime`, `geoData`, `geoDataExif`), matched by five naming rules.

**Videos**
- Display rotation is read from the stream and applied to every frame. NVDEC frames lose it otherwise. 20 of 46 set A videos are portrait and were previously embedded sideways: the median cosine between the old and corrected frame is 0.77.
- A video thumbnail is taken from the first sampled frame.

**Formats:** NEF via a LibRaw half-size demosaic (the embedded preview is opt-in: it failed the search gate, `results/raw_preview.json`); GIF (first frame); 3GP.

**Change handling**
- Missing-file marking.
- Move detection by content ID.
- Removal of orphaned assets and their thumbnails.
- Deterministic ownership of duplicates.
  - Byte-identical copies used to get different embeddings, because the decoder route hashes the path.
  - Under v1's one-asset-per-content rule, whichever copy finished last won.
  - Now the copy with the smallest path owns the frames.

**Fixes**
- With `--video-cpu 0`, videos in codecs NVDEC cannot decode were queued for a CPU pool that did not exist and were silently never written. They now go to the NVDEC workers, which decode them on the CPU.
- The report's `unaccounted_files` must be 0.

**Other**
- Full content hashes are computed at no extra read cost for files already in memory; `roamingest hash` fills the rest.
- CLI: `inventory`, `run`, `status`, `verify`, `hash`, `migrate`.
- Python API: `Ingestor`, `IngestConfig`, `on_progress` events, cancel token.
- pytest suite on synthetic data.
