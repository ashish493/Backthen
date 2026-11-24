# ADR-007: Opt the ingestion process out of OS power throttling by default

Status: accepted · 22 Nov 2026

**Evidence** (`EXP_POWER.md`).
- On the Balanced plan, default throttling took 148.9 s; with process throttling off it took 46.2 s.
- On High Performance, 5 of 6 default runs were throttled (105–113 s). With throttling off, all ran at 48–50 s.
- High priority added nothing (46.0 s).

**Decision.** `--throttle off` (Windows `ProcessPowerThrottling`, EcoQoS opt-out) by default, normal priority. A flag restores Windows' default, for users who prefer quiet background ingestion. Users are never asked to change their power plan.
**Revisit** after RoamIndex adds a "background mode" setting; the desktop app should expose this choice to the user.
