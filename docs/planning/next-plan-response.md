This is a much stronger next experiment than simply trying to squeeze more throughput out of FFmpeg or NVDEC. You're shifting the research question from “How do I decode faster?” to “How do I avoid decoding or reading data that the retrieval task doesn't need, while keeping the result useful?”

That is a more meaningful systems contribution for RoamIngest.

I especially like that you've fixed the keyframe quality thresholds before running the benchmarks. That makes the experiment falsifiable rather than choosing a threshold after seeing the results.

There are, however, a few things I'd tighten before treating the plan as final.
1. The keyframe experiment needs one important distinction
Your proposed keyframe strategy is potentially a major win, but “nearest preceding keyframe” is not the same as “frame at the target time.”

If the target is at 30 seconds and the preceding keyframe is at 24 seconds, you're representing six seconds of content with a frame from a different scene. That may be acceptable for broad scene retrieval, but not necessarily for finding a particular moment.

More importantly, cosine coverage alone can hide temporal failures. A video could have visually similar frames across several seconds and score well even when the selected frame misses the event.

I'd measure three things separately:
- Semantic coverage: your proposed best cosine similarity between exact-sampled and keyframe-sampled embeddings.
- Temporal displacement: absolute difference between the target timestamp and selected keyframe timestamp.
- Distinct-frame retention: unique keyframes returned divided by requested samples.
One refinement: make the comparison macro-averaged by video, not just pooled across all frames. Otherwise a few long videos can dominate the score and conceal poor coverage on short clips.

I'd also add a fourth quality metric:

- Bidirectional embedding coverage: exact → keyframe tells you how well keyframes represent the original samples; keyframe → exact helps reveal whether keyframes introduce representations that don't correspond closely to any exact sample.

Your current thresholds are reasonable starting gates, but I'd make the decision per dataset and per video too. A strong overall median should not allow a few badly represented videos to disappear in the average.
A better sampling policy may emerge
Don't assume keyframe-only must be the final policy. The benchmark could lead to a hybrid:
1. Use the preceding keyframe for each target time.
2. If the keyframe is too far away in time, decode forward from it to the target.
3. If multiple targets map to the same keyframe, optionally decode additional frames within that interval.
4. Record the sampling method and actual timestamp in the manifest.
That gives you a useful design space: keyframe-only, exact sampling, and adaptive keyframe-plus-forward-decode. It may turn out that keyframe-only is excellent for long static scenes but poor for fast-changing travel footage.
2. Be careful with the HDD claim
Seeking can reduce bytes read, but it does not automatically reduce wall-clock time on a hard drive.

Random seeks introduce head movement. If targets are scattered across a file, the drive may spend more time repositioning than reading. The result depends on:
- Number of seeks per video.
- Distance between seeks.
- Whether the file is fragmented.
- Whether FFmpeg or the OS reads ahead.
- Whether the decoder must read additional data around the target keyframe.
So measure bytes read and elapsed time independently. A reduction from 10.2 GiB to a fraction of that is valuable, but only if it also improves end-to-end ingestion time.

For the HDD experiment, I'd report:
- Total bytes read from the source drive.
- Number of seek operations.
- Video decode time and total ingestion time.
- Effective MB/s and time spent waiting on I/O.
Also compare the same sampling policy on SSD and HDD. That will tell you whether keyframe sampling is a general algorithmic improvement or an especially useful storage-aware optimisation.
3. Separate the power-management experiment
Your 2.7× power-plan result is important, but it is also a major confounder.

I'd run these as distinct configurations:
Configuration	Windows plan	Process throttling	Priority
A	Balanced	Default	Normal
B	Balanced	Disabled	Normal
C	Balanced	Disabled	High
D	High Performance	Default	Normal

This lets you determine whether the gain comes from process-level throttling, priority, or the system power plan. Don't combine B and C and then attribute the improvement to EcoQoS.

One caution: disabling process power throttling may not reproduce the full High Performance result. The system power plan can affect CPU frequency and other platform-level behaviour beyond the process's QoS settings. Treat this as an experiment, not an expected recovery.

I'd also avoid high priority as a default product setting. It can make the machine less responsive while ingestion runs. If normal priority with throttling disabled recovers most of the performance, that's a much better user-facing outcome.
4. Codec-aware routing and thread budgeting
The HEVC result is promising, but I'd separate two questions:
- Does NVDEC improve HEVC decoding?
- Does routing HEVC to NVDEC improve the whole ingestion pipeline?
The first is already supported by your per-file result. The second needs the end-to-end benchmark.

