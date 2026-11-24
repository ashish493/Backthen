"""Environment snapshot (BENCHMARK_PLAN §4). Usage: python -m bench.collect_env [out.json]"""
import json
import platform
import subprocess
import sys
from importlib import metadata


def _ver(pkg):
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return None


def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


def collect():
    import psutil

    env = {
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "cpu": platform.processor(),
        "cpu_cores_physical": psutil.cpu_count(logical=False),
        "cpu_cores_logical": psutil.cpu_count(logical=True),
        "ram_total_gb": round(psutil.virtual_memory().total / 2**30, 2),
        "libs": {p: _ver(p) for p in [
            "numpy", "pillow", "opencv-python-headless", "av", "torch", "torchvision",
            "psutil", "nvidia-ml-py", "xxhash", "blake3", "PyYAML", "decord", "torchcodec"]},
        "power_plan": _run(["powercfg", "/getactivescheme"]),
    }
    batt = psutil.sensors_battery()
    env["on_ac_power"] = None if batt is None else batt.power_plugged
    env["gpu"] = _run(["nvidia-smi",
                       "--query-gpu=name,memory.total,driver_version,clocks.max.sm",
                       "--format=csv,noheader"])
    try:
        import torch
        env["torch_cuda"] = torch.version.cuda
        env["cuda_available"] = torch.cuda.is_available()
    except ImportError:
        env["cuda_available"] = None
    try:
        import av
        env["pyav_codecs_gpu"] = sorted(c for c in ("h264_cuvid", "hevc_cuvid")
                                        if c in av.codecs_available)
    except ImportError:
        pass
    return env


if __name__ == "__main__":
    out = collect()
    text = json.dumps(out, indent=2)
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w") as f:
            f.write(text)
    print(text)
