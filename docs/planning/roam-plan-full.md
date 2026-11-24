I'd structure RoamIndex as a three-project monorepo, with clear ownership boundaries and independently usable components.

The most important architectural decision: RoamIngest should not know about RoamIndex's UI, and RoamEngine should not depend on RoamIndex to function. Each component should have a defined contract so that other developers can reuse it independently.

Below is the architecture and a planning brief you can give directly to your AI coding agent.
RoamIndex — Full system architecture
User-facing product
RoamIndex
Offline-first multimodal set B archive explorer

Desktop application · Search · Map · Timeline · Media viewer


RoamIngest
Processing layer
Efficiently discovers, processes and enriches heterogeneous media.


RoamEngine
Intelligence layer
Indexes, retrieves, ranks and manages multimodal media.



Versioned ingestion contract · Index APIs

Local media and persistent storage

Original files · Metadata · Thumbnails · Embeddings · Indexes


Shared foundation
Common contracts and infrastructure

Schemas · IDs · Configuration · Logging · Benchmarking · Documentation


1. Project 1 — RoamIngest
RoamIngest
Media ingestion library

Purpose: Convert a heterogeneous media collection into a normalized, searchable-ready dataset with bounded resources, reproducibility and resumability.

It is responsible for processing media, not answering user queries.






















8











Internal architecture
RoamIngest
│
├── 1. Discovery
│   ├── Filesystem scanner
│   ├── Media type detection
│   ├── File metadata
│   ├── Content hashing
│   └── Incremental discovery
│
├── 2. Probing
│   ├── Image metadata probe
│   ├── Video codec probe
│   ├── Resolution / FPS / duration
│   ├── EXIF extraction
│   └── GPS / timestamp extraction
│
├── 3. Processing Planner
│   ├── Media classification
│   ├── Codec-aware routing
│   ├── Sampling strategy selection
│   ├── CPU / GPU resource budgeting
│   └── Storage-aware scheduling
│
├── 4. Image Processing
│   ├── JPEG / HEIC / PNG / WebP
│   ├── Reduced-size decoding
│   ├── Resize / normalization
│   └── Thumbnail generation
│
├── 5. Video Processing
│   ├── FFmpeg / PyAV
│   ├── CPU decoding
│   ├── NVDEC hardware decoding
│   ├── Sequential / keyframe sampling
│   ├── Frame extraction
│   └── Timestamp preservation
│
├── 6. AI Enrichment
│   ├── Image embeddings
│   ├── Video frame embeddings
│   ├── Optional OCR
│   ├── Optional audio transcription
│   └── Model/version tracking
│
├── 7. Persistence
│   ├── Manifest writer
│   ├── Thumbnail storage
│   ├── Frame storage
│   ├── Embedding storage
│   └── Checkpoint / resume
│
└── 8. Validation & Reporting
    ├── Determinism tests
    ├── Output verification
    ├── Resource monitoring
    ├── Benchmark reports
    └── Error reporting


Input and output
	Responsibility
Input	Local folders, images, videos and media metadata
Output	Normalized asset records, embeddings, thumbnails, frames and processing reports
Users	RoamEngine, Python developers, media-processing applications
Execution	Local, offline-capable, batch and incremental

Important design requirements
1. Resource-aware: worker counts, memory budgets, VRAM budgets and storage type must be configurable.
2. Deterministic: processing results must not depend on worker scheduling.
3. Resumable: interruption must not force the user to restart an entire archive.
4. Incremental: unchanged files should not be processed again.
5. Extensible: new image formats, codecs, sampling strategies and embedding models should be pluggable.
6. Quality-aware: faster sampling or reduced-resolution decoding must have measurable quality trade-offs.
Public interface
A developer should eventually be able to do something like:
from roamingest import Ingestor, IngestConfigconfig = IngestConfig(    storage="hdd",    max_ram_gb=6,    max_vram_gb=3.5,    video_sampling="adaptive",)report = Ingestor(config).run(    source="./travel_media",    output="./media_dataset")



Illustrative API, not a commitment to these exact names.

2. Project 2 — RoamEngine
RoamEngine
Multimodal retrieval engine

Purpose: Turn processed media into a searchable, queryable and incrementally maintainable index.

This is where the actual intelligence of the product lives.






















6











