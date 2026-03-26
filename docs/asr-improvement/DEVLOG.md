# ASR Improvement Development Log

## 2026-03-26 — Implementation start

**Branch**: `feature/asr-improvement` (both repos)

**Experiment basis**: `~/.nanobot/workspace/data/analysis/asr-improvement/asr_experiment_analysis.md`

Three changes identified from experiment results:

1. **Feishu ASR retry logic** — transient failures (5xx, network, empty response) currently
   cause immediate fallback to local ASR. Adding max-2-retry with 2s interval improves
   reliability with zero latency cost on the happy path.

2. **Disable forced on-device recognition** — experiment showed on-device mode misses the
   first ~5s of longer audio (43s clip: 63 chars on-device vs 113 chars server, +79%).
   Removing the forced flag lets the system choose server-based recognition by default.
   Escape hatch: `NANOBOT_ASR_ON_DEVICE=1`.

3. **Logging/diagnostics** — ASR subprocess stderr was only surfaced at WARNING level if
   it contained "WARNING", hiding INFO-level diagnostics. Promoting all stderr to INFO
   in `registry.py`. Also adding diagnostic fields to the JSON output in `asr.py`
   (`retry_count`, `fallback_used`, `engines_tried`, `audio_duration_ms`).

Implementation completed same day. See CHANGELOG.md for details.
