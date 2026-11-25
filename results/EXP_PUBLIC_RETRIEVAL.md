# Retrieval quality on public benchmarks (unchanged pipeline)

**Question:** how well does the product find things, measured on standard public data with known answers?

**Setup:** the code under test is RoamIngest 0.1.1 and RoamEngine with default settings.
- Each benchmark was ingested with `roamingest run <folder> --storage ssd`, with no other flags.
- Every caption was searched with `Engine.search`, the same call the app makes, over the whole benchmark library.
- The datasets and their indexes were deleted afterwards; only these aggregate numbers are kept.
- Scripts: `bench/public_eval/prep.py`, `bench/public_eval/evaluate.py`. Raw numbers: `results/public_retrieval.json`.

**Benchmarks:**

| Benchmark | Items | Queries | Task |
|---|---|---|---|
| Flickr30k 1k test (Karpathy split) | 1,000 photos | 5,000 captions | text → photo |
| MS-COCO 5k test (Karpathy split) | 5,000 photos | 25,010 captions | text → photo |
| MSR-VTT 1k-A test | 1,000 video clips | 1,000 captions | text → video |

**Metrics:** each query has one correct item.
- **R@K:** the share of queries where that item is in the top K.
- **MRR:** mean of 1/rank.
- **nDCG@10:** with one relevant item, this is 1/log2(rank+1) if the item is in the top 10, else 0.
- **Median rank:** the rank of the correct item, middle value over all queries.

## Results

| Benchmark | R@1 | R@5 | R@10 | R@50 | MRR | nDCG@10 | Median rank |
|---|---|---|---|---|---|---|---|
| Flickr30k 1k | **0.662** | 0.884 | 0.931 | 0.986 | 0.760 | 0.799 | 1 |
| MS-COCO 5k | **0.394** | 0.654 | 0.754 | 0.933 | 0.515 | 0.564 | 2 |
| MSR-VTT 1k-A (video) | **0.327** | 0.556 | 0.652 | 0.859 | 0.440 | 0.481 | 4 |

## Does the pipeline lose quality? No.

Two references, both for text → image with OpenCLIP ViT-B/32 laion2b_s34b_b79k:
- **Published:** the OpenCLIP benchmark table (`docs/openclip_retrieval_results.csv` in the open_clip repo).
- **Plain OpenCLIP:** the official preprocessing on a full decode in fp32, run here on the same files.

| R@1 / R@5 / R@10 | Product (fast decode, fp16) | Plain OpenCLIP, same files | Published |
|---|---|---|---|
| Flickr30k | 0.662 / 0.884 / 0.931 | 0.667 / 0.883 / 0.931 | 0.668 / 0.884 / 0.931 |
| MS-COCO | 0.394 / 0.654 / 0.754 | 0.394 / 0.654 / 0.756 | 0.393 / 0.654 / 0.756 |

- **Product vs reference:** within 0.5 points of plain OpenCLIP. That difference is smaller than the noise between runs on 5,000 queries.
- **Reference vs published:** plain OpenCLIP reproduces the published numbers, so the evaluation itself is sound.
- **Vector agreement:** the vectors stored by the pipeline agree with plain OpenCLIP's vectors for the same file at a median cosine of 0.999 (Flickr) and 0.997 (COCO).

## Video: best frame vs mean of frames

The product scores a video by its best-matching frame, which is also what gives the clickable moments. Each video had a median of 7 sampled frames (min 6, max 16), one every 2 s.

| MSR-VTT 1k-A | R@1 | R@5 | R@10 | MRR |
|---|---|---|---|---|
| Best frame (product) | 0.327 | 0.556 | 0.652 | 0.440 |
| Mean of frames | 0.341 | 0.567 | 0.668 | 0.451 |

- **Mean of frames:** slightly better on MSR-VTT's whole-clip captions. The gap is +1.4 points R@1 over 1,000 queries, about one standard error.
- **Best frame:** better suited to moment search, finding a shot inside a long personal video.
- **Possible change:** a blend of the two (for example, max plus mean) could be tested on a labelled set of personal photos before changing anything.

## Speed

| | Flickr30k | MS-COCO | MSR-VTT |
|---|---|---|---|
| Ingest wall time | 2.5 s (1,000 photos) | 12.6 s (5,000 photos) | 56.7 s (1,000 clips) |
| Query p50 | 32 ms | 71 ms * | 149 ms * |
| Of which ranking | 1.0 ms | 1.3 ms | 3.6 ms |

\* COCO and MSR-VTT queries ran at the same time and shared the CPU, which inflated the text-encode share. Flickr ran alone and shows the normal figure.

## What this says about the product
- **The model sets the quality, not the pipeline.** The fast decode, fp16 and smart frame sampling cost nothing measurable.
- **Absolute quality** is what ViT-B/32 delivers:
  - Strong on Flickr-style everyday photos: the right photo is first two times in three, and in the top 10 for 93% of queries.
  - Harder on COCO's 5,000 cluttered scenes: the right photo is in the top 10 for 75% of queries.
- **Larger models:** they score higher on the same benchmarks (see the published table). The GPU was idle 92% of the 355 GB ingest (`BOTTLENECK_ARCHIVE355.md`), so a larger model is the main quality lever. Check it on a labelled set of personal photos before switching.
- **Public data is not a personal archive.** These captions describe whole scenes in detail; personal searches are short ("beach", "temple at night"). A labelled evaluation of personal photos remains the deciding test.