Internal architecture
RoamEngine
│
├── 1. Data Ingestion Adapter
│   ├── RoamIngest manifest reader
│   ├── External manifest import
│   ├── Schema validation
│   └── Asset registration
│
├── 2. Asset Catalogue
│   ├── Asset registry
│   ├── File identity / content hashes
│   ├── Image / video relationships
│   ├── Frame-to-video relationships
│   └── Metadata management
│
├── 3. Index Management
│   ├── Vector index
│   ├── Metadata index
│   ├── Full-text index
│   ├── Geospatial index
│   └── Index versioning
│
├── 4. Embedding Providers
│   ├── Text encoder
│   ├── Image encoder
│   ├── Model compatibility checks
│   ├── Embedding dimensions
│   └── Model/version registry
│
├── 5. Query Understanding
│   ├── Text query embedding
│   ├── Date/time extraction
│   ├── Location extraction
│   ├── Media type filters
│   └── Query decomposition
│
├── 6. Retrieval
│   ├── Vector similarity search
│   ├── Metadata filtering
│   ├── Geographic search
│   ├── Temporal search
│   └── Full-text search
│
├── 7. Ranking & Fusion
│   ├── Candidate merging
│   ├── Similarity ranking
│   ├── Metadata-aware ranking
│   ├── Deduplication
│   └── Result diversification
│
├── 8. Incremental Indexing
│   ├── Add assets
│   ├── Update assets
│   ├── Delete assets
│   ├── Detect moved files
│   └── Reconciliation
│
└── 9. Query Results
    ├── Asset details
    ├── Thumbnail references
    ├── Original media references
    ├── Exact video timestamps
    └── Related media


Example query execution
User asks:
Find pictures of swimming pools around Tangalle.

RoamEngine should:
1. Understand the text query.
2. Generate a text embedding.
3. Retrieve semantically similar images and video frames.
4. Apply geographic constraints or geographic ranking.
5. Merge candidates.
6. Rank and deduplicate.
7. Return matching assets, thumbnails and original media references.
The query system should also support structured constraints:
results = index.search(    query="swimming pools",    filters={        "location": "Tangalle",        "media_type": ["image", "video"],        "date_from": "2026-10-01",        "date_to": "2026-10-10",    },    limit=30,)



Storage architecture
I'd initially use:
Storage	Responsibility	Suggested starting point
Asset catalogue	Asset metadata and relationships	SQLite
Vector retrieval	Image/frame embeddings	FAISS or SQLite vector extension
Full-text search	Text and extracted metadata	SQLite FTS5
Geographic search	GPS coordinate queries	SQLite R-Tree
Media files	Original media	Existing filesystem
Derived media	Thumbnails and selected frames	Local filesystem

Don't make every storage backend a permanent architectural dependency. Introduce interfaces around them so that you can replace a vector backend without rewriting query execution.
Public interface
from roamengine import MediaIndexindex = MediaIndex.open("./my_media_index")results = index.search(    "mountain roads in the Alps",    limit=20)for result in results:    print(result.asset_id, result.score)



A separate developer should be able to use RoamEngine without installing RoamIndex.

3. Project 3 — RoamIndex
RoamIndex
Desktop application

Purpose: Provide an intuitive way for users to explore their personal media through natural language, geography, time and visual relationships.

This is the product people actually interact with.






















9











Frontend architecture
RoamIndex
│
├── 1. Application Shell
│   ├── Navigation
│   ├── Library management
│   ├── Settings
│   └── Background task status
│
├── 2. Search Interface
│   ├── Natural language search
│   ├── Search suggestions
│   ├── Filters
│   └── Search history
│
├── 3. Media Explorer
│   ├── Photo grid
│   ├── Video thumbnails
│   ├── Sorting
│   ├── Selection
│   └── Infinite scrolling
│
├── 4. Map Explorer
│   ├── GPS-based pins
│   ├── Location clusters
│   ├── Map-to-gallery filtering
│   └── Geographic exploration
│
├── 5. Timeline Explorer
│   ├── Date-based grouping
│   ├── Travel-day grouping
│   ├── Event clusters
│   └── Chronological browsing
│
├── 6. Media Viewer
│   ├── Full-resolution photo viewer
│   ├── Video player
│   ├── Exact timestamp navigation
│   ├── Metadata panel
│   └── Related media
│
└── 7. Library Management
    ├── Add media folders
    ├── Start / pause ingestion
    ├── Monitor processing
    ├── Update library
    └── Storage and index status