For thread budgeting, don't only compare the old 16-thread default against a new cap. Run a small controlled sweep:
FFmpeg threads per decoder	What it tests
1	Minimal decoder parallelism
2	Low contention
4	Moderate parallelism
8	Higher parallelism

Keep the total worker configuration fixed. Otherwise, you'll change too many variables at once.

Also record the number of concurrent decoder instances. Two decoders with eight threads each are not equivalent to eight independent decoders.
5. Utilisation timelines should become part of the benchmark infrastructure
This is probably the most valuable instrumentation improvement in this round.

I'd capture one-second intervals for:
Metric	Why it matters
CPU utilisation	Identifies CPU starvation or saturation
GPU utilisation	Shows whether CLIP or NVDEC is underutilised
NVDEC utilisation	Separates video decoding from general GPU activity
Disk read throughput	Identifies HDD/SSD bottlenecks
Process I/O bytes	Measures actual ingestion reads
Queue depths	Identifies producer/consumer imbalance
In-flight memory	Validates your byte-budget mechanism
Stage completion counts	Shows which pipeline stage is falling behind

Averages alone are insufficient. For example, 50% CPU utilisation could mean the CPU was continuously half-utilised or completely idle for half the run and saturated for the other half.

The timelines should help distinguish those cases.
6. One additional experiment I'd introduce
High research value
 Adaptive sampling based on temporal density.

Your current exact sampler decodes approximately 64 frames for every retained frame. Keyframe sampling reduces that work dramatically, but can sacrifice temporal precision.

Instead, consider an adaptive policy:
Long video, sparse sampling
Keyframe-first
Use keyframes and avoid decoding unnecessary intermediate frames.


Short video, dense sampling
Sequential decode
Decode sequentially because repeated seeking may cost more than decoding intervening frames.


Fast-changing scenes
Higher temporal precision
Decode additional frames around target intervals when keyframe coverage or temporal displacement is inadequate.


This is where your earlier observation about random seeking decoding 69–75× the requested frames becomes useful. You can build a decision policy around sampling density rather than imposing one strategy on every video.

It also gives you a potentially interesting research question:

Can a resource-aware ingestion pipeline dynamically select media sampling strategies based on storage characteristics, codec, video duration, and desired temporal coverage?

That's a much stronger direction than simply saying you integrated NVDEC.
7. My recommended execution order
I'd run this round in the following order:
1. Keyframe-only sampling: establish its quality and I/O reduction independently.
2. Codec-aware routing: add HEVC NVDEC and test seek-based decoding.
3. Thread budgeting: tune the decoder configuration.
4. Power experiment: isolate EcoQoS and priority effects.
5. Combined end-to-end run: test the best configuration against the existing baseline.
6. Correctness and recovery: determinism, interruption, resume, unchanged-tree rerun.
Don't combine every optimisation immediately. Keep individual experiment results so you can attribute the gains.

For the combined benchmark, I'd preserve these comparisons:
- Existing best configuration.
- Keyframe-only.
- Keyframe + codec routing.
- Keyframe + codec routing + thread budget.
- Best configuration under Balanced with process throttling disabled.
Keep the High Performance run as a reference ceiling.

My overall assessment
This is the right next direction for RoamIngest.

Your previous experiments established that:
- GPU inference is not the bottleneck.
- GPU JPEG decoding is not worthwhile on this hardware.
- CPU and NVDEC can complement each other.
- Storage access patterns matter.
- The ingestion pipeline can already achieve substantial gains without requiring a Rust rewrite.
Now you're investigating something more fundamental: how to minimise unnecessary computation and I/O while maintaining retrieval quality.

One thing I'd protect against: don't let the project become an endless optimisation exercise. After this round, I'd aim to freeze a RoamIngest v0.1 with:
- Reliable heterogeneous media ingestion.
- Deterministic and resumable processing.
- Bounded RAM and VRAM.
- Configurable sampling policies.
- Codec-aware scheduling.
- A reproducible benchmark suite.
- Documented quality/performance trade-offs.
Then move to RoamEngine: incremental indexing, semantic retrieval, temporal queries, and geospatial search.

That's where the system starts becoming the travel-search product you originally envisioned.

The most important result from this next round won't be the biggest speedup. It'll be demonstrating that RoamIngest can intelligently choose how much media to process, based on the quality the downstream search system actually needs.