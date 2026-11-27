"""Stage what the installer needs to set up the engine on first launch (BOTTLENECKS.md #20).

    python apps/roamindex/tools/prepare_runtime.py

Writes src-tauri/resources/runtime/ (git-ignored):
  uv.exe            pinned uv release, checked against its published SHA-256
  wheels/           roamingest and roamengine built from this checkout
  requirements.txt  pinned direct dependencies (versions tested together)
  torch.json        PyTorch pins and the CUDA / CPU wheel indexes
The app (src-tauri/src/lib.rs) runs uv from there on first launch to install Python, PyTorch and the wheels into
%LOCALAPPDATA%\\app.roamindex.desktop\\runtime.
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

UV_VERSION = "0.13.7"
UV_SHA256 = "0cdc5b38fe5a8168ed6a25814f5ba43c21d313dd3187314fccb37b7cec46e021"
UV_URL = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-x86_64-pc-windows-msvc.zip"
PYTHON = "3.13"
TORCH = dict(torch="2.11.0", torchvision="0.26.0", python=PYTHON,
             cuda_index="https://download.pytorch.org/whl/cu128", cpu_index="https://download.pytorch.org/whl/cpu")
REQUIREMENTS = """\
# Direct dependencies of roamingest and roamengine, at the versions they were tested with.
av==19.0.2
numpy==2.5.5
pillow==13.0.0
pillow-heif==1.8.1
psutil==7.2.2
xxhash==4.1.0
open_clip_torch==4.2.2
timm==1.0.31
huggingface_hub==2.4.0
safetensors==0.8.0
ftfy==6.3.1
regex==2026.11.19
opencv-python-headless==5.1.0.95
rawpy==0.28.0
"""

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
OUT = os.path.join(HERE, "..", "src-tauri", "resources", "runtime")


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(os.path.join(OUT, "wheels"))
    data = urllib.request.urlopen(UV_URL, timeout=120).read()
    got = hashlib.sha256(data).hexdigest()
    if got != UV_SHA256:
        sys.exit(f"uv download checksum mismatch: {got}")
    with zipfile.ZipFile(io.BytesIO(data)) as z, open(os.path.join(OUT, "uv.exe"), "wb") as f:
        f.write(z.read("uv.exe"))
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", os.path.join(OUT, "wheels"),
                    os.path.join(REPO, "packages", "roam-ingest"), os.path.join(REPO, "packages", "roam-engine")],
                   check=True)
    with open(os.path.join(OUT, "requirements.txt"), "w", encoding="utf-8") as f:
        f.write(REQUIREMENTS)
    with open(os.path.join(OUT, "torch.json"), "w", encoding="utf-8") as f:
        json.dump(TORCH, f, indent=1)
    for name in sorted(os.listdir(os.path.join(OUT, "wheels"))):
        print("wheel", name)
    print("staged", os.path.normpath(OUT))


if __name__ == "__main__":
    main()
