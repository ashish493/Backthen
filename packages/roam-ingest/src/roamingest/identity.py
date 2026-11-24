"""Content identity.

asset_id = partial content ID: xxh3-128 of (size, first 64 KiB, last 64 KiB). It is cheap (two small reads) and
does not change when a file is moved or renamed. content_hash_full = xxh3-128 of all bytes; computed for free when
the whole file is already in memory (photos, prefetched videos), otherwise by `roamingest hash` later.
The full hash detects the (rare) case of two different files sharing a partial ID.
"""
import xxhash

HEADTAIL = 1 << 16


def cid_from(size, head, tail):
    h = xxhash.xxh3_128()
    h.update(str(size).encode())
    h.update(head)
    h.update(tail)
    return h.hexdigest()


def cid_from_bytes(data):
    return cid_from(len(data), data[:HEADTAIL], data[-HEADTAIL:] if len(data) > HEADTAIL else b"")


def cid_from_file(path, size):
    with open(path, "rb") as f:
        head = f.read(HEADTAIL)
        tail = b""
        if size > HEADTAIL:
            f.seek(size - HEADTAIL)
            tail = f.read(HEADTAIL)
    return cid_from(size, head, tail)


def full_hash_bytes(data):
    return xxhash.xxh3_128_hexdigest(data)


def full_hash_file(path, bufsize=8 << 20):
    h = xxhash.xxh3_128()
    with open(path, "rb") as f:
        while b := f.read(bufsize):
            h.update(b)
    return h.hexdigest()