Backend architecture
Tauri Desktop Runtime
│
├── React + TypeScript frontend
│
├── Tauri commands / local IPC
│
├── Application service layer
│   ├── Library manager
│   ├── Ingestion task manager
│   ├── Search service
│   ├── Media service
│   └── Settings manager
│
├── RoamEngine integration
│   ├── Search
│   ├── Filters
│   ├── Metadata
│   └── Related media
│
└── RoamIngest integration
    ├── Start ingestion
    ├── Task status
    ├── Progress
    └── Cancellation / resume


I recommend keeping the UI separate from the processing and retrieval implementations. The desktop app should communicate through stable application services, rather than importing arbitrary internal modules.

4. How the projects communicate
This is the most important part to get right before implementation.

There are two primary contracts.
Contract A: RoamIngest → RoamEngine
RoamIngest produces a versioned dataset that RoamEngine can import.

Example:
media_dataset/
│
├── manifest.sqlite
│
├── embeddings/
│   ├── images.bin
│   └── video_frames.bin
│
├── thumbnails/
│   ├── image/
│   └── video/
│
├── frames/
│   └── selected_frames/
│
└── report.json


The manifest should contain records such as:
Field	Description
asset_id	Stable asset identifier
content_hash	File content identity
media_type	Image or video
original_path	Original media reference
file_size	File size
timestamp	Capture or media timestamp
latitude / longitude	GPS, if available
codec	Video codec, where applicable
duration	Video duration
embedding_ref	Reference to embedding
model_id	Embedding model identity
thumbnail_ref	Thumbnail reference
sampling_policy	How frames were selected

For videos, introduce a separate frame table with frame_id, asset_id, timestamp, embedding_ref and sampling_method.

Important: the manifest should describe where derived data lives, not assume all consumers use the same storage implementation.
Contract B: RoamEngine → RoamIndex
RoamEngine returns structured search results, not UI-specific objects.

Example:
{
  "asset_id": "asset_123",
  "media_type": "video",
  "score": 0.91,
  "thumbnail_ref": "thumbnails/video/asset_123.jpg",
  "original_ref": "media/asset_123.mp4",
  "matched_frame": {
    "timestamp": 18.4
  },
  "metadata": {
    "location": "Tangalle"
  }
}


RoamIndex decides how to display this result.

The engine shouldn't know about React components, grid layouts or map marker styling.

5. Recommended monorepo structure
I'd keep all three projects together initially. This makes development and integration easier while the interfaces are evolving.
RoamIndex/
│
├── apps/
│   └── roamindex/
│       ├── frontend/
│       │   ├── src/
│       │   │   ├── components/
│       │   │   ├── features/
│       │   │   │   ├── search/
│       │   │   │   ├── gallery/
│       │   │   │   ├── map/
│       │   │   │   ├── timeline/
│       │   │   │   └── viewer/
│       │   │   └── services/
│       │   └── package.json
│       │
│       └── src-tauri/
│           ├── src/
│           └── Cargo.toml
│
├── packages/
│   ├── roam-ingest/
│   │   ├── src/
│   │   │   └── roamingest/
│   │   │       ├── discovery/
│   │   │       ├── probing/
│   │   │       ├── planning/
│   │   │       ├── images/
│   │   │       ├── videos/
│   │   │       ├── enrichment/
│   │   │       ├── scheduling/
│   │   │       ├── persistence/
│   │   │       └── validation/
│   │   ├── tests/
│   │   ├── benchmarks/
│   │   └── pyproject.toml
│   │
│   └── roam-engine/
│       ├── src/
│       │   └── roamengine/
│       │       ├── catalogue/
│       │       ├── ingestion/
│       │       ├── embeddings/
│       │       ├── indexing/
│       │       ├── retrieval/
│       │       ├── ranking/
│       │       ├── filters/
│       │       ├── incremental/
│       │       └── api/
│       ├── tests/
│       └── pyproject.toml
│
├── shared/
│   ├── schemas/
│   ├── contracts/
│   └── fixtures/
│
├── benchmarks/
│   ├── datasets/
│   ├── evaluation/
│   └── reports/
│
├── docs/
│   ├── architecture/
│   ├── decisions/
│   ├── research/
│   └── api/
│
├── scripts/
│
├── .gitignore
├── README.md
└── LICENSE


