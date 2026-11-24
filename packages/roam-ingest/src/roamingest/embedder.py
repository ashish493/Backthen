"""CLIP image embedder. Batches are always padded to a fixed size, so a row's embedding does not depend on its
batch neighbours (determinism, T1). Output: L2-normalised float16."""
import numpy as np

from . import transforms as T

DEFAULT_MODEL = ("ViT-B-32", "laion2b_s34b_b79k")


class ClipEmbedder:
    def __init__(self, batch=64, device="auto", arch=DEFAULT_MODEL[0], pretrained=DEFAULT_MODEL[1]):
        import open_clip
        import torch
        self.torch = torch
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device, self.batch = device, batch
        self.half = device == "cuda"
        model, _, _ = open_clip.create_model_and_transforms(arch, pretrained=pretrained)
        cfg = open_clip.get_model_preprocess_cfg(model)
        size = cfg.get("size", 224)
        size = size if isinstance(size, int) else size[0]
        same = lambda u, v: all(abs(x - y) < 1e-4 for x, y in zip(u, v))    # noqa: E731
        if size != T.MODEL_SIZE or not same(cfg.get("mean", T.MEAN), T.MEAN) or not same(cfg.get("std", T.STD), T.STD):
            raise ValueError(f"{arch}/{pretrained} needs {size} px input with mean {cfg.get('mean')}; the pipeline makes "
                             f"{T.MODEL_SIZE} px with CLIP mean/std")
        model = model.eval()
        self.model = (model.half() if self.half else model.float()).to(device)
        dtype = "fp16" if self.half else "fp32"
        self.info = dict(model_id=f"open_clip/{arch}/{pretrained}/{dtype}", name=arch, pretrained=pretrained,
                         dim=int(self.model.visual.output_dim), dtype="float16", preprocess=T.PREPROCESS)
        self._x = np.zeros((batch, 3, T.MODEL_SIZE, T.MODEL_SIZE), dtype=np.float32)
        self.encode([])                                   # warm-up (cuDNN autotune, allocator)

    def encode(self, rows):
        """rows: list of float32 CHW arrays (len <= batch) -> float16 array (len(rows), dim)."""
        torch = self.torch
        x = self._x
        x[:] = 0
        for i, r in enumerate(rows):
            x[i] = r
        with torch.no_grad():
            t = torch.from_numpy(x).to(self.device)
            e = self.model.encode_image(t.half() if self.half else t).float()
            e = torch.nn.functional.normalize(e, dim=-1).half().cpu().numpy()
        if self.device == "cuda":
            torch.cuda.synchronize()
        return e[:len(rows)]
