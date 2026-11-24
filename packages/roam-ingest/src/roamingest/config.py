"""IngestConfig: every tunable of a run, with the v0.1 defaults (results/PHASE1_REPORT.md).

The same fields back the Python API, the `roamingest run` CLI and the benchmark wrapper bench/ingest.py.
Benchmarks before commit 330c1df used: video_strategy=auto, route=hash, nvdec_seek=False, throttle=default,
video_prefetch_mb=0, read_budget_mb=384.
"""
import argparse
import dataclasses
from dataclasses import dataclass, field


@dataclass
class IngestConfig:
    roots: list = field(default_factory=list)
    out_dir: str = "roam_dataset"
    tag: str = "run"
    # storage and I/O (ADR-005)
    storage: str = "ssd"                 # 'ssd' | 'hdd'
    readers: int = 0                     # 0 = auto (hdd: 1, ssd: 4)
    read_budget_mb: int | None = None    # default: 768 with video prefetch, else 384
    video_prefetch_mb: int | None = None  # default: 256 on hdd, else 0
    cold: bool = False                   # evict files from the OS cache first (benchmarks)
    # photos
    photo_decode: str = "draft"          # 'draft' (reduced-size JPEG decode) | 'full'
    photo_workers: int = 12
    raw_decode: str = "demosaic"         # RAW: "demosaic" (half-size LibRaw, default) | "preview" (embedded JPEG, opt-in)
    # videos (ADR-003, ADR-006)
    video_cpu: int = 2
    video_nvdec: int = 2
    nvdec_pct: int = 40
    route: str = "codec"                 # 'codec' | 'hash'
    route_key: str = "path"              # 'path' | 'content': what the H.264 NVDEC/CPU split hashes (content: every
                                         # byte-identical copy gets the same decoder, so identical embeddings)
    dedup: str = "skip"                  # 'off' | 'skip': link byte-identical copies of a committed asset without
                                         # reading the rest of the file or decoding it (output identical to 'off')
    read_order: str = "auto"             # 'auto' | 'path' | 'disk': physical on-disk order (NTFS extents); auto =
                                         # disk when storage is 'hdd', else path. Output is identical either way.
    nvdec_seek: bool = True
    video_strategy: str = "smart"        # 'smart' | 'seq' | 'seek' | 'auto'
    video_sampling: str = "exact"        # 'exact' | 'keyframe' | 'adaptive'
    adaptive_tau: float = 1.0
    video_threads: int = 0
    video_rotate: bool = True            # apply display rotation to frames
    video_thumbs: bool = True            # thumbnail from the first sampled frame
    max_frames: int = 32
    max_decode_frames: int = 20000
    video_timeout: float = 300.0         # safety net only (non-deterministic)
    # scheduling and model
    schedule: str = "mixed"              # 'mixed' | 'phased'
    batch: int = 64
    model: str = "ViT-B-32/laion2b_s34b_b79k"   # OpenCLIP <arch>/<pretrained>; 224 px, CLIP mean/std only
    device: str = "auto"                 # 'auto' | 'cuda' | 'cpu'
    # probing
    probe: bool = True                   # EXIF / container / sidecar metadata
    sidecars: bool = True
    # process (ADR-007)
    throttle: str = "off"                # 'off' | 'default'
    priority: str = "normal"
    no_throttle: bool = False            # legacy shorthand: throttle off + priority above
    # limits and tests
    max_out_gb: float = 8.0
    min_free_gb: float = 15.0
    limit: int | None = None
    stride: int = 1
    kill_after_files: int = 0            # TEST: hard-exit after N committed files
    commit_every: int = 50               # files per manifest transaction (also committed every 2 s)
    report: str | None = None
    quiet: bool = False
    timeline: bool = True

    @classmethod
    def from_namespace(cls, ns):
        names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in vars(ns).items() if k in names})

    def public(self):
        """Config without source paths (safe for aggregate reports)."""
        d = dataclasses.asdict(self)
        for k in ("roots", "out_dir", "report"):
            d.pop(k, None)
        return d


