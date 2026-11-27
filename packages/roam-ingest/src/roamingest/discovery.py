"""Discovery: walk sources, classify files, match Takeout-style JSON sidecars by name (no file is opened).

Sidecar name rules (checked on the 355 GB archive: they match 23,446 of the 23,468 media files that have a sidecar;
reading each sidecar's `title` field would add 2 more, results/EXP_INVENTORY.md):
  1. <name.ext>.json
  2. <name.ext>.<anything>.json          (.supplemental-metadata.json and its truncated forms)
  3. <stem>(n).<ext>  ->  <stem>.<ext>(n).json  or  <stem>.<ext>.<anything>(n).json   (Takeout duplicates)
  4. <stem>-edited.<ext> -> the original's sidecar
  5. truncated names: a sidecar whose name without .json (at least 20 characters) is a prefix of the media name
"""
import bisect
import collections
import os
import re

import xxhash

from .images import RAW_EXT

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif"} | set(RAW_EXT)
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".3gp"}
SIDECAR_EXT = {".json", ".xmp", ".aae"}
_DUP = re.compile(r"^(.*)\((\d+)\)$")


def classify(ext):
    if ext in PHOTO_EXT:
        return "photo"
    if ext in VIDEO_EXT:
        return "video"
    if ext in SIDECAR_EXT:
        return "sidecar"
    return None


def sidecar_index(jsons):
    """Built once per directory: sorted names for prefix lookups, plus directory order to break ties as before."""
    return sorted(jsons), {j: i for i, j in enumerate(jsons)}


def _with_prefix(keys, p):
    i, out = bisect.bisect_left(keys, p), []
    while i < len(keys) and keys[i].startswith(p):
        out.append(keys[i])
        i += 1
    return out


def match_sidecar(name, jsons, index=None):
    """name: media file name; jsons: {lower-case json name: real name} in the same directory -> json name or None.
    index: sidecar_index(jsons), so a directory of n media and m sidecars costs O(n log m), not O(n m)."""
    keys, order = index or sidecar_index(jsons)
    first = lambda js: min(js, key=lambda j: (len(j), order[j]))    # noqa: E731  (shortest, then directory order)
    ml = name.lower()
    if ml + ".json" in jsons:
        return jsons[ml + ".json"]
    pref = _with_prefix(keys, ml + ".")
    if pref:
        return jsons[first(pref)]
    stem, ext = os.path.splitext(ml)
    m = _DUP.match(stem)
    if m:
        base, n = m.group(1) + ext, m.group(2)
        if f"{base}({n}).json" in jsons:
            return jsons[f"{base}({n}).json"]
        cand = [j for j in _with_prefix(keys, base + ".") if j.endswith(f"({n}).json")]
        if cand:
            return jsons[first(cand)]
    if stem.endswith("-edited"):
        return match_sidecar(stem[:-7] + ext, jsons, (keys, order))
    for k in range(len(ml), 19, -1):                      # rule 5: the longest name prefix that is a sidecar
        j = ml[:k] + ".json"
        if j in jsons and not _DUP.match(ml[:k]):
            return jsons[j]
    return None


def discover(roots, stride=1, limit=None, sidecars=True):
    """-> (items, counts). items are sorted by path within each root, roots in the given order.
    Each item: path, root_index, rel, size, mtime_ns, kind, ext, route (0-99, deterministic), sidecar_rel.
    counts: other_ext (Counter of unsupported extensions), sidecars (files), unreadable (dirs/files)."""
    items, other, n_sidecar, unreadable = [], collections.Counter(), 0, 0
    for ri, root in enumerate(roots):
        found, stack = [], [root]
        while stack:
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    entries = list(it)
            except OSError:
                unreadable += 1
                continue
            jsons, media = {}, []
            for e in entries:
                try:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                        continue
                    ext = os.path.splitext(e.name)[1].lower()
                    kind = classify(ext)
                    if kind is None:
                        other[ext or "(none)"] += 1
                    elif kind == "sidecar":
                        n_sidecar += 1
                        if ext == ".json":
                            jsons[e.name.lower()] = e.name
                    else:
                        media.append((e, ext, kind))
                except OSError:
                    unreadable += 1
            index = sidecar_index(jsons) if (sidecars and jsons) else None
            for e, ext, kind in media:
                try:
                    st = e.stat()
                except OSError:
                    unreadable += 1
                    continue
                rel = os.path.relpath(e.path, root)
                sc = match_sidecar(e.name, jsons, index) if index else None
                found.append(dict(path=e.path, root_index=ri, rel=rel, size=st.st_size, mtime_ns=st.st_mtime_ns,
                                  kind=kind, ext=ext,
                                  route=xxhash.xxh3_64_intdigest(f"{ri}:{rel}:{st.st_size}".encode()) % 100,
                                  sidecar_rel=os.path.join(os.path.dirname(rel), sc) if sc else None))
        found.sort(key=lambda x: x["path"])
        items.extend(found)
    items = items[::stride][:limit]
    return items, dict(other_ext=other, sidecars=n_sidecar, unreadable=unreadable)
