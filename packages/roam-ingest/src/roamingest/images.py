"""Photo decoding from in-memory bytes.

JPEG: reduced-size draft decode (results/EXP_IMAGES.md). HEIC/PNG/WebP/GIF: full decode (GIF: first frame).
NEF RAW: LibRaw half-size demosaic by default. Opt-in: the camera's embedded JPEG preview, decoded with the same
draft path (43x faster; fails the ADR-002 search gate against a full demosaic, results/raw_preview.json).
"""
import io

from PIL import Image

from . import transforms as T

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:  # noqa: BLE001
    pass

JPEG_EXT = (".jpg", ".jpeg")
RAW_EXT = (".nef",)
# LibRaw flip -> PIL transpose (0 = none)
_FLIP = {3: Image.Transpose.ROTATE_180, 5: Image.Transpose.ROTATE_90, 6: Image.Transpose.ROTATE_270}


def photo_strategy(ext, mode="draft", raw_decode="demosaic"):
    if ext in RAW_EXT:
        return "raw_preview" if raw_decode == "preview" else "raw_demosaic_half"
    return "draft" if (mode == "draft" and ext in JPEG_EXT) else "full"


def decode_photo(data, ext, mode="draft", raw_decode="demosaic"):
    """-> (PIL RGB image, oriented original size or None, draft scale, strategy).
    RAW: raw_decode='demosaic' (default) = LibRaw half-size demosaic, which matches a full demosaic in search
    (top-10 neighbour overlap 0.98, results/raw_preview.json); 'preview' = the camera's embedded JPEG, 43x faster
    but rendered differently (overlap 0.868: fails the ADR-002 gate), opt-in."""
    if ext in RAW_EXT:
        if raw_decode == "demosaic":
            return _demosaic_half(data)
        return _decode_raw(data, mode)
    bio = io.BytesIO(data)
    if mode == "draft" and ext in JPEG_EXT:
        im, orig, s = T.decode_image_draft(bio, margin=2)
        return im, orig, s, "draft"
    return T.decode_image(bio), None, 1, "full"


def _demosaic_half(data):
    import rawpy
    with rawpy.imread(io.BytesIO(data)) as raw:
        rgb = raw.postprocess(half_size=True, use_camera_wb=True, output_bps=8)   # LibRaw applies the flip
    return Image.fromarray(rgb), None, 1, "raw_demosaic_half"


def _decode_raw(data, mode):
    import rawpy
    with rawpy.imread(io.BytesIO(data)) as raw:
        flip = raw.sizes.flip
        try:
            th = raw.extract_thumb()
        except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
            th = None
        if th is None:
            rgb = raw.postprocess(half_size=True, use_camera_wb=True, output_bps=8)   # already oriented by LibRaw
            return Image.fromarray(rgb), None, 1, "raw_demosaic"
        if th.format == rawpy.ThumbFormat.BITMAP:
            im = Image.fromarray(th.data)
            return (im.transpose(_FLIP[flip]) if flip in _FLIP else im), None, 1, "raw_preview"
        jpeg = bytes(th.data)
    with Image.open(io.BytesIO(jpeg)) as probe:
        has_orient = probe.getexif().get(274, 1) != 1
    if mode == "draft":
        im, orig, s = T.decode_image_draft(io.BytesIO(jpeg), margin=2)
    else:
        im, orig, s = T.decode_image(io.BytesIO(jpeg)), None, 1
    if not has_orient and flip in _FLIP:                  # preview carries no orientation: use the RAW's
        im = im.transpose(_FLIP[flip])
        if orig is not None and flip in (5, 6):
            orig = (orig[1], orig[0])
    return im, orig, s, "raw_preview"


def model_input_and_thumb(im, orig, scale):
    if orig:
        return T.make_thumbnail(im, orig, scale), T.to_model_input(im, orig, scale)
    return T.make_thumbnail(im), T.to_model_input(im)