def add_run_arguments(ap, roots=True):
    if roots:
        ap.add_argument("roots", nargs="+")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--tag", default="run")
    ap.add_argument("--storage", choices=["ssd", "hdd"], default="ssd")
    ap.add_argument("--readers", type=int, default=0, help="0 = auto (hdd: 1, ssd: 4)")
    ap.add_argument("--photo-decode", choices=["draft", "full"], default="draft",
                    help="draft = reduced-size JPEG decode (optimised); full = exact reference decode")
    ap.add_argument("--photo-workers", type=int, default=12)
    ap.add_argument("--raw-decode", choices=["demosaic", "preview"], default="demosaic",
                    help="RAW photos: half-size demosaic (default) or the embedded JPEG preview (faster, rendered differently)")
    ap.add_argument("--video-cpu", type=int, default=2)
    ap.add_argument("--video-nvdec", type=int, default=2)
    ap.add_argument("--nvdec-pct", type=int, default=40, help="share of H.264 videos routed to NVDEC (hash rule)")
    ap.add_argument("--schedule", choices=["mixed", "phased"], default="mixed")
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--model", default="ViT-B-32/laion2b_s34b_b79k",
                    help="OpenCLIP image model as <arch>/<pretrained> (224 px input, CLIP mean/std)")
    ap.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    ap.add_argument("--max-frames", type=int, default=32)
    ap.add_argument("--max-decode-frames", type=int, default=20000)
    ap.add_argument("--video-timeout", type=float, default=300.0, help="safety net only (non-deterministic)")
    ap.add_argument("--video-strategy", choices=["auto", "seq", "seek", "smart"], default="smart",
                    help="smart = exact frames like seq, but jumps over keyframe intervals that hold no target")
    ap.add_argument("--video-sampling", choices=["exact", "keyframe", "adaptive"], default="exact",
                    help="exact = frame at/after each target time; keyframe = the keyframe at/before it (lossy); "
                         "adaptive = keyframe only if within --adaptive-tau s of the target, else exact")
    ap.add_argument("--adaptive-tau", type=float, default=1.0)
    ap.add_argument("--video-rotate", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--video-thumbs", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--probe", action=argparse.BooleanOptionalAction, default=True,
                    help="read capture time / GPS from EXIF, container tags and sidecars")
    ap.add_argument("--sidecars", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--throttle", choices=["default", "off"], default="off",
                    help="off = opt this process out of Windows power throttling (EcoQoS)")
    ap.add_argument("--priority", choices=["normal", "above", "high"], default="normal")
    ap.add_argument("--route", choices=["hash", "codec"], default="codec",
                    help="hash = --nvdec-pct of videos to NVDEC; codec = HEVC to NVDEC, H.264 by hash, others CPU")
    ap.add_argument("--nvdec-seek", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--route-key", choices=["path", "content"], default="path",
                    help="hash the file path or its content ID for the H.264 NVDEC/CPU split")
    ap.add_argument("--dedup", choices=["off", "skip"], default="skip",
                    help="skip = when a file's size and head/tail content ID match an asset already committed, "
                         "link it without reading the whole file or decoding it; output is identical to off")
    ap.add_argument("--read-order", choices=["auto", "path", "disk"], default="auto",
                    help="disk = read files in physical on-disk order (fewer seeks on hard drives); auto = disk on hdd")
    ap.add_argument("--video-threads", type=int, default=0, help="FFmpeg threads per CPU video decoder (0 = auto)")
    ap.add_argument("--no-throttle", action="store_true", help="legacy: --throttle off --priority above")
    ap.add_argument("--read-budget-mb", type=int, default=None, help="default: 768 with prefetch, else 384")
    ap.add_argument("--video-prefetch-mb", type=int, default=None,
                    help="videos up to this size are read whole by the reader (default 256 on hdd, else 0)")
    ap.add_argument("--max-out-gb", type=float, default=8.0)
    ap.add_argument("--min-free-gb", type=float, default=15.0)
    ap.add_argument("--cold", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--kill-after-files", type=int, default=0, help="TEST: hard-exit after N committed files")
    ap.add_argument("--commit-every", type=int, default=50, help="files per manifest transaction")
    ap.add_argument("--report")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-timeline", dest="timeline", action="store_false")
    return ap
