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
