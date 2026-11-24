"""Benchmark entry point for the unified ingestion pipeline. Since Phase 3 this is a thin wrapper over the
`roamingest` package (packages/roam-ingest); flags and their defaults are unchanged.

    python -m bench.ingest <root> [<root> ...] --out-dir out/run1 [--tag run1] [--storage ssd|hdd] [...]

The implementation that produced results/ up to commit f071ae3 is in git history (bench/ingest.py at f071ae3).
Differences since then (packages/roam-ingest/CHANGELOG.md): manifest v1 (manifest.sqlite, with read-only views
`files` and `emb` in the v0 layout), probing (EXIF / container / sidecar), video display rotation applied to frames,
video thumbnails, NEF/GIF/3GP support, move and missing-file detection.
"""
import argparse
import sys

from roamingest.config import IngestConfig, add_run_arguments
from roamingest.pipeline import Ingestor
from roamingest.videos import sample_targets  # noqa: F401  (used by bench.compare_sampling)


def parse(argv=None):
    return add_run_arguments(argparse.ArgumentParser()).parse_args(argv)


def run(a, out=sys.stdout):
    return Ingestor(IngestConfig.from_namespace(a), out=out).run()


if __name__ == "__main__":
    run(parse())
