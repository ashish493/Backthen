"""Hypothesis D (and E data): does GPU->CPU transfer cancel the benefit of hardware decode?

Usage: python -m bench.exp_d_nvdec <dataset_root> [--repeats 3] [--stride 1] [--out results/exp_d.jsonl]

Same sampling policy as the baseline (stages.sample_times), same S4 transform, same timed S5 upload.
Paths:
  cpu        : PyAV software decoder, thread_type=AUTO
  nvdec_host : h264_cuvid / hevc_cuvid; every decoded frame is returned in host memory (nv12),
               i.e. the implicit device->host copy is *inside* the measured S3.
Per clip/path/repeat we record: open_ms (container open + decoder init + first frame), s3_ms
(decode to last target), s4_ms (RGB convert + resize/crop/normalise on picked frames), s5_ms
(host->GPU upload, synced), frames_decoded/returned. Correctness: PTS lists must match and the
224x224 model inputs must reach PSNR >= 40 dB vs the CPU path.
A GPU-resident path (TorchCodec CUDA / DALI) is NOT included: it is unavailable on this stack
(see results/EXP_D.md).
"""
import argparse
import json
import os
import statistics
import time

import av
import numpy as np
import torch
from av.codec.context import CodecContext

from . import stages as S

try:
    import pynvml
    pynvml.nvmlInit()
    _NV = pynvml.nvmlDeviceGetHandleByIndex(0)
except Exception:  # noqa: BLE001
    _NV = None


def _vram():
    return pynvml.nvmlDeviceGetMemoryInfo(_NV).used if _NV else 0


def _pick(frames, tb, start, tgts):
    """frames: iterator of decoded frames in display order. -> picks, pts list, decoded, t_first"""
    picks, pts, decoded, ti, t_first = [], [], 0, 0, None
    for fr in frames:
        if t_first is None:
            t_first = time.perf_counter()      # absolute; caller subtracts its own start
        decoded += 1
        t = fr.pts * tb - start if fr.pts is not None else None
        while ti < len(tgts) and t is not None and t >= tgts[ti] - 1e-9:
            picks.append(fr)
            pts.append(round(float(t), 6))
            ti += 1
        if ti >= len(tgts):
            break
    return picks, pts, decoded, t_first


def decode_cpu(path):
    t0 = time.perf_counter()
    with av.open(path) as c:
        s = c.streams.video[0]
        s.thread_type = "AUTO"
        tb, start = float(s.time_base), float(s.start_time * s.time_base) if s.start_time is not None else 0.0
        tgts = S.sample_times(float(c.duration / av.time_base))
        picks, pts, dec, tf = _pick(c.decode(s), tb, start, tgts)
        s3 = time.perf_counter() - t0
        return picks, pts, dict(frames_decoded=dec, open_ms=(tf - t0) * 1000, s3_ms=s3 * 1000, vram_mib=0)


def decode_nvdec(path):
    t0 = time.perf_counter()
    with av.open(path) as c:
        s = c.streams.video[0]
        name = {"h264": "h264_cuvid", "hevc": "hevc_cuvid"}[s.codec_context.name]
        ctx = CodecContext.create(name, "r")
        ctx.extradata = s.codec_context.extradata
        tb, start = float(s.time_base), float(s.start_time * s.time_base) if s.start_time is not None else 0.0
        tgts = S.sample_times(float(c.duration / av.time_base))

        def frames():
            for pkt in c.demux(s):
                yield from ctx.decode(pkt)
            yield from ctx.decode(None)

        picks, pts, dec, tf = _pick(frames(), tb, start, tgts)
        s3 = time.perf_counter() - t0
        v = _vram()
        return picks, pts, dict(frames_decoded=dec, open_ms=(tf - t0) * 1000, s3_ms=s3 * 1000, vram_mib=v / 2**20)


