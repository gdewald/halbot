# Plan 015 (REFERENCE) — Voice pipeline tests + latency harness

> **STATUS: REFERENCE — not implemented as designed.**
> Plan 016 (`benchmarks/` harness) shipped in lieu of L2/L3 here, and
> the post-mortem replay equivalent is `scripts/extract_transcripts.py`.
> The L0/L1 pure-unit and component-level test scaffolding sketched
> below was never built. Kept here as a starting point if/when we
> want a `tests/` tree.

**Goal:** stop diagnosing voice slowness by squinting at `halbot.log`.
Give us (a) cheap unit coverage of the bits that keep breaking
silently, (b) a one-command end-to-end latency harness that prints a
stage waterfall, and (c) a log post-processor so we can run the
waterfall against real production transcripts after the fact.

**Non-goals:** full Discord mock, real voice-WebSocket test, CI
infrastructure. Single-machine, run-on-demand from a dev shell.

## The three problems this solves

1. **Silent regressions in pure logic.** Last week the wake-word gate
   stopped firing after an unrelated refactor because `_fire_text_triggers`
   moved above the wake check (or didn't). A 40-line unit test file
   would have caught it in 50ms.
2. **"It feels slow" with no numbers.** User has no way to answer
   "where did the 15 seconds go?" short of timestamp-grepping logs.
3. **No pre-deploy smoke.** Today, post-deploy verification = "join
   voice and say robot list". If ollama is backlogged or whisper
   couldn't load we find out live.

## Layers

### L0 — Pure unit (fast, always-on)

`tests/unit/test_wake_gate.py`

- `_has_wake_candidate` truth table: exact match, embedded in
  sentence, all variants, empty, noise-only.
- `_extract_command`: token stripping, punctuation, earliest-match
  rule, no-wake returns original.
- `voice_session._fire_text_triggers` ordering: invariant that it
  runs before mention check (structural: grep the function for the
  two anchor comments and assert the line numbers are in the right
  order; brittle but exactly matches how it breaks).

`tests/unit/test_intent_parse.py`

- Feed `parse_voice_intent` canned ollama JSON via `responses` /
  `requests-mock` and assert the shape of the actions list for each
  happy path and each failure mode (empty content, `<think>` wrap,
  reasoning_content fallback, 503 → retry).

### L1 — Pipeline integration (medium, run-on-demand)

`tests/integration/test_voice_pipeline.py`

- Stubs `transcribe()` to return a fixed string (skip whisper entirely).
- Stubs `requests.post` against ollama with canned JSON per scenario.
- Drives `handle_voice_command(fake_guild, user_id, transcript)`
  directly.
- Scenarios: wake-only no command, wake + list, wake + refuse,
  trigger without wake, mention without wake.
- Asserts side-effects observable via a recording sink (captures what
  `send_halbot_reply` would have sent; no Discord).

### L2 — Latency harness (manual, real backends)

`scripts/bench_voice.py`

- Loads a short wav from `tests/fixtures/voice/*.wav` (ship 2-3
  clips: "robot list sounds", "robot say hi", ambient noise).
- Runs the real `transcribe()` on real whisper (optionally loading
  the model first to exclude cold-start).
- Runs the real `parse_voice_intent` against real ollama.
- Prints a stage waterfall:

  ```
  [bench] whisper load        2.4s (cached: 0.02s)
  [bench] whisper transcribe  0.41s
  [bench] wake gate           0.00s
  [bench] intent parse        1.18s
  [bench] TOTAL cold          3.99s
  [bench] TOTAL warm          1.59s
  ```

- Flags: `--concurrency N` for simulating N users talking at once,
  `--iters M` for warm-run averaging.
- Writes last run to `dist/.bench-voice.json` so deploy.ps1 can
  optionally block on regression.

### L3 — Log replay (runs against prod halbot.log)

`scripts/replay_voice_log.py`

- Parses `halbot.log` for `[voice-cmd] stage=begin`, `[stt] user=…
  (Xs):`, `[voice-llm] finish_reason=… usage=…`, `[tts] stage=synth-
  done`, `[play] stage=play-dispatched` lines.
- Groups into per-utterance turns by (user_id, monotonic proximity).
- Prints a waterfall like bench_voice.py but sourced from real events.
- Output option `--top-slow 10` to surface the worst outliers.

## Files touched

- `tests/unit/test_wake_gate.py` (new)
- `tests/unit/test_intent_parse.py` (new)
- `tests/integration/test_voice_pipeline.py` (new)
- `tests/fixtures/voice/*.wav` (new, 3 clips, <500 KB total)
- `tests/conftest.py` (new — stub factories)
- `scripts/bench_voice.py` (new)
- `scripts/replay_voice_log.py` (new)
- `pyproject.toml` — add `pytest`, `pytest-asyncio`, `responses`
  under a `test` optional-dependency group; no runtime dep changes.

## Open questions

1. **Does L1 need real audio bytes, or is stubbing `transcribe()`
   enough?** Stubbing is 100x faster and covers everything except
   whisper itself; L2 covers whisper. I'd stub.
2. **Do we commit the wav fixtures or gitignore and generate on
   demand from a TTS round-trip?** Committing is simpler and the
   files are tiny; regenerating makes the repo reproducible on a new
   machine. Leaning commit.
3. **L2: should `--concurrency N` share one whisper model (current
   prod shape) or spin one per user (alt design)?** Current prod
   shares + `_transcribe_lock` serializes. If we want to test lock
   contention this needs to be the default.
4. **Should `scripts/deploy.ps1` gate on L2?** A ~5s bench before
   every deploy is nice; a 5s bench that's flaky because ollama is
   cold is annoying. Probably opt-in via `-Bench`.
5. **pytest or unittest?** Project has no existing test infra; pytest
   is the lower-friction default, but it's a new top-level dep.

## Rough order if we approve

1. L0 unit tests (half a day) — instant value, blocks further
   wake-gate silent breakage.
2. L3 log replay (half a day) — answers "where did the 15 seconds
   go?" against the logs we already have, no fixtures needed.
3. L2 bench harness (half a day) — now we have baseline numbers to
   regress against.
4. L1 pipeline tests (1 day) — most scaffolding, least immediate
   value; do last.
