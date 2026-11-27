"""CLIP text encoder matching the image model recorded in the manifest (models.model_id)."""
import os
import re
import threading

import numpy as np


def parse_model_id(model_id):
    """'open_clip/ViT-B-32/laion2b_s34b_b79k/fp16' -> ('ViT-B-32', 'laion2b_s34b_b79k')."""
    parts = model_id.split("/")
    if len(parts) < 3 or parts[0] != "open_clip":
        raise ValueError(f"unsupported model_id {model_id!r} (expected open_clip/<arch>/<pretrained>/<dtype>)")
    return parts[1], parts[2]


class _NoFastPath(Exception):
    pass


def _load_text_tower(arch, pretrained):
    """Build only the text tower and read only its weights (BOTTLENECKS.md #6).

    The model is built on the `meta` device (no memory, no random init), the image tower is dropped, and only the
    text tower is allocated and filled from the checkpoint: safetensors read tensor by tensor, zip .bin files memory-
    mapped, so the image-tower weights are never read into RAM. Measured on CPU, RSS added and load time: ViT-B-32
    601 -> 237 MB (3.2 -> 2.0 s), ViT-L-14 2,871 -> 479 MB (3.6 -> 0.5 s), ViT-H-14 5,706 -> 1,345 MB (8.3 -> 1.2 s),
    with bit-identical vectors. Anything unexpected raises _NoFastPath and the caller does the full load instead."""
    import logging

    import open_clip
    import torch
    from open_clip.pretrained import download_pretrained, get_pretrained_cfg
    cfg = get_pretrained_cfg(arch, pretrained)
    if not cfg:
        raise _NoFastPath("not a known pretrained tag")
    log = logging.getLogger("open_clip")
    level, log.level = log.level, logging.ERROR          # "initialized randomly": true on meta, and not what we load
    try:
        with torch.device("meta"):
            model = open_clip.create_model(arch, pretrained=pretrained, load_weights=False, device="meta")
    finally:
        log.setLevel(level)
    if getattr(model, "visual", None) is None:
        raise _NoFastPath("no separate image tower")
    model.visual = None
    masks = []                                            # non-persistent buffers are not in the checkpoint
    for name, buf in model.named_buffers():
        mod_name, _, leaf = name.rpartition(".")
        mod = model.get_submodule(mod_name)
        if leaf in mod._non_persistent_buffers_set:
            if leaf != "attn_mask" or buf.ndim != 2 or buf.shape[0] != buf.shape[1]:
                raise _NoFastPath(f"unknown non-persistent buffer {name}")
            masks.append((mod, leaf, buf.shape[0]))
    want = {k: tuple(v.shape) for k, v in model.state_dict().items()}    # dtype: load_state_dict casts, as before
    path = download_pretrained(cfg)
    if path.endswith(".safetensors"):
        from safetensors import safe_open
        with safe_open(path, framework="pt") as f:
            sd = {k: f.get_tensor(k) for k in f.keys() if k in want}
    else:
        full = torch.load(path, map_location="cpu", mmap=True, weights_only=True)
        full = full.get("state_dict", full) if isinstance(full, dict) else None
        if not isinstance(full, dict):
            raise _NoFastPath("unexpected checkpoint layout")
        sd = {k: v for k, v in full.items() if k in want}
        del full
    if {k: tuple(v.shape) for k, v in sd.items()} != want:
        raise _NoFastPath("checkpoint keys need conversion")   # open_clip's loader converts; ours does not
    model.to_empty(device="cpu")
    model.load_state_dict(sd, strict=True)
    del sd
    for mod, leaf, n in masks:                            # the causal mask, exactly as open_clip builds it
        setattr(mod, leaf, torch.full((n, n), float("-inf")).triu_(1))
    return model


class TextEncoder:
    """Text tower only is used; runs on CPU by default so the GPU stays free for ingest (about 20-40 ms/query)."""

    def __init__(self, model_id, device="cpu", fast=True):
        import open_clip
        import torch
        arch, pretrained = parse_model_id(model_id)
        self.torch, self.device, self.model_id = torch, device, model_id
        model = None
        if fast:
            try:
                model, self.load_path = _load_text_tower(arch, pretrained), "text_tower"
            except Exception:  # noqa: BLE001         # _NoFastPath, or an open_clip change: the full load still works
                model = None
        if model is None:
            model, _, _ = open_clip.create_model_and_transforms(arch, pretrained=pretrained)
            model.visual = None                            # drop the image tower
            self.load_path = "full"
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


SEED_DIR = os.path.join(os.path.dirname(__file__), "data", "prompts")


def seed_path(model_id):
    """Shipped vectors of the app's constant prompts for one text model (tools/build_prompt_seed.py). Keyed by
    architecture and pretrained tag only: the text tower runs in fp32 whatever dtype the image model used."""
    arch, pretrained = parse_model_id(model_id)
    return os.path.join(SEED_DIR, re.sub(r"[^A-Za-z0-9._-]+", "_", f"{arch}__{pretrained}") + ".npz")


def _read_npz(path):
    try:
        with np.load(path, allow_pickle=False) as z:
            return dict(zip(z["texts"].tolist(), z["vecs"].astype(np.float32)))
    except (OSError, KeyError, ValueError):
        return {}


class PromptCache:
    """Text vectors for the app's own fixed prompts (cover and people prompts, suggestion chips, town names), stored
    next to the library so each is encoded once per model instead of once per launch (encoding costs ~20 ms per
    prompt on the CPU, and these never change). Typed search text is never stored here.

    The constant prompts also ship with the app for the default model (seed_path), so a new library needs no text
    model for them (BOTTLENECKS.md #8); only library-specific prompts such as town names are ever encoded."""

    def __init__(self, folder, model_id):
        self.path = os.path.join(folder, f"prompts-{re.sub(r'[^A-Za-z0-9._-]+', '_', model_id)}.npz")
        try:
            self.seed = seed_path(model_id)
        except ValueError:
            self.seed = None
        self._lock = threading.Lock()
        self._mem = None

    def _load(self):
        if self._mem is None:
            mem = _read_npz(self.seed) if self.seed else {}
            mem.update(_read_npz(self.path))              # the library's own copy wins (it was encoded here)
            self._mem = mem
        return self._mem

    def missing(self, texts):
        """The texts that would need the text model (not shipped, not cached yet)."""
        with self._lock:
            mem = self._load()
            return [t for t in dict.fromkeys(texts) if t not in mem]

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
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path[:-4] + ".tmp.npz"
        np.savez(tmp, texts=np.array(list(self._mem)), vecs=np.stack(list(self._mem.values())))
        os.replace(tmp, self.path)
