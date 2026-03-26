# ASR Improvement Requirements

## Background

Experiment results (2026-03-26) from `~/.nanobot/workspace/data/analysis/asr-improvement/asr_experiment_analysis.md`
identified three actionable improvements to the ASR pipeline.

---

## Change 1: Feishu ASR Retry Logic

**File**: `feishu-parser/scripts/feishu_parser.py` — `transcribe_feishu()`

**Why**: The Feishu ASR API is a remote HTTP service subject to transient failures
(server errors, network blips, occasional empty responses). A single failure currently
causes immediate fallback to local ASR, which is less accurate for longer audio.

**What**: Add retry logic (max 2 retries, 2s interval) for:
- HTTP 5xx server errors
- Network exceptions (ConnectionError, Timeout)
- Empty text response (API returns code=0 but recognition_text is empty)

**Acceptance criteria**:
- Successful requests have zero added latency (no sleep on success path)
- Non-retryable errors (4xx, credential failures) are not retried
- Retry count is tracked and surfaced in diagnostic output
- Existing outer try/except structure is preserved

---

## Change 2: Disable Forced On-Device ASR

**File**: `feishu-parser/scripts/feishu_parser.py` — `transcribe_local()`

**Why**: Experiment showed that forcing on-device recognition causes the model to miss
the first ~5 seconds of longer audio clips:
- 43s clip: 63 chars (on-device) vs 113 chars (server) — +79% content with server mode
- For shorter clips there is no regression
- Speed is identical in both modes

**What**: Remove the forced `setRequiresOnDeviceRecognition_(True)` call. Let the system
choose (typically uses server-based recognition). Provide an escape hatch via
`NANOBOT_ASR_ON_DEVICE=1` env var for offline use.

**Acceptance criteria**:
- Default behavior: system chooses recognition mode (no forced on-device)
- `NANOBOT_ASR_ON_DEVICE=1` restores the old forced on-device behavior
- Env var usage is logged at INFO level

---

## Change 3: Improve Logging and Diagnostics

### Part A: registry.py (nanobot core)

**File**: `nanobot/asr/registry.py`

**Why**: The current logic only logs ASR stderr at WARNING level if it contains "WARNING",
otherwise hides it at DEBUG. This means INFO-level diagnostic messages (retry attempts,
engine selection) from the ASR subprocess are invisible in normal operation.

**What**: Always log ASR stderr at INFO level.

**Acceptance criteria**:
- All ASR subprocess stderr output is visible at INFO log level
- No diagnostic messages are silently dropped

### Part B: asr.py diagnostics (feishu-parser)

**File**: `feishu-parser/scripts/asr.py` — `do_recognize()`

**Why**: The JSON output currently only includes `recognition`, `engine`, and `duration_ms`.
Adding diagnostic fields enables better observability and post-hoc debugging.

**What**: Add to JSON output:
- `retry_count`: number of retries performed by Feishu ASR
- `fallback_used`: whether local ASR fallback was triggered
- `engines_tried`: list of engines attempted in order
- `audio_duration_ms`: the input audio duration (passed via `--duration`)

**Acceptance criteria**:
- All new fields present in success output
- `transcribe_feishu` returns `(text, retry_count)` tuple
- Callers handle both old and new return format for safety
