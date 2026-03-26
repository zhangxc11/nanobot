# ASR Improvement Changelog

## [Unreleased] — 2026-03-26

### nanobot core (`nanobot/asr/registry.py`)

- **feat**: Always log ASR subprocess stderr at INFO level. Previously, only
  stderr containing "WARNING" was logged (at warning level); all other output
  was silently dropped at debug level. This hid retry attempts and engine
  selection messages from normal operation.

### feishu-parser skill (`scripts/feishu_parser.py`, `scripts/asr.py`)

- **feat**: Add retry logic to `transcribe_feishu()` — max 2 retries with 2s
  interval for HTTP 5xx errors, network exceptions (ConnectionError, Timeout),
  and empty text responses. Successful requests have zero added latency.
  `transcribe_feishu` now returns `(text, retry_count)` tuple.

- **feat**: Disable forced on-device recognition in `transcribe_local()`.
  Experiment showed on-device mode misses the first ~5s of longer audio
  (43s clip: 63 chars on-device vs 113 chars server, +79% content).
  Default is now system-chosen (typically server-based). On-device can be
  forced via `NANOBOT_ASR_ON_DEVICE=1` environment variable.

- **feat**: Add diagnostic fields to `do_recognize()` JSON output:
  `retry_count`, `fallback_used`, `engines_tried`, `audio_duration_ms`.
