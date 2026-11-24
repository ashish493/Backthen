"""Display renditions for the desktop app. Originals are only ever opened read-only.

- preview_jpeg: any supported photo (JPEG, PNG, WebP, HEIC, GIF, NEF) -> upright JPEG, long edge <= max_edge.
- frame_jpeg: one video frame at a timestamp (for moments, and for codecs the webview cannot play), upright.
"""
import collections
import io
import os
import threading

from PIL import Image, ImageOps

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:  # noqa: BLE001
    pass

Image.MAX_IMAGE_PIXELS = 400_000_000          # local, user-owned files; large panoramas must still open

VIDEO_MIME = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/mp4", ".webm": "video/webm",
              ".mkv": "video/webm", ".3gp": "video/3gpp", ".avi": "video/x-msvideo"}
PHOTO_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
              ".gif": "image/gif"}


class LRU:
    def __init__(self, max_bytes=256 << 20):
        self.max, self.n, self.d, self.lock = max_bytes, 0, collections.OrderedDict(), threading.Lock()

    def get(self, k):
        with self.lock:
            v = self.d.get(k)
            if v is not None:
                self.d.move_to_end(k)
            return v

    def put(self, k, v):
        with self.lock:
            if k in self.d:
                return
            self.d[k] = v
            self.n += len(v)
            while self.n > self.max and self.d:
                _, old = self.d.popitem(last=False)
                self.n -= len(old)


_cache = LRU()


def _jpeg(im, quality=88):
    bio = io.BytesIO()
    im.convert("RGB").save(bio, "JPEG", quality=quality)
    return bio.getvalue()


def preview_jpeg(path, max_edge=2048):
    key = ("p", path, max_edge)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    ext = os.path.splitext(path)[1].lower()
    if ext == ".nef":
        import rawpy
        with rawpy.imread(path) as raw:
            im = Image.fromarray(raw.postprocess(half_size=True, use_camera_wb=True, output_bps=8))
    else:
        with Image.open(path) as src:
            if src.format == "JPEG":
                src.draft("RGB", (max_edge, max_edge))
            src.load()
            im = ImageOps.exif_transpose(src).convert("RGB")
    im.thumbnail((max_edge, max_edge), Image.LANCZOS)
    out = _jpeg(im)
    _cache.put(key, out)
    return out


def frame_jpeg(path, t, max_edge=1280):
    key = ("f", path, round(float(t), 2), max_edge)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    import av
    from roamingest.videos import _ROT, stream_rotation
    with av.open(path) as c:
        s = c.streams.video[0]
        s.thread_type = "AUTO"
        rot = stream_rotation(s)
        tb = float(s.time_base) if s.time_base else 1 / 90000
        start = float(s.start_time * s.time_base) if s.start_time is not None else 0.0
        c.seek(int((start + max(0.0, float(t))) / tb), stream=s, backward=True, any_frame=False)
        img = None
        for fr in c.decode(s):
            img = fr
            if fr.time is not None and fr.time >= start + float(t) - 1e-3:
                break
        if img is None:
            raise ValueError("no frame decoded")
        im = img.to_image()
    if rot in _ROT:                                # same convention as the ingest (frames are stored upright)
        im = im.transpose(_ROT[rot])
    im.thumbnail((max_edge, max_edge), Image.BILINEAR)
    out = _jpeg(im, 85)
    _cache.put(key, out)
    return out
