"""RoamIngest: local, resumable ingestion of photo/video archives into manifest v1 (shared/schemas/manifest_v1.sql).

    from roamingest import Ingestor, IngestConfig
    report = Ingestor(IngestConfig(roots=["E:/archive"], out_dir="D:/roam_dataset", storage="hdd")).run()
"""
__version__ = "0.1.1"


def __getattr__(name):                     # lazy: importing the package does not load torch
    if name == "Ingestor":
        from .pipeline import Ingestor
        return Ingestor
    if name == "IngestConfig":
        from .config import IngestConfig
        return IngestConfig
    raise AttributeError(name)


__all__ = ["Ingestor", "IngestConfig", "__version__"]
