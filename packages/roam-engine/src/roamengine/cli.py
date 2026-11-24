"""roamengine CLI: serve | search | stats | eval | bench."""
import argparse
import json
import statistics
import sys
import time


def _engine(a):
    from .engine import Engine
    return Engine(a.library, text_device=a.text_device)


def cmd_search(a):
    from .engine import SearchFilters
    e = _engine(a)
    f = SearchFilters(media_type=a.type, date_from=a.date_from, date_to=a.date_to)
    res = e.search(a.query, f, k=a.k)
    for r, it in enumerate(res["items"], 1):
        line = f"{r:3d}  {it['score']:.4f}  {it['media_type']:5s}  {it['capture_time'] or '-':20s}"
        if it.get("moments"):
            line += "  @" + ",".join(f"{m['timestamp_s']:.1f}s" for m in it["moments"][:3] if m["timestamp_s"] is not None)
        if a.paths:
            line += "  " + (e.cat.primary_path(it["asset_id"]) or "(missing)")
        else:
            line += "  " + it["asset_id"][:12]
        print(line)
    print(f"-- {res['total']} candidates, encode {res.get('timing_ms', {}).get('encode')} ms, "
          f"rank {res.get('timing_ms', {}).get('rank')} ms", file=sys.stderr)


def cmd_stats(a):
    print(json.dumps(_engine(a).cat.stats(), indent=1, default=str))


def cmd_eval(a):
    from .evaluation import Labels, evaluate
    e = _engine(a)
    rep = evaluate(e, Labels(a.library))
    if not a.per_query:
        rep.pop("per_query")
    print(json.dumps(rep, indent=1))


def cmd_bench(a):
    """Query latency on the library (aggregate timings only; query texts are generic)."""
    e = _engine(a)
    t0 = time.perf_counter()
    e.text.encode(["warm up"])
    load = time.perf_counter() - t0
    qs = ["a beach at sunset", "people smiling", "a city street at night", "mountains with fog", "food on a table",
          "a dog", "a temple", "a train", "a waterfall", "a birthday cake", "snow", "a boat on a lake"]
    enc, rank = [], []
    for _ in range(a.repeats):
        for q in qs:
            e.text._cache.clear()
            r = e.search(q, k=50)
            enc.append(r["timing_ms"]["encode"])
            rank.append(r["timing_ms"]["rank"])
    tot = sorted(x + y for x, y in zip(enc, rank))
    p = lambda xs, q: sorted(xs)[min(len(xs) - 1, int(q * len(xs)))]  # noqa: E731
    print(json.dumps(dict(assets=len(e.cat.asset_ids), frames=len(e.cat.f_asset), text_device=a.text_device,
                          text_model_load_s=round(load, 2), queries=len(tot),
                          encode_ms_p50=statistics.median(enc), rank_ms_p50=statistics.median(rank),
                          total_ms_p50=p(tot, .5), total_ms_p95=p(tot, .95), total_ms_max=tot[-1]), indent=1))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "serve":
        from .server import main as serve_main
        return serve_main(argv[1:])
    ap = argparse.ArgumentParser(prog="roamengine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the local API (see `roamengine serve --help`)")

    def lib(p):
        p.add_argument("--library", required=True, help="RoamIngest --out-dir (holds manifest.sqlite)")
        p.add_argument("--text-device", default="cpu")
        return p
    s = lib(sub.add_parser("search"))
    s.add_argument("query")
    s.add_argument("-k", type=int, default=20)
    s.add_argument("--type", choices=["photo", "video"])
    s.add_argument("--from", dest="date_from")
    s.add_argument("--to", dest="date_to")
    s.add_argument("--paths", action="store_true", help="print file paths (local terminal only)")
    lib(sub.add_parser("stats"))
    ev = lib(sub.add_parser("eval"))
    ev.add_argument("--per-query", action="store_true")
    b = lib(sub.add_parser("bench"))
    b.add_argument("--repeats", type=int, default=3)
    a = ap.parse_args(argv)
    dict(search=cmd_search, stats=cmd_stats, eval=cmd_eval, bench=cmd_bench)[a.cmd](a)


if __name__ == "__main__":
    main()
