# Bigger CLIP models: Recall@1 on public benchmarks

**Goal:** raise Recall@1. Five OpenCLIP models were run through the same pipeline and the same benchmarks as `EXP_PUBLIC_RETRIEVAL.md`.

**Method:**
- Each benchmark was ingested with `roamingest run <folder> --storage ssd --model <arch>/<pretrained>`. Only `--model` differs between runs.
- Each caption was scored with the product's rule: a video's score is its best frame's score.
- For every model, a sample of 300 queries per benchmark gave identical ranks through `Engine.search`.
- Scripts: `bench/public_eval/run_models.sh`, `evaluate_fast.py`, `latency.py`. Raw numbers: `results/public_retrieval_models.json`.

**Model eligibility:** the pipeline makes 224 px inputs with CLIP mean/std normalisation. `--model` refuses models that need anything else. For example, ViT-L-14 laion2b uses mean 0.5, so it was not tested.

## Recall@1 (the right item ranked first)

| Model | Flickr30k 1k | MS-COCO 5k | MSR-VTT 1k-A (video) |
|---|---|---|---|
| ViT-B/32 laion2b (current default) | 0.662 | 0.394 | 0.327 |
| ViT-B-16 DFN-2B | 0.691 | 0.436 | 0.314 |
| ViT-L-14 DataComp-XL | 0.733 | 0.459 | 0.359 |
| ViT-L-14 DFN-2B | 0.751 | 0.487 | 0.368 |
| **ViT-H-14 DFN-5B** | **0.797** | **0.527** | **0.384** |
| *Change, H-14 vs current* | *+13.5 pts* | *+13.3 pts* | *+5.7 pts* |

## Other metrics

| Model | Flickr R@5 / R@10 / MRR / nDCG@10 | COCO R@5 / R@10 / MRR / nDCG@10 | MSR-VTT R@5 / R@10 / MRR / nDCG@10 |
|---|---|---|---|
| ViT-B/32 (current) | 0.884 / 0.931 / 0.760 / 0.799 | 0.654 / 0.754 / 0.515 / 0.564 | 0.556 / 0.652 / 0.440 / 0.481 |
| ViT-B-16 DFN-2B | 0.898 / 0.941 / 0.782 / 0.819 | 0.692 / 0.789 / 0.554 / 0.603 | 0.554 / 0.651 / 0.431 / 0.476 |
| ViT-L-14 DataComp-XL | 0.918 / 0.958 / 0.816 / 0.849 | 0.699 / 0.791 / 0.570 / 0.617 | 0.596 / 0.680 / 0.471 / 0.512 |
| ViT-L-14 DFN-2B | 0.927 / 0.961 / 0.830 / 0.861 | 0.739 / 0.823 / 0.602 / 0.649 | 0.609 / 0.682 / 0.476 / 0.517 |
| ViT-H-14 DFN-5B | 0.945 / 0.969 / 0.863 / 0.888 | 0.769 / 0.845 / 0.636 / 0.681 | 0.621 / 0.717 / 0.501 / 0.545 |

## Cost

**Query latency** comes from `Engine.search` on an idle machine: 200 queries on the Flickr library, the first 5 not counted.

**Ingest figures** are measured on COCO 5k, where the run is GPU-bound. They are per photo or frame.

| Model | Vector size | Query p50, text on CPU (app today) | Query p50, text on GPU | Text model VRAM | Ingest GPU time | Ingest peak VRAM / RAM |
|---|---|---|---|---|---|---|
| ViT-B/32 (current) | 512 | 32 ms | 9 ms | 0.25 GB | 2.2 ms | 0.7 / 1.7 GB |
| ViT-B-16 DFN-2B | 512 | 32 ms | 9 ms | 0.25 GB | 4.5 ms | 0.9 / 2.7 GB |
| ViT-L-14 DataComp-XL | 768 | 60 ms | 9 ms | 0.48 GB | 16.6 ms | 1.7 / 4.4 GB |
| ViT-L-14 DFN-2B | 768 | 61 ms | 9 ms | 0.48 GB | 18.3 ms | 1.8 / 4.4 GB |
| ViT-H-14 DFN-5B | 1024 | **228 ms** | 30 ms | 1.37 GB | 36.0 ms | **3.2 / 7.3 GB** |

## What this means for the 355 GB archive

These are projections from the rates above, not measurements.

**GPU time, per model.** The archive has about 74k photos and frames.
- **ViT-B/32 (current):** the reference run used 465 s of GPU time inside an 87 min wall time, because the USB drive limits the speed.
- **ViT-L-14:** about 22 min of GPU time.
- **ViT-H-14:** about 45 min of GPU time.

**Wall time.** Decoding overlaps with reading, so both models should stay close to the drive-bound wall time, roughly 87–95 min. This needs a measurement.

**ViT-H-14 limits:**
- **VRAM:** ingest peaks at 3.2 GB of the 4 GB card. That is close to the 3.5 GB budget, and the GPU text encoder (1.4 GB) cannot run at the same time.
- **RAM:** peaks at 7.3 GB on COCO. On the archive, with the 3.9 GB pipeline peak added, it would be about 9–10 GB of 16 GB.
- **Query latency:** 228 ms on the CPU, or 30 ms if the app encodes text on the GPU when it is free.

**Search index size:**
- **Memory:** at 1024 dims the vectors take about 300 MB instead of 151 MB.
- **Ranking time:** about 12 ms instead of 6 ms (a projection).

## Recommendation

| If the priority is | Choose | Why |
|---|---|---|
| Highest Recall@1 | **ViT-H-14 DFN-5B**, with the app encoding text on the GPU when it is free (CPU fallback) | Best on every benchmark. Costs VRAM and RAM headroom and needs an app change for fast queries. |
| A safe upgrade with no app changes | **ViT-L-14 DFN-2B** | About two thirds of H-14's gain. 61 ms queries on the CPU, 1.8 GB VRAM at ingest, fits every budget. |

In both cases:
- **Decide on the user's own data.** Confirm on the labelled `Dataset/` evaluation before re-indexing the archive; public captions are long scene descriptions, unlike short personal searches.
- **Existing indexes still load.** A re-index writes a new `model_id`; the manifest records the model per vector, so old indexes stay readable (ADR-009).

**Mean-pooling:** still a small, within-noise gain on MSR-VTT for every model, for example 0.394 vs 0.384 for H-14. The product keeps best-frame scoring for moment search.