def s4_s5(picks):
    t0 = time.perf_counter()
    arrs = [S.to_model_input(f.to_image()) for f in picks]
    s4 = time.perf_counter() - t0
    t1 = time.perf_counter()
    x = torch.from_numpy(np.stack(arrs)).to("cuda")
    torch.cuda.synchronize()
    s5 = time.perf_counter() - t1
    return arrs, s4 * 1000, s5 * 1000, x.numel() * 4


def psnr(a, b):
    def den(x):
        return x * S._STD[:, None, None] + S._MEAN[:, None, None]
    mse = float(np.mean((den(a) - den(b)) ** 2))
    return 99.0 if mse == 0 else 10 * np.log10(1.0 / mse)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--out", default="results/exp_d.jsonl")
    a = ap.parse_args()

    clips = sorted(os.path.join(dp, f) for dp, _, fs in os.walk(a.root) for f in fs
                   if os.path.splitext(f)[1].lower() in S.VID_EXT)[:: a.stride]
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    out = open(a.out, "w", encoding="utf-8")

    # one-time warm-up, reported separately: CUDA context + first NVDEC session
    t = time.perf_counter(); torch.zeros(1).cuda(); torch.cuda.synchronize()
    cuda_init = time.perf_counter() - t
    smallest = min(clips, key=os.path.getsize)
    t = time.perf_counter(); decode_nvdec(smallest); first_nvdec = time.perf_counter() - t
    decode_cpu(smallest)
    out.write(json.dumps(dict(kind="setup", cuda_init_s=cuda_init, first_nvdec_session_s=first_nvdec)) + "\n")
    print(f"setup: CUDA init {cuda_init:.2f}s, first NVDEC session on smallest clip {first_nvdec:.2f}s")

    for path in clips:
        name = os.path.relpath(path, a.root)
        with av.open(path) as c:
            s = c.streams.video[0]
            meta = dict(codec=s.codec_context.name, w=s.width, h=s.height,
                        dur_s=round(float(c.duration / av.time_base), 2), size_mb=round(os.path.getsize(path) / 2**20, 1))
        ref_arrs, ref_pts = None, None
        for rep in range(a.repeats):
            for pname, fn in (("cpu", decode_cpu), ("nvdec_host", decode_nvdec)):
                try:
                    picks, pts, st = fn(path)
                    arrs, s4, s5, nbytes = s4_s5(picks)
                except Exception as e:  # noqa: BLE001
                    out.write(json.dumps(dict(file=name, path=pname, error=repr(e)[:200], **meta)) + "\n")
                    print(f"{name[:40]:40s} {pname:10s} ERROR {repr(e)[:80]}")
                    continue
                row = dict(file=name, path=pname, rep=rep, frames_returned=len(picks), s4_ms=s4, s5_ms=s5,
                           total_ms=st["s3_ms"] + s4 + s5, upload_bytes=nbytes, **st, **meta)
                if rep == 0:
                    if pname == "cpu":
                        ref_arrs, ref_pts = arrs, pts
                    elif ref_arrs is not None:
                        row["pts_match"] = pts == ref_pts
                        n = min(len(arrs), len(ref_arrs))
                        ps = [psnr(arrs[i], ref_arrs[i]) for i in range(n)]
                        row["psnr_min"], row["psnr_mean"] = min(ps), float(np.mean(ps))
                out.write(json.dumps(row) + "\n")
                out.flush()
                if rep == 0:
                    print(f"{name[:40]:40s} {pname:10s} {meta['w']}x{meta['h']} {meta['dur_s']:5.1f}s dec={st['frames_decoded']:5d} "
                          f"open={st['open_ms']:6.0f} s3={st['s3_ms']:7.0f} s4={s4:6.0f} s5={s5:5.1f}"
                          + (f" psnr_min={row.get('psnr_min', 0):.1f} pts_ok={row.get('pts_match')}" if pname != 'cpu' else ""))
    out.close()


if __name__ == "__main__":
    main()
