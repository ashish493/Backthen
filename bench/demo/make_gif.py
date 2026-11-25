"""Turn record.mjs frames into a compact looping GIF for the README.

usage: python bench/demo/make_gif.py FRAMES_DIR OUT.gif [--width 760] [--fps 12] [--speed 1.0] [--max-colors 128]
Frames are resampled to a steady frame rate from their capture times (--speed 1.5 plays 1.5x faster), cropped to the
recorded page area, scaled to --width, and quantised to one shared palette so the file stays small.
"""
import argparse
import json
import os

from PIL import Image

ap = argparse.ArgumentParser()
ap.add_argument("frames")
ap.add_argument("out")
ap.add_argument("--width", type=int, default=760)
ap.add_argument("--fps", type=float, default=12)
ap.add_argument("--speed", type=float, default=1.0)
ap.add_argument("--max-colors", type=int, default=128)
ap.add_argument("--dither", action="store_true", help="Floyd-Steinberg: smoother photos, larger file")
ap.add_argument("--per-frame", action="store_true", help="a palette per frame: true photo colours, larger file")
ap.add_argument("--hold", type=float, default=1.2, help="seconds to hold the last frame before looping")
a = ap.parse_args()

meta = json.load(open(os.path.join(a.frames, "meta.json")))
x, y, w, h = (round(v) for v in meta["box"])
frames = meta["frames"]
if not frames:
    raise SystemExit("no frames")
duration = frames[-1]["t"]
step = a.speed / a.fps                                 # seconds of recording per GIF frame
times = [i * step for i in range(int(duration / step) + 1)]
picked, j = [], 0
for t in times:                                        # the latest frame captured at or before t
    while j + 1 < len(frames) and frames[j + 1]["t"] <= t:
        j += 1
    picked.append(frames[j]["file"])

scale = a.width / w
size = (a.width, round(h * scale))
imgs = [Image.open(os.path.join(a.frames, f)).convert("RGB").crop((x, y, x + w, y + h)).resize(size, Image.LANCZOS)
        for f in picked]
dither = Image.Dither.FLOYDSTEINBERG if a.dither else Image.Dither.NONE
if a.per_frame:
    q = [im.quantize(colors=a.max_colors, method=Image.Quantize.MEDIANCUT, dither=dither) for im in imgs]
else:                                                  # one palette for all frames (from a sample): smaller files
    sample = Image.new("RGB", (size[0], size[1] * min(6, len(imgs))))
    for k, im in enumerate(imgs[:: max(1, len(imgs) // 6)][:6]):
        sample.paste(im, (0, size[1] * k))
    pal = sample.quantize(colors=a.max_colors, method=Image.Quantize.MEDIANCUT)
    q = [im.quantize(palette=pal, dither=dither) for im in imgs]
ms = round(1000 / a.fps)
durations = [ms] * (len(q) - 1) + [ms + round(a.hold * 1000)]
q[0].save(a.out, save_all=True, append_images=q[1:], duration=durations, loop=0, optimize=True, disposal=1)
print(f"{a.out}: {len(q)} frames, {size[0]}x{size[1]}, {os.path.getsize(a.out) / 1e6:.1f} MB")