This is a target structure, not a demand to reorganize all your existing code immediately. Preserve your working bench/ implementation and benchmark results; migrate incrementally.
6. Technology choices
Given your current hardware, local-first requirements and existing Python benchmark work, I'd start with the following.
Area	Recommendation	Reason
Ingestion	Python + native codec libraries	Your measured bottlenecks are mainly native decoding and I/O
Image processing	Pillow / libjpeg-turbo / pillow-heif	Existing tested paths
Video processing	PyAV / FFmpeg / NVDEC	Existing tested paths
ML inference	PyTorch + OpenCLIP initially	Reuse your actual benchmarked model
Metadata	SQLite	Portable and local
Vector search	FAISS initially	Simple local similarity retrieval
Full text	SQLite FTS5	Avoid unnecessary infrastructure
Geospatial	SQLite R-Tree	Local spatial filtering
Desktop	Tauri + React + TypeScript	Cross-platform desktop foundation
API	FastAPI	Convenient Python integration
Testing	pytest + benchmark harness	Reproducibility and regression testing

Do not introduce Kubernetes, distributed processing, cloud infrastructure or a Rust rewrite just to make the architecture look more sophisticated. They should only be introduced when a demonstrated requirement justifies them.

7. Development roadmap — what to build next
This is the order I'd follow. The important thing is to avoid building three projects simultaneously.
1. Stage 1 — Finish RoamIngest v0.1
   Current stage
   - Finish the keyframe and scheduling experiments.
   - Validate on the diverse 355 GB archive using an inventory-first approach.
   - Complete quality, determinism, recovery and memory tests.
   - Finalize the manifest schema.
   - Document benchmark methodology and limitations.
   Deliverable: independently runnable, tested ingestion library.
2. Stage 2 — RoamEngine foundation
   - Import RoamIngest output.
   - Build asset catalogue.
   - Store and query image embeddings.
   - Implement natural-language image search.
   - Return ranked results and metadata.
   Deliverable: Python API that searches a local media index.
3. Stage 3 — Multimodal retrieval
   - Add video frame retrieval.
   - Return exact video timestamps.
   - Combine semantic and metadata filters.
   - Add geographic and temporal queries.
   - Add ranking evaluation and retrieval benchmarks.
   Deliverable: searchable set B archive.
4. Stage 4 — Incremental indexing
   - Detect new and modified files.
   - Handle deletion and movement.
   - Update only affected index entries.
   - Verify consistency after interrupted updates.
   Deliverable: maintainable index, not just a one-time import.
5. Stage 5 — RoamIndex desktop MVP
   - Library setup.
   - Ingestion progress.
   - Search interface.
   - Photo/video gallery.
   - Video viewer with timestamp navigation.
   - Basic metadata and map.
   Deliverable: usable local desktop product.
6. Stage 6 — Public release and portfolio
   - Curate a safe public travel-media dataset.
   - Prepare a reproducible demo index.
   - Publish documentation and benchmarks.
   - Add installation instructions and sample applications.
   - Evaluate performance on hardware other than your own.
   Deliverable: public engineering project with a working demonstration.

8. Give this to your AI coding agent
I'd use the following as the master planning instruction. It deliberately asks the agent to inspect your existing repository before proposing changes, rather than blindly generating a new architecture or rewriting your completed work.
RoamIndex — Master Architecture and Implementation Planning Brief
You are the technical architect and senior systems engineer responsible for planning RoamIndex, a local-first multimodal travel-media search system.

Your task is to inspect the existing repository, understand completed work, and produce a realistic implementation roadmap for the complete system.

Do not start implementing features immediately. First produce an architecture assessment and staged execution plan.
1. Product vision
RoamIndex enables users to search and explore large personal collections of photos and videos using:
- Natural-language semantic search.
- Geographic and location-based search.
- Temporal and date-based search.
- Video frame and timestamp retrieval.
- Similarity search.
- Incremental library updates.
The system must work locally and offline, preserve the user's original media, and operate under constrained CPU, RAM, VRAM and storage resources.
2. Three independently reusable components
A. RoamIngest — Media Processing and Ingestion
Purpose: efficiently process heterogeneous media and produce normalized, reproducible, searchable-ready output.

