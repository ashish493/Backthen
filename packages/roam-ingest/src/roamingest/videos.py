"""Video sampling and decoding (results/EXP_B.md, EXP_SAMPLING.md, ADR-006).

Targets: one frame every FRAME_EVERY_S seconds, evenly spread, at most max_frames per video.
Strategies (all exact except keyframe/adaptive):
  seq      decode from the start, keep the first frame at/after each target time
  seek     per target: seek to the preceding keyframe, decode forward to the first frame >= target
  smart    seq's frames with the least decoding: jump only over keyframe intervals that hold no target (default)
  keyframe per target: the keyframe at/before it (lossy, opt-in); duplicate keyframes are emitted once
  adaptive smart, but uses the keyframe when it lies within tau seconds of a target that would need a jump
Any strategy runs on the CPU or NVDEC (H.264/HEVC). Display rotation is read once from the stream and applied
to every picked frame, so CPU and NVDEC give the same orientation.
"""
import io
import math
import struct
import time

import av
from av.codec.context import CodecContext
from PIL import Image

from . import transforms as T

NVDEC_CODECS = ("h264", "hevc")
_ROT = {90: Image.Transpose.ROTATE_90, -90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180,
        -180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_270, -270: Image.Transpose.ROTATE_90}


def sample_targets(dur, max_frames):
    n = min(max_frames, max(1, int(dur // T.FRAME_EVERY_S) + 1))
    return [round(i * dur / n, 3) for i in range(n)]


def stream_rotation(stream):
    """Display rotation in degrees (FFmpeg convention, counter-clockwise; e.g. -90 for most portrait phone clips),
    rounded to a multiple of 90. 0 when the stream has no display matrix."""
    try:
        m = stream.codec_context.coded_side_data.get("display_matrix")
    except Exception:  # noqa: BLE001
        m = None
    if not m or len(m) < 36:
        return 0
    v = struct.unpack("<9i", m[:36])
    a, b, c, d = (x / 65536.0 for x in (v[0], v[1], v[3], v[4]))
    s0, s1 = math.hypot(a, c), math.hypot(b, d)
    if not s0 or not s1:
        return 0
    r = int(round(-math.degrees(math.atan2(b / s1, a / s0)) / 90.0)) * 90
    r = (r + 180) % 360 - 180                       # -> one of -180, -90, 0, 90
    return 180 if r == -180 else r


def probe_codec(src):
    """Video codec name of a file (path or file-like), or None. Reads only the container header/index."""
    try:
        with av.open(src) as c:
            return c.streams.video[0].codec_context.name if c.streams.video else None
    except Exception:  # noqa: BLE001
        return None


def container_meta(c):
    """Container tags useful for probing (creation time, location). Values stay local."""
    out = {}
    md = dict(c.metadata or {})
    try:
        md.update({k: v for k, v in (c.streams.video[0].metadata or {}).items() if k not in md})
    except Exception:  # noqa: BLE001
        pass
    if md.get("creation_time"):
        out["creation_time"] = md["creation_time"]
    for k in ("com.apple.quicktime.location.ISO6709", "location", "location-eng"):
        if md.get(k):
            out["location"] = md[k]
            break
    return out


def video_emits(path, use_nvdec, a, info, want_thumb=False):
    """Decode one video -> list of (idx, pts, model_input). Fills `info`:
    codec, width, height, dur_s, fps, rotation, strategy, decoder, frames_decoded, seeks, probes,
    methods {idx: 'exact'|'keyframe'}, requested {idx: target seconds}, meta (container tags),
    thumb (PIL image of the first picked frame, if want_thumb), t_decode_ms, t_xform_ms.
    `a` is any object with the IngestConfig sampling attributes."""
    t_start = time.perf_counter()
    deadline = t_start + a.video_timeout
    emits, t_xf = [], 0.0
    c = av.open(path)
    try:
        s = c.streams.video[0]
        codec = s.codec_context.name
        tb = float(s.time_base)
        start = float(s.start_time * tb) if s.start_time is not None else 0.0
        dur = float(c.duration / av.time_base) if c.duration else (float(s.duration * tb) if s.duration else None)
        fps = float(s.average_rate) if s.average_rate else 0.0
        rotation = stream_rotation(s) if getattr(a, "video_rotate", True) else 0
        transpose = _ROT.get(rotation)
        info.update(codec=codec, width=s.width, height=s.height, dur_s=dur, fps=fps or None, rotation=rotation,
                    meta=container_meta(c))
        tgts = sample_targets(dur, a.max_frames) if dur else None
        total = dur * fps if dur and fps else None
        if tgts is not None and a.video_sampling == "keyframe":
            strategy = "keyframe"
        elif tgts is not None and a.video_sampling == "adaptive":
            strategy = "adaptive"
        elif tgts is not None and a.video_strategy == "smart":
            strategy = "smart"
        elif tgts is not None and (a.video_strategy == "seek" or (a.video_strategy == "auto" and total is not None
                                                                  and total > 250 * len(tgts))):
            strategy = "seek"
        else:
            strategy = "seq"
        info["strategy"] = strategy
        nv = use_nvdec and codec in NVDEC_CODECS and (a.nvdec_seek or strategy == "seq")
        info["decoder"] = "nvdec" if nv else "cpu"
        decoded = 0

        if nv:
            ctx = CodecContext.create(codec + "_cuvid", "r")
            ctx.extradata = s.codec_context.extradata
        else:
            ctx = s.codec_context
            # frame threading delays the first output frame; a single-frame-per-seek strategy wants slice threads
            ctx.thread_type = "SLICE" if strategy == "keyframe" else "AUTO"
            if a.video_threads:
                ctx.thread_count = a.video_threads

        def frames_from_here():
            for pkt in c.demux(s):
                yield from ctx.decode(pkt)
            try:
                yield from ctx.decode(None)             # drain (no-op if the demuxer's flush packet already did)
            except EOFError:
                pass

        methods = info.setdefault("methods", {})
        requested = info.setdefault("requested", {})

        def emit(fr, t, idx, method="exact", req=None):
            nonlocal t_xf
            x = time.perf_counter()
            img = fr.to_image()
            if transpose is not None:
                img = img.transpose(transpose)
            if want_thumb and "thumb" not in info:
                info["thumb"] = T.make_thumbnail(img)
            emits.append((idx, round(t, 6), T.to_model_input(img)))
            methods[idx] = method
            requested[idx] = req if req is not None else (tgts[idx] if tgts is not None else round(t, 6))
            t_xf += time.perf_counter() - x

        def check():
            if decoded > a.max_decode_frames:
                info["status"] = "truncated"
                return True
            if time.perf_counter() > deadline:
                info["status"] = "timeout"
                return True
            return False

        if strategy in ("smart", "adaptive"):
            # A second, decode-free handle finds the keyframe at/before each target; the decoder jumps there only
            # if it lies beyond where it already is. Dense targets therefore fall back to continued decoding.
            src2 = io.BytesIO(path.getbuffer()) if isinstance(path, io.BytesIO) else path
            pc = av.open(src2)
            try:
                ps = pc.streams.video[0]

                def keyframe_before(tg):
                    pc.seek(int((start + tg) / tb), stream=ps, backward=True, any_frame=False)
                    for pkt in pc.demux(ps):
                        if pkt.pts is not None:
                            return pkt.pts * tb - start
                    return None

                gen, cur_t, last_fr, last_t, seeks, probes = None, float("-inf"), None, None, 0, 0
                for idx, tg in enumerate(tgts):
                    if last_t is not None and last_t >= tg - 1e-9:     # current frame also satisfies this target
                        emit(last_fr, last_t, idx)
                        continue
                    k = keyframe_before(tg)
                    probes += 1
                    need_jump = gen is None or (k is not None and k > cur_t + 1e-9)
                    if need_jump:
                        c.seek(int((start + tg) / tb), stream=s, backward=True, any_frame=False)
                        ctx.flush_buffers()
                        gen, seeks = frames_from_here(), seeks + 1
                    if strategy == "adaptive" and need_jump and k is not None and tg - k <= a.adaptive_tau + 1e-9:
                        for fr in gen:                             # first frame after the jump = the keyframe
                            decoded += 1
                            if fr.pts is None:
                                continue
                            cur_t = fr.pts * tb - start
                            emit(fr, cur_t, idx, "keyframe")
                            break
                        continue
                    for fr in gen:
                        decoded += 1
                        t = fr.pts * tb - start if fr.pts is not None else None
                        if t is None:
                            continue
                        cur_t = t
                        if t >= tg - 1e-9:
                            emit(fr, t, idx)
                            last_fr, last_t = fr, t
                            break
                        if check():
                            break
                    if info.get("status") in ("truncated", "timeout"):
                        break
                info["seeks"], info["probes"] = seeks, probes
            finally:
                pc.close()
        elif strategy in ("seek", "keyframe"):
            last_t = None
            info["seeks"] = len(tgts)
            for idx, tg in enumerate(tgts):
                c.seek(int((start + tg) / tb), stream=s, backward=True, any_frame=False)
                ctx.flush_buffers()
                for fr in frames_from_here():
                    decoded += 1
                    t = fr.pts * tb - start if fr.pts is not None else None
                    if t is None:
                        continue
                    if strategy == "keyframe":
                        if last_t is None or abs(t - last_t) > 1e-9:   # same keyframe as previous target: skip
                            emit(fr, t, idx, "keyframe")
                            last_t = t
                        break
                    if t >= tg - 1e-9:
                        emit(fr, t, idx)
                        break
                    if check():
                        break
                if info.get("status") in ("truncated", "timeout"):
                    break
        else:
            it = frames_from_here()
            ti, next_t = 0, 0.0
            for fr in it:
                decoded += 1
                t = fr.pts * tb - start if fr.pts is not None else None
                if t is not None:
                    if tgts is not None:
                        while ti < len(tgts) and t >= tgts[ti] - 1e-9:
                            emit(fr, t, ti)
                            ti += 1
                        if ti >= len(tgts):
                            break
                    elif t >= next_t:                       # unknown duration: every FRAME_EVERY_S seconds
                        emit(fr, t, ti, req=round(next_t, 6))
                        ti += 1
                        next_t += T.FRAME_EVERY_S
                        if ti >= a.max_frames:
                            break
                if check():
                    break
        info["frames_decoded"] = decoded
    finally:
        c.close()
    info["t_xform_ms"] = t_xf * 1000
    info["t_decode_ms"] = (time.perf_counter() - t_start - t_xf) * 1000
    return emits
