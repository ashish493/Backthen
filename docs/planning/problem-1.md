RoamIngest: Problem Statement
Draft v0.1 · 20 November 2026 · Working name; scope may change as the research progresses.
1. The problem
Importing a large personal media archive (tens of thousands of photos and hundreds of videos) into an AI-searchable index is slow, and it is unclear why. The work is usually described as one step, "ingestion", but it combines file discovery, metadata extraction, decoding, transformation, device transfers, model inference and writing results. Each stage has a different bottleneck, and existing tools (Pillow, OpenCV, PyAV, Decord, TorchCodec, NVIDIA DALI, WebDataset) each optimise only part of the path. Nobody can tune what they have not measured, so this project starts by measuring.
2. Scope
In scope
• Local media only: folders of images (JPEG, PNG, WebP) and videos (H.264, HEVC) at 720p to 4K.
• Output: metadata, thumbnails, sampled video frames, normalised media, optional model-ready tensors, and a processing manifest.
• Bounded memory: the pipeline must stay within a fixed RAM and VRAM budget (reference machine: 16 GB RAM, RTX 3050 with 4 GB VRAM) regardless of archive size.
• Incremental: unchanged files are not reprocessed, interrupted runs resume, and repeated runs give the same result.
• Reference-based access: the original archive is never duplicated. Derived assets are written only when requested.
• Hardware: CPU, RAM, SSD, and an optional NVIDIA GPU.
Out of scope (for now)
• Audio, documents and GPS data (future modality plugins).
• Network or cloud storage, and distributed or multi-node processing.
• Model training, and building a new embedding model.
• Writing a new codec or a thin wrapper around FFmpeg.
3. Intended users
• Developers of local-first media tools, such as photo or video search apps like RoamIndex, who need to index large personal libraries on consumer hardware.
• ML practitioners preparing image and video datasets from raw folders on a single workstation or laptop.
• Researchers who want a reproducible baseline for heterogeneous media ingestion on constrained hardware.
4. Research question
Primary question: Where does the time go when importing a large folder of mixed photos and videos: filesystem access, metadata extraction, media decoding, transformation, GPU transfers, or AI inference?
Follow-up question: Can a resource-aware, incremental pipeline reduce end-to-end processing time without changing output correctness or exceeding a fixed memory budget?
5. Hypotheses to test
Each will be marked confirmed, rejected or untested after benchmarking.
• A: Many small files cause significant filesystem overhead.
• B: Random seeking in video causes redundant decoding.
• C: CPU preprocessing starves the GPU.
• D: GPU-to-CPU transfers cancel the benefit of hardware decoding.
• E: Decoder initialisation dominates on short clips.
• F: Fixed worker allocation leaves stages idle on mixed workloads.
6. Success criteria
• Identify at least one reproducible bottleneck and explain why it occurs.
• Demonstrate an optimisation that gives a measurable end-to-end gain with identical output (checked against a reference backend).
• Publish the benchmark harness, datasets (or generators) and results so others can reproduce them.
7. Method in brief
Build a Python benchmark harness first. Test filesystem, image, video and end-to-end workloads. Separate cold-cache, warm-cache and end-to-end timings. Record per-stage active time, queue wait, resource use, and p50 and p95 latency. Choose one improvement based on the evidence, and only then consider a Rust core.