Responsibilities:
- Filesystem discovery and incremental scanning.
- Media probing and metadata extraction.
- Content hashing and duplicate detection.
- Image decoding and resizing.
- Video decoding and temporal sampling.
- CPU/GPU/NVDEC scheduling.
- Codec-aware processing.
- Resource-aware execution.
- Optional AI enrichment and embedding generation.
- Thumbnail and frame generation.
- Manifest generation.
- Deterministic, resumable processing.
- Benchmarking and correctness validation.
RoamIngest must be usable independently through a Python SDK and CLI.

Current implementation and research already exist. Inspect these before proposing changes:
- BENCHMARK_PLAN.md
- OVERNIGHT_PLAN.md
- results/OVERNIGHT_REPORT.md
- Existing results/EXP_*.md
- bench/ingest.py
- bench/verify_ingest.py
- Other existing benchmark and analysis scripts.
Preserve existing research, implementations and benchmark evidence.

Do not recommend a Rust rewrite without measured evidence that Python orchestration is a bottleneck.
B. RoamEngine — Multimodal Indexing and Retrieval
Purpose: transform processed media into an efficient, queryable and incrementally maintainable search index.

Responsibilities:
- Import and validate RoamIngest manifests.
- Maintain asset catalogue and media relationships.
- Manage image and video-frame embeddings.
- Support configurable embedding providers.
- Implement vector similarity retrieval.
- Implement metadata filtering.
- Implement temporal queries.
- Implement geospatial queries.
- Implement full-text search where useful.
- Merge and rank retrieval candidates.
- Deduplicate and diversify results.
- Return exact video frame timestamps.
- Incrementally add, update and delete assets.
- Maintain index consistency and versioning.
- Expose a Python SDK, CLI and eventually a local API.
RoamEngine must work without the RoamIndex desktop application.
C. RoamIndex — Desktop Product
Purpose: give users an intuitive interface for exploring their personal media collections.

Target stack:
- Tauri.
- React.
- TypeScript.
Responsibilities:
- Library setup and management.
- Ingestion progress and status.
- Natural-language search.
- Photo and video gallery.
- Map explorer.
- Timeline explorer.
- Photo viewer.
- Video player with exact timestamp navigation.
- Metadata panel.
- Similar and related media.
- Local settings and resource controls.
The UI must communicate with stable application services. Do not place retrieval, media processing or database logic inside frontend components.
3. Architecture boundaries
Maintain these boundaries:

RoamIngest:
- Owns discovery, decoding, sampling and enrichment.
- Produces a documented, versioned dataset/manifest.
- Does not depend on RoamEngine or RoamIndex.
RoamEngine:
- Owns asset registration, indexing, retrieval, ranking and incremental updates.
- Imports RoamIngest output through a documented contract.
- Does not depend on the RoamIndex UI.
RoamIndex:
- Owns user interaction and presentation.
- Calls RoamIngest and RoamEngine through stable services/interfaces.
- Does not implement its own search engine.
Establish versioned contracts for:
1. RoamIngest → RoamEngine.
2. RoamEngine → applications.
3. RoamIngest → application task management.
Keep original media references separate from generated derivatives.
4. Initial technology direction
Evaluate, but do not blindly adopt:
- Python for ingestion and retrieval.
- PyAV / FFmpeg for video.
- Pillow / libjpeg-turbo / pillow-heif for images.
- PyTorch and OpenCLIP for initial embedding inference.
- SQLite for asset catalogue and metadata.
- FAISS or an alternative replaceable vector backend.
- SQLite FTS5 for text search.
- SQLite R-Tree for geographic search.
- FastAPI for local API.
- Tauri + React + TypeScript for desktop.
The system must remain usable on a laptop with 16 GB RAM and 4 GB GPU VRAM.

