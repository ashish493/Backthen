"""Run `roamengine serve` as if today were another date, so date-driven screens (On this day, this month in earlier
years) have something to show in the small CC0 demo library. README screenshots only.

usage: python bench/demo/serve_on_date.py 2026-11-17 --library <demo library> --port 8766 --token demo --memories-solo-ok
"""
import datetime as dt
import sys
import types

from roamengine import memories, nostalgia, server

DAY = dt.date.fromisoformat(sys.argv[1])


class _Date(dt.date):
    @classmethod
    def today(cls):
        return DAY


shim = types.SimpleNamespace(**{k: getattr(dt, k) for k in dir(dt) if not k.startswith("__")})
shim.date = _Date
for mod in (server, memories, nostalgia):
    mod.dt = shim

server.main(sys.argv[2:])
