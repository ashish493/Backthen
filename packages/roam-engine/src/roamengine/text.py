"""CLIP text encoder matching the image model recorded in the manifest (models.model_id)."""
import threading

import numpy as np


def parse_model_id(model_id):
    """'open_clip/ViT-B-32/laion2b_s34b_b79k/fp16' -> ('ViT-B-32', 'laion2b_s34b_b79k')."""
    parts = model_id.split("/")
    if len(parts) < 3 or parts[0] != "open_clip":
        raise ValueError(f"unsupported model_id {model_id!r} (expected open_clip/<arch>/<pretrained>/<dtype>)")
    return parts[1], parts[2]


class TextEncoder:
    """Text tower only is used; runs on CPU by default so the GPU stays free for ingest (about 20-40 ms/query)."""

    def __init__(self, model_id, device="cpu"):
        import open_clip
        import torch
        arch, pretrained = parse_model_id(model_id)
        self.torch, self.device, self.model_id = torch, device, model_id
        model, _, _ = open_clip.create_model_and_transforms(arch, pretrained=pretrained)
        model.visual = None                                # drop the image tower (about 350 MB)
        self.model = model.eval().float().to(device)
        self.tokenizer = open_clip.get_tokenizer(arch)
        self._lock = threading.Lock()
        self._cache = {}

    def encode(self, texts):
        """list[str] -> float32 (n, dim), L2-normalised."""
        out = [self._cache.get(t) for t in texts]          # read hits first: inserting below may clear the cache
        todo = [i for i, v in enumerate(out) if v is None]
        if todo:
            torch = self.torch
            with self._lock, torch.no_grad():
                tok = self.tokenizer([texts[i] for i in todo]).to(self.device)
                e = torch.nn.functional.normalize(self.model.encode_text(tok).float(), dim=-1).cpu().numpy()
            for i, v in zip(todo, e):
                out[i] = v
                if len(self._cache) > 512:
                    self._cache.clear()
                self._cache[texts[i]] = v
        return np.stack(out).astype(np.float32)


class PromptCache:
    """Text vectors for the app's own fixed prompts (cover and people prompts, suggestion chips, town names), stored
    next to the library so each is encoded once per model instead of once per launch (encoding costs ~20 ms per
    prompt on the CPU, and these never change). Typed search text is never stored here."""

    def __init__(self, folder, model_id):
        import os
        import re
        self.path = os.path.join(folder, f"prompts-{re.sub(r'[^A-Za-z0-9._-]+', '_', model_id)}.npz")
        self._lock = threading.Lock()
        self._mem = None

    def _load(self):
        if self._mem is None:
            try:
                with np.load(self.path, allow_pickle=False) as z:
                    self._mem = dict(zip(z["texts"].tolist(), z["vecs"].astype(np.float32)))
            except (OSError, KeyError, ValueError):
                self._mem = {}
        return self._mem

    def get(self, texts, encode):
        """Vectors for texts (n, dim); `encode(list) -> array` runs only for texts not cached yet."""
        with self._lock:
            mem = self._load()
            todo = [t for t in dict.fromkeys(texts) if t not in mem]
        if todo:
            vecs = np.concatenate([encode(todo[i:i + 256]) for i in range(0, len(todo), 256)])
            with self._lock:
                for t, v in zip(todo, vecs):
                    mem[t] = np.asarray(v, np.float32)
                self._save()
        return np.stack([mem[t] for t in texts]).astype(np.float32) if texts else np.zeros((0, 0), np.float32)

    def _save(self):
        import os
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path[:-4] + ".tmp.npz"
        np.savez(tmp, texts=np.array(list(self._mem)), vecs=np.stack(list(self._mem.values())))
        os.replace(tmp, self.path)
