# ADR-008: Manifest v1 identity, frames and duplicates

Status: accepted · 22 Nov 2026 (refines the proposal in ROAMINDEX_ARCHITECTURE.md §3)

**Decisions**
1. **Two identity tables.**
   - `asset_files` (`source_id`, `rel_path`) is file identity.
   - `assets` (`asset_id` = partial content ID) is content identity.
   - The proposal put `rel_path` in `assets`, which cannot hold two copies of the same content (§6 Q10).
2. **Photos have one frame.**
   - Every embedding is keyed by (`asset_id`, `frame_index`, `model_id`). A photo is frame 0 with `sampling_method = 'image'`.
   - This replaces the proposal's `owner_type`/`owner_id`, so the engine has one code path.
3. **Duplicate content has one owner.**
   - When several files share an `asset_id`, the frames come from the file with the smallest (`source_id`, `rel_path`).
   - Evidence: in the old implementation, two byte-identical copies in the set A got different embeddings, because the decoder route hashes the path (NVDEC vs CPU).
   - Under one asset per content, the stored result then depended on which copy finished last. The owner rule makes it independent of timing and of crash/resume order.
4. **`asset_id` never changes** once assigned.
   - `content_hash_full` is an extra column, filled at ingest when the bytes are in memory, otherwise by `roamingest hash`.
   - It detects partial-ID collisions; it does not replace the ID.
5. **Video frames are stored upright.** The stream's display rotation is applied to every frame and recorded in `assets.rotation`.
   - NVDEC output carries no rotation, so it is read from the stream, not the frame.
   - 20 of the 46 set A videos are portrait, and their old embeddings were of sideways frames (median cosine to the upright frame: 0.77).
6. **Compatibility views.** Read-only views `files` and `emb` reproduce the v0 layout, so the benchmark tools in `bench/` keep working. They are deprecated and not part of the engine contract.

**Consequences**
- Embeddings of rotated videos differ from every result before Phase 3; photo and unrotated-video embeddings are bit-identical.
- Duplicates are cheap: they still cost a decode at ingest in v0.1, but only one set of frames is stored.

**Revisit**
- Skipping the decode of known duplicates; this would need the content ID before dispatch.
- Display-matrix flips (mirroring).
