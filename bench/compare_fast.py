"""Compare a manifest built with speed options (--dedup skip, --read-order disk) against the reference manifest of the
same archive. Aggregate output only (no paths or metadata values).

    python -m bench.compare_fast out/archive355 out/archive355_fast results/archive_fast_compare.json

Checks: same files -> same assets and status; identical asset metadata; identical frame timestamps; embeddings
bit-identical for photos; for videos, per-frame cosine (content routing can move an H.264 video between NVDEC and CPU,
which the T6 bound allows: cosine >= 0.98). Also per size class read rates of the files actually read (not linked).
"""
import json
import sqlite3
import sys

import numpy as np


def main(ref_dir, new_dir, out_json):
    con = sqlite3.connect(f"file:{new_dir}/manifest.sqlite?mode=ro", uri=True)
    con.execute(f"ATTACH 'file:{ref_dir}/manifest.sqlite?mode=ro' AS r")
    q = lambda s, *a: con.execute(s, a).fetchall()  # noqa: E731
    res = {}
    res["files"] = dict(new=q("SELECT COUNT(*) FROM asset_files")[0][0], ref=q("SELECT COUNT(*) FROM r.asset_files")[0][0])
    res["file_rows_differ"] = q("""SELECT COUNT(*) FROM asset_files f JOIN r.asset_files g ON g.rel_path = f.rel_path
        WHERE f.asset_id IS NOT g.asset_id OR f.status != g.status OR f.n_emb != g.n_emb""")[0][0]
    res["files_only_in_one"] = q("""SELECT (SELECT COUNT(*) FROM asset_files WHERE rel_path NOT IN (SELECT rel_path FROM r.asset_files))
        + (SELECT COUNT(*) FROM r.asset_files WHERE rel_path NOT IN (SELECT rel_path FROM asset_files))""")[0][0]
    cols = ("media_type, format, width, height, orientation, rotation, capture_time_utc, capture_time_local, "
            "capture_time_source, lat, lon, gps_source, codec, duration_s, n_frames")
    res["assets"] = dict(new=q("SELECT COUNT(*) FROM assets")[0][0], ref=q("SELECT COUNT(*) FROM r.assets")[0][0])
    res["asset_metadata_differ"] = q(f"""SELECT COUNT(*) FROM (SELECT asset_id, {cols} FROM assets EXCEPT
        SELECT asset_id, {cols} FROM r.assets)""")[0][0]
    res["frame_timestamps_differ"] = q("""SELECT COUNT(*) FROM frames f JOIN r.frames g USING(asset_id, frame_index)
        WHERE f.timestamp_s IS NOT g.timestamp_s OR f.sampling_method != g.sampling_method""")[0][0]
    rows = q("""SELECT a.media_type, e.vector, g.vector FROM embeddings e JOIN r.embeddings g
        USING(asset_id, frame_index, model_id) JOIN assets a USING(asset_id)""")
    emb = {"photo": [0, 0, []], "video": [0, 0, []]}          # [frames, identical, cosines of differing frames]
    for kind, v1, v2 in rows:
        e = emb[kind]
        e[0] += 1
        if v1 == v2:
            e[1] += 1
        else:
            a, b = np.frombuffer(v1, np.float16).astype(np.float32), np.frombuffer(v2, np.float16).astype(np.float32)
            e[2].append(float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b))))
    for kind, (n, same, cos) in emb.items():
        c = np.array(cos) if cos else np.array([1.0])
        res[f"{kind}_embeddings"] = dict(frames=n, identical=same, differ=len(cos), cos_min=round(float(c.min()), 4),
                                         cos_p5=round(float(np.percentile(c, 5)), 4),
                                         cos_median=round(float(np.median(c)), 4),
                                         below_t6_0_98=int((c < 0.98).sum()) if cos else 0)
    res["video_assets_with_any_difference"] = q("""SELECT COUNT(DISTINCT asset_id) FROM embeddings e JOIN r.embeddings g
        USING(asset_id, frame_index, model_id) WHERE e.vector != g.vector""")[0][0]
    # read rate of files actually read, per size class (new run vs reference per-file telemetry)
    buckets = ("CASE WHEN f.size<1e6 THEN 'a<1MB' WHEN f.size<1e7 THEN 'b1-10MB' WHEN f.size<1e8 THEN 'c10-100MB' "
               "ELSE 'd>=100MB' END")
    rr = {}
    for db, tag in (("main", "new"), ("r", "ref")):
        for b, n, mb, s in q(f"""SELECT {buckets}, COUNT(*), SUM(f.size)/1e6, SUM(st.t_read_ms)/1e3 FROM {db}.asset_files f
            JOIN {db}.file_stats st USING(source_id, rel_path) WHERE f.kind='photo' AND st.strategy != 'dedup_link'
            AND st.t_read_ms IS NOT NULL GROUP BY 1"""):
            rr.setdefault(b, {})[tag] = dict(files=n, read_s=round(s), mb_per_s=round(mb / s, 1) if s else None,
                                            ms_per_file=round(1e3 * s / n, 1))
    res["photo_read_rate_by_size"] = rr
    res["links"] = dict(q("SELECT f.kind, COUNT(*) FROM file_stats st JOIN asset_files f USING(source_id, rel_path) "
                          "WHERE st.strategy='dedup_link' GROUP BY 1"))
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:4])