Prefer local-first, modular and replaceable components. Avoid unnecessary distributed systems, cloud dependencies and infrastructure.
5. Target repository organization
Propose a monorepo structure containing:
- apps/roamindex
- packages/roam-ingest
- packages/roam-engine
- shared/schemas
- shared/contracts
- benchmarks
- docs/architecture
- docs/decisions
- docs/research
Do not force a repository reorganization before inspecting the current implementation. Provide a migration plan that preserves working code and existing benchmark results.
6. Critical design questions to resolve
Investigate and recommend solutions for:
1. Stable asset identifiers across moved or renamed files.
2. Content identity versus file identity.
3. Versioned manifest and schema evolution.
4. Embedding model and dimension compatibility.
5. Image and video-frame relationships.
6. Video timestamp and sampling provenance.
7. Original media and derivative storage references.
8. Incremental index consistency and recovery.
9. Deleted, moved and modified source files.
10. Duplicate and near-duplicate media handling.
11. Index rebuilding and migration.
12. Query ranking and evaluation.
13. Memory and storage limits.
14. Offline operation and privacy.
15. Cross-platform filesystem and codec compatibility.
Explicitly distinguish requirements for the initial MVP from future capabilities.
7. Required implementation roadmap
Plan these stages in order:

Stage 1: Finish and validate RoamIngest v0.1.

Stage 2: Establish versioned data contracts and import RoamIngest output into RoamEngine.

Stage 3: Implement the asset catalogue and image semantic search.

Stage 4: Add video-frame retrieval and timestamp navigation.

Stage 5: Add temporal, geographic and metadata-aware retrieval.

Stage 6: Implement ranking, evaluation and incremental updates.

Stage 7: Build RoamIndex desktop MVP.

Stage 8: Prepare a public-safe dataset, reproducible demo and documentation.

For every stage, specify:
- Objective.
- Dependencies.
- Modules to implement.
- Existing files to reuse.
- New files to create.
- API and data contracts.
- Tests.
- Acceptance criteria.
- Performance measurements.
- Risks and unresolved questions.
- Explicit out-of-scope items.
8. Research and engineering standards
This is intended to become a serious Applied ML and systems engineering portfolio project.

Require:
- Reproducible benchmarks.
- Baseline comparisons.
- Retrieval quality evaluation.
- Resource utilization measurements.
- Deterministic processing.
- Correctness tests.
- Interruption and recovery tests.
- Documented limitations.
- Clear separation between measured results and projections.
Do not optimize only for throughput. Consider search quality, memory, storage, latency and usability together.
9. Immediate task
Inspect the current repository and all relevant existing documentation and benchmark results.

Then produce:
1. Current implementation inventory.
2. Architecture diagram showing all three components.
3. Component responsibility and dependency matrix.
4. Data flow diagram.
5. Versioned manifest/schema proposal.
6. Public SDK/API proposal.
7. Recommended monorepo structure and migration plan.
8. Detailed staged implementation roadmap.
9. Risks, architectural decisions and unresolved questions.
10. A prioritized list of the next five concrete tasks.
Do not rewrite existing code or start implementing the entire system.

Your first output must be a technical planning document grounded in the actual repository state, clearly identifying what already exists, what is partially implemented, and what is entirely new.

Be critical. Avoid unnecessary abstraction and overengineering. Make decisions based on the actual hardware, existing benchmarks and product requirements.





9. Three architectural decisions I want you to keep in mind
Before handing this off, there are three things I'd personally insist on.

1. The manifest is a first-class product interface.

Don't treat it as a temporary JSON export. It is the contract that lets other developers consume RoamIngest without depending on its internals. Give it a schema version, stable identifiers and documented semantics.

2. RoamEngine must be evaluated independently.

Build a retrieval evaluation dataset with queries such as:
- Swimming pool in Tangalle.
- Mountain roads in the Alps.
- Photos taken at night in Lisbon.
- Videos showing waterfalls.
- Similar photographs from the same trip.
Measure Recall@K, Precision@K, and ranking quality. A working search box alone is not evidence of a good retrieval engine.

3. Don't build all three simultaneously.
Immediate priority
Now

Finish RoamIngest validation and its public contract.

Next

Build RoamEngine's first semantic image search.

After that

Integrate video, maps, timelines and incremental updates.

Finally

Build the RoamIndex desktop experience around the working engine.

My recommendation is to ask your coding agent to produce the architecture assessment first. Review its findings against the actual repository, especially the existing ingestion implementation, before authorizing any large refactoring.

That way, the next stage builds on the research you've already completed rather than restarting the project under a new folder structure.