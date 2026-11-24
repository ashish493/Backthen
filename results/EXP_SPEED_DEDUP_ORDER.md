# Speed: skip duplicates and read in disk order (355 GB archive)

Fixes 1 and 2 from `BOTTLENECK_ARCHIVE355.md`, measured on the same archive, drive and laptop as the v0.1 reference run. Both runs were cold. Only aggregate numbers are shown.

- **Duplicate skip (`--dedup skip`):** when a file's size matches a committed asset, read only its first and last 64 KiB to get the content ID. If the ID is known and the file is not the asset's owner, link it without reading the rest or decoding it.
- **Disk order (`--read-order disk`):** sort files by the physical start of their data (`FSCTL_GET_RETRIEVAL_POINTERS`). Within each group of same-size files, the earliest disk positions go to the smallest paths, so a duplicate set's owner is read before its copies.

## Result: output identical, 11% faster

| | v0.1 reference | dedup + disk order | change |
|---|---|---|---|
| Wall time | 98.2 min | **87.1 min** | **−11.1 min (−11.3%)** |
| Bytes read by the process | 337.0 GB | 288.9 GB | −48.1 GB (−14.3%) |
| Files linked without decoding | 0 | 18,883 (17,312 photos, 1,571 videos) | 52.6 GB not read |
| Photos under 1 MB, read time per file | 29.1 ms | 25.0 ms | −14% |
| Photos 1–10 MB, read time per file | 68.9 ms | 63.0 ms | −9% |
| Cost of computing disk order | n/a | 7.5 s | |
| Peak RAM / VRAM | 4.28 GB / 1.12 GB | 3.85 GB / 1.12 GB | |
| Decode errors | 5 | 5 (same files) | |
| Files unaccounted for | 0 | 0 | |

**Equality with the reference** (`bench/compare_fast.py`, `results/archive_fast_compare.json`):
- 67,512 file rows each: 0 differ, 0 only in one manifest.
- 48,569 assets each: 0 metadata differences, 0 frame-timestamp differences.
- Photo embeddings: 44,014 frames, all bit-identical.
- Video embeddings: 29,801 frames, all bit-identical.

## Why the first attempt was not identical, and the fix

Attempt 1 (`results/archive_fast_compare_attempt1.json`) ran in 84.7 min but differed in 211 capture times and 1,444 video embeddings.
- **Ownership:** whichever copy was read first became the decoded copy. The manifest rule (ADR-008) says the smallest path owns a duplicate set, and the owner's sidecar supplies the capture time.
- **Routing:** dedup implied content-hash routing for the H.264 NVDEC/CPU split, which gave different decoders, and so different embeddings, than the reference's path routing.

The fix:
- A copy is linked only if its `(source, path)` is greater than the asset's current writer.
- Disk order puts owners first within each size group.
- Routing stays on path.

The output is now the same as a run without either option. The last 2.4 min of attempt 1's lead came from content routing and is given up for exactness.

## Against the projection

The projection was 65–75 min; the measurement is 87 min.
- **Duplicate skip delivered as projected:** 52.6 GB was not read.
- **Disk order delivered less:** small-photo latency went from 29 to 25 ms, against a projected 10–15 ms. The small files on this drive are spread across many directories written at different times, so physical order removes little head travel. Rotational latency and per-request USB overhead remain.

The remaining ideas from the bottleneck write-up are still open:
- Two outstanding reads for small files only.
- Faster storage.
- The NTFS change journal for reruns.

## Defaults from now on

Both options are now on by default (`roamingest` 0.1.1):
- `--dedup skip`.
- `--read-order auto`, which means disk order when `--storage hdd` and path order otherwise.

Both cases fall back cleanly: `--dedup off` or `--read-order path` restore the old behaviour, and a file without extent information sorts first. Tests: `packages/roam-ingest/tests/test_dedup_order.py`, where each dedup and disk-order case equals a run without the option. 44 ingest tests pass.

**Caveat:** this is one run. Run-to-run variation on this drive was not measured at full scale. The app may have been compiling or running during part of it, which would only have slowed it.
