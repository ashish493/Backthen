"""Build the shipped text vectors of the app's constant prompts (BOTTLENECKS.md #8), so a new library needs no text
model for them. Rerun after changing any prompt in roamengine.engine.constant_prompts() or when adding a model.

Usage: python tools/build_prompt_seed.py [model_id ...]   (default: the default ingest model)
Writes src/roamengine/data/prompts/<arch>__<pretrained>.npz (float32, CPU, the same encoder the engine uses).
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from roamengine.engine import constant_prompts  # noqa: E402
from roamengine.text import TextEncoder, seed_path  # noqa: E402


def main(model_ids):
    texts = constant_prompts()
    for mid in model_ids:
        enc = TextEncoder(mid, device="cpu")
        vecs = np.concatenate([enc.encode(texts[i:i + 64]) for i in range(0, len(texts), 64)]).astype(np.float32)
        path = seed_path(mid)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        np.savez_compressed(path, texts=np.array(texts), vecs=vecs)
        print(f"{os.path.basename(path)}: {len(texts)} prompts, {vecs.shape[1]} dims, {os.path.getsize(path) // 1024} KiB")


if __name__ == "__main__":
    from roamingest.embedder import DEFAULT_MODEL
    main(sys.argv[1:] or [f"open_clip/{DEFAULT_MODEL[0]}/{DEFAULT_MODEL[1]}/fp16"])
