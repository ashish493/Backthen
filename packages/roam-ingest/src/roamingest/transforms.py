"""Image transforms shared by every path: decode, thumbnail, model input.

These are the reference definitions (moved here from bench/stages.py, which re-exports them).
The reduced-size ("draft") JPEG path resamples exactly the same source region as the reference path,
so thumbnails and model inputs agree with a full decode (PSNR >= 40 dB gate, results/EXP_IMAGES.md).
"""
import numpy as np
from PIL import Image, ImageOps

THUMB_LONG_EDGE = 512        # thumbnail size
MODEL_SIZE = 224             # model input side
FRAME_EVERY_S = 2.0          # video sampling policy: 1 frame / 2 s ...
MAX_FRAMES = 32              # ... capped per clip (bounds memory/time)

_ROTATED = {5, 6, 7, 8}      # EXIF orientations that swap width/height


def decode_image(src):
    """Full decode with EXIF orientation applied -> PIL RGB image. `src` may be a path or file-like.
    Multi-frame formats (GIF, animated WebP) give their first frame."""
    with Image.open(src) as im:
        im.load()
        return ImageOps.exif_transpose(im).convert("RGB")


def decode_image_draft(src, margin=1):
    """Reduced-size JPEG decode (libjpeg DCT scaling 1/2, 1/4 or 1/8).

    Picks the largest reduction that keeps long edge >= margin*THUMB_LONG_EDGE and short edge >=
    margin*MODEL_SIZE. Non-JPEG formats ignore draft() and decode at full size.
    Returns (PIL RGB image with EXIF orientation applied, oriented size of the ORIGINAL image, scale s).
    Pass orig_size and scale to make_thumbnail / to_model_input so they resample exactly the same
    source region as the reference path (draft pixel i covers original pixels [i*s, (i+1)*s)).
    """
    with Image.open(src) as im:
        w, h = im.size
        orient = im.getexif().get(274)
        s = 1
        while (s < 8 and max(w, h) // (s * 2) >= margin * THUMB_LONG_EDGE
               and min(w, h) // (s * 2) >= margin * MODEL_SIZE):
            s *= 2
        if s > 1:
            im.draft("RGB", (w // s, h // s))
        im.load()
        out = ImageOps.exif_transpose(im).convert("RGB")
    orig = (h, w) if orient in _ROTATED else (w, h)
    return out, orig, s


def make_thumbnail(img, orig_size=None, scale=1, reducing_gap=None):
    """Thumbnail with long edge THUMB_LONG_EDGE (never upscaled).

    orig_size/scale: img is a 1/scale reduced decode of an image whose oriented size is orig_size;
    output size and source region then match the reference exactly.
    """
    w, h = orig_size or img.size
    r = THUMB_LONG_EDGE / max(w, h)
    if r >= 1:
        return img.copy()
    box = (0, 0, w / scale, h / scale) if scale > 1 else None
    return img.resize((max(1, round(w * r)), max(1, round(h * r))), Image.BILINEAR,
                      box=box, reducing_gap=reducing_gap)


_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
MEAN, STD = tuple(_MEAN.tolist()), tuple(_STD.tolist())
PREPROCESS = "short side 224 bicubic, centre crop 224, CLIP mean/std"


def _normalise(img224):
    a = np.asarray(img224, dtype=np.float32) / 255.0
    return ((a - _MEAN) / _STD).transpose(2, 0, 1).copy()


def to_model_input(img, orig_size=None, scale=1, reducing_gap=None):
    """Resize short side to MODEL_SIZE, centre crop, normalise -> float32 CHW.

    With orig_size/scale (reduced decode), the same centre-crop region is computed in original
    coordinates and resampled directly from img via `box`.
    """
    if orig_size is None:
        w, h = img.size
        s = MODEL_SIZE / min(w, h)
        img = img.resize((max(MODEL_SIZE, round(w * s)), max(MODEL_SIZE, round(h * s))), Image.BICUBIC,
                         reducing_gap=reducing_gap)
        w, h = img.size
        l, t = (w - MODEL_SIZE) // 2, (h - MODEL_SIZE) // 2
        return _normalise(img.crop((l, t, l + MODEL_SIZE, t + MODEL_SIZE)))
    W, H = orig_size
    s = MODEL_SIZE / min(W, H)
    W2, H2 = max(MODEL_SIZE, round(W * s)), max(MODEL_SIZE, round(H * s))
    l, t = (W2 - MODEL_SIZE) // 2, (H2 - MODEL_SIZE) // 2
    sx, sy = W2 / W, H2 / H
    box = (l / sx / scale, t / sy / scale, (l + MODEL_SIZE) / sx / scale, (t + MODEL_SIZE) / sy / scale)
    return _normalise(img.resize((MODEL_SIZE, MODEL_SIZE), Image.BICUBIC, box=box))
