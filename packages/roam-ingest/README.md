# RoamIngest

Local, resumable ingestion of large mixed photo and video archives into a versioned manifest with CLIP embeddings.
Nothing leaves the machine.

## What it does
1. **Discover** media under one or more source roots:
   - Photos: JPEG, PNG, WebP, HEIC/HEIF, GIF (first frame), Nikon NEF (half-size demosaic; `--raw-decode preview` for the faster embedded preview).
   - Videos: MP4, MOV, MKV, AVI, M4V, WebM, 3GP.
   - Takeout-style `.json` sidecars, matched by file name.
2. **Read each file once.** Hard drives get one sequential reader; small videos are read whole by it.
3. **Decode:**
   - Photos: reduced-size JPEG decode.
   - Videos: exact "smart" frame sampling, one frame every 2 s, at most 32 per video. HEVC goes to NVDEC, and display rotation is applied.
4. **Probe** capture time and GPS from EXIF, container tags and sidecars, recording each value's source.
5. **Embed** with OpenCLIP ViT-B/32 (fp16 on CUDA, fp32 on CPU) in fixed-shape batches. The result is deterministic.
6. **Write** `manifest.sqlite` (schema v1, [`shared/schemas/manifest_v1.sql`](../../shared/schemas/manifest_v1.sql)), thumbnails, and an aggregate `report.json`.

## Use
```
pip install -e packages/roam-ingest[raw,test]
roamingest inventory E:\archive                       # discovery only, aggregate counts
roamingest run E:\archive --out-dir D:\roam_dataset --storage hdd
roamingest status D:\roam_dataset
roamingest verify D:\roam_dataset [--rehash]
roamingest hash D:\roam_dataset                       # fill full content hashes (background-friendly)
```
```python
from roamingest import Ingestor, IngestConfig
rep = Ingestor(IngestConfig(roots=["E:/archive"], out_dir="D:/roam_dataset", storage="hdd"),
               on_progress=print).run()
```

### Resuming and changes on disk
- **Rerun:** running again on the same `--out-dir` resumes. Files with an unchanged size and modification time are skipped.
- **Kill-safe:** a run can be killed at any time. Files are committed with their embeddings in the same transaction.
- **Missing files:** files no longer found are marked `missing`. This only happens on full scans, without `--limit` or `--stride`.
- **Moves:** a file that reappears at a new path with the same content is relinked without decoding.
- **Duplicates:** byte-identical copies share one asset. The copy with the smallest path owns the frames.

## Defaults and the evidence behind them
Defaults are set in `config.py`. The evidence is in `results/PHASE1_REPORT.md` and `docs/decisions/ADR-*.md`:
- **Smart sampling:** exact frames, with 66% fewer decoded frames than sequential decoding.
- **Power throttling opt-out:** Windows EcoQoS made runs 2–3× slower.
- **Codec routing:** HEVC goes to NVDEC.
- **One reader on hard drives,** plus whole-file prefetch for videos of 256 MB or less.
- **Duplicate skip and disk-order reads on hard drives:** byte-identical copies are linked without decoding, and files are read in physical order. Output is identical to a run without either; on the 355 GB archive the run took 98 → 87 min (`results/EXP_SPEED_DEDUP_ORDER.md`). Turn them off with `--dedup off` or `--read-order path`.

## Privacy
- Console output, `report.json` and the `runs` table contain aggregate numbers only: no file names, paths or metadata values.
- Personal metadata (times, GPS) is stored only in the local manifest. Sidecar fields other than capture time and location, such as people and descriptions, are never read into the manifest.

## Tests
`pytest packages/roam-ingest` uses only a synthetic dataset generated at test time (`tests/synth.py`).

NVDEC tests are skipped without CUDA. The real-data checks remain in `bench/verify_ingest.py` (T1–T7).

## Limitations (v0.1)
- **Partial content IDs:** the content ID is computed from the size and the first and last 64 KiB. Edits in the middle of a file are caught only by `verify --rehash`, and `hash` reports any partial-ID collisions.
- **RAW formats:** only Nikon NEF is validated (`results/raw_preview.json`): the embedded preview failed the search gate (top-10 overlap 0.868 vs 0.98 for a half-size demosaic), so demosaicing is the default. Other RAW formats are counted as unsupported files.
- **Video frames:** display-matrix flips (mirroring) are ignored; only rotation is applied.
- **Capture time:** EXIF times without an offset tag stay local times (`capture_time_source = exif_local`) unless a sidecar or container gives UTC. File modification time is never used.
- **One embedding model at a time:** the legacy views `files` and `emb` assume a single model.
