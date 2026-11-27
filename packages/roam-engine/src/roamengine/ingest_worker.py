"""Run one RoamIngest job and stream its progress as JSON lines on stdout (used by the engine's IngestManager).

Usage: python -m roamengine.ingest_worker '<json IngestConfig fields>'
Killing this process at any time is safe: RoamIngest commits per file and the next run resumes.
"""
import json
import sys
import time


def main():
    cfg = json.loads(sys.argv[1])
    from roamingest.config import IngestConfig
    from roamingest.pipeline import Ingestor
    out = sys.stdout

    def emit(ev):
        ev["t"] = round(time.time(), 3)
        out.write(json.dumps(ev) + "\n")
        out.flush()

    from roamingest.pipeline import IngestError
    keep = ("files_processed", "files_resumed_skipped", "files_moved", "files_missing", "unaccounted_files",
            "wall_s", "errors", "peak_rss_mib", "peak_vram_mib", "cancelled", "failure")
    emit(dict(event="starting"))
    try:
        rep = Ingestor(IngestConfig(**cfg, quiet=True), on_progress=emit).run()
        emit(dict(event="report", **{k: rep.get(k) for k in keep}))
    except IngestError as e:                              # did not complete; what was saved is kept, a rerun resumes
        emit(dict(event="failed", error=str(e), report={k: (e.report or {}).get(k) for k in keep}))
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        emit(dict(event="failed", error=f"{type(e).__name__}: {e}"))
        sys.exit(1)


if __name__ == "__main__":
    main()
