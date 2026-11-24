"""RoamEngine: search over a RoamIngest manifest v1 library. Everything runs locally."""
__version__ = "0.1.0"


def __getattr__(name):
    if name in ("Engine", "SearchFilters"):
        from . import engine
        return getattr(engine, name)
    if name == "Catalog":
        from .catalog import Catalog
        return Catalog
    raise AttributeError(name)
