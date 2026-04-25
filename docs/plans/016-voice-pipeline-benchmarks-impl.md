# Plan 016 (draft) — Voice-pipeline benchmarks

**Goal:** systematic, reproducible measurement of the STT → LLM → TTS
pipeline so we can (a) pin a baseline number, (b) compare models and
configs head-to-head, (c) detect regressions over time. Answers
questions like "does `large-v3` cost us 400 ms over `large-v3-turbo`?",
"is beam=5 worth it?", "which kokoro voice synth-es fastest?", "gemma3
vs qwen2.5 for `answer_voice_conversation`?".

Scaffold already landed in commit `a757a47` — this plan fills it in.

## Relation to plan 015

015 has an L2 bench (`scripts/bench_voice.py`) — a one-shot waterfall
for **one** config, feeding a single bench number into deploy gating.
016 is the orthogonal axis: a **sweep** harness that fixes everything
except one variable and emits comparison tables across N scenarios.
Same timing primitives, different product. If 015 ships first, 016
reuses its `transcribe()` timing hooks; if 016 ships first, 015's L2
becomes `benchmarks run baseline --iters 10`.

## Problem

1. **No baseline.** "Voice feels slow" is our only SLA. We cannot say
   if a change made it 50 ms or 2 s worse without manual stopwatching.
2. **Model choices are guesses.** `large-v3-turbo`, `beam_size=1`,
   `gemma3:12b`, `kokoro/af_heart`, cpu TTS — each was a local
   decision. No single place shows the latency/quality tradeoff.
3. **Config drift goes unnoticed.** Yesterday's `num_predict=512` cap
   (commit `1d3f2a8`) was tuned by eye. A benchmark would have shown
   the exact p95 hit from the cap and flagged if it regressed completion
   quality.
4. **Comparing configs is N×M hand-timed experiments.** Needs harness.

## Shape

### Scenario model

Already in `benchmarks/runner.py`:

```python
Scenario(
    name="whisper-turbo-beam1",
    pipeline=["stt"],                           # isolate one stage
    stt={"model": "large-v3-turbo", "compute_type": "float16",
         "beam_size": 1, "device": "cuda"},
    inputs=[Path("benchmarks/corpus/voice/short-01.wav"), ...],
    warmup=1, iterations=10,
)
```

- `pipeline` lets the same harness bench a single stage in isolation
  (`["stt"]`) or the full chain (`["stt", "llm", "tts"]`). Stage
  output feeds next stage input when chained; otherwise each stage
  gets input pulled from the corpus directly (llm gets a prompt
  string, tts gets a text string).
- Per-stage `config` dict is a thin passthrough: the stage wrapper
  translates keys into the underlying library's kwargs. One schema
  fits all models by keeping keys generic (`model`, `beam_size`,
  `compute_type`, `voice`, `num_predict`, `temperature`, ...).

### Stage wrappers (`benchmarks/stages.py`)

- `run_stt(audio, config)` → call `WhisperModel(...)` directly, NOT
  `halbot.voice.transcribe()`. Harness needs to vary `model` /
  `device` / `compute_type` / `beam_size` per scenario, which the
  production path doesn't expose.
- `run_llm(prompt, config)` → POST to ollama at
  `http://localhost:11434/api/chat` with the scenario's `model`,
  `num_predict`, `temperature`, `format`. For realistic-prompt runs,
  route through `halbot.llm.answer_voice_conversation` with model
  override (gives us the real prompt size + JSON-mode path).
- `run_tts(text, config)` → subclass-dispatch on `config["engine"]`:
  `KokoroEngine(voice=..., lang=..., speed=...)`. If/when we add
  coqui/xtts/piper, same wrapper, new branch.

Each wrapper captures:

- `wall_ms` measured around the blocking call with `time.perf_counter`.
- `model_load_ms` on first call (cold vs warm separation — warmup
  run eats this).
- Stage-native telemetry: whisper returns `info.language_probability`;
  ollama response has `eval_count` / `prompt_eval_count` /
  `total_duration`; kokoro gives us output samples. Stash in
  `StageTiming.extra`.

### Corpus (`benchmarks/corpus/`)

- `voice/` — 5–10 short wav clips (16 kHz mono, <10 s each). Mix:
  clean "robot list sounds", noisy "robot say hi", wake-only, no-wake
  command, edge-case long sentence. Committed — tiny, reproducibility
  wins over size.
- `prompts/` — captured real prompts from `halbot.log`
  (`[voice-llm] stage=request messages=...`) across a range of
  history sizes and intents. Hand-picked, 10-20 entries.
- `texts/` — TTS inputs of varying length: 5, 20, 50, 100 words.
  Lets us see per-char-ish cost curves.

### Runner (`benchmarks/runner.py`)

- `run_scenario`: warmup iterations discarded; measured iterations
  emit `IterationResult` per input×iteration. Aggregate into
  `ScenarioResult.summary`: per-stage {mean, p50, p95, stdev} plus
  total wall.
- `run_suite`: sequential (not parallel — GPU contention would
  poison numbers). Loads/unloads models between scenarios when the
  `{engine,model,compute_type}` tuple changes; skips unload when
  adjacent scenarios share the same model.
- **Isolation discipline.** Between scenarios: `torch.cuda.empty_cache()`,
  force GC, short sleep. Log NVIDIA memory pre/post via `pynvml`
  (already available — nvidia-cublas is installed).

### Scenarios (`benchmarks/scenarios.py`)

Registry pattern: `all_scenarios() -> list[Scenario]`. Organized
thematically:

- **Baseline** — mirrors production exactly. The number we compare
  everything against.
- **STT sweep** — baseline with one STT knob varied at a time: model
  (`large-v3`, `large-v3-turbo`, `medium`, `small.en`), beam
  (1, 3, 5), compute (`float16`, `int8_float16`, `int8`),
  device (`cuda`, `cpu`).
- **LLM sweep** — model (current prod vs qwen2.5:7b vs llama3.2:3b),
  `num_predict` (256, 384, 512, 1024), `temperature`, JSON-mode vs
  free-form (tests both voice paths).
- **TTS sweep** — voice (`af_heart` vs others), text length
  (5/20/50/100 words), cpu vs gpu (if we ever port kokoro to gpu).
- **Full-pipeline** — end-to-end, each STT winner × each LLM winner
  × each TTS winner. Small cartesian, tells us what production
  should actually be.

### Results (`benchmarks/results.py`)

- JSONL per scenario: one row per iteration, all `StageTiming` fields
  flattened. Easy to re-aggregate or pipe into pandas.
- `summary.json` per run: scenario → {n, mean, p50, p95, stdev} for
  total + each stage.
- `render_markdown(results)` → a table that drops into commit
  messages / PR descriptions:

  ```
  | scenario             | stt p50 | llm p50 | tts p50 | total p50 | vs baseline |
  |----------------------|---------|---------|---------|-----------|-------------|
  | baseline             |   410ms |  1180ms |   720ms |    2310ms |          +0 |
  | stt:large-v3-beam1   |   820ms |  1170ms |   715ms |    2705ms |       +395  |
  | llm:qwen2.5:7b       |   415ms |   640ms |   710ms |    1765ms |       -545  |
  ```

- `compare(a, b)`: diff two result files, flag regressions past a
  threshold (`p95 > baseline_p95 * 1.1`).

### CLI (`benchmarks/__main__.py`)

```
uv run python -m benchmarks                         # baseline only
uv run python -m benchmarks --list                  # dump registry
uv run python -m benchmarks stt-sweep               # one suite
uv run python -m benchmarks full                    # everything (~30 min)
uv run python -m benchmarks --compare baseline.json candidate.json
```

## Files touched

- `benchmarks/__init__.py` — already scaffolded
- `benchmarks/__main__.py` — real CLI dispatch
- `benchmarks/runner.py` — `run_scenario`, `run_suite`, aggregation
- `benchmarks/stages.py` — real `run_stt`/`run_llm`/`run_tts`
- `benchmarks/scenarios.py` — `baseline()` + sweep builders
- `benchmarks/results.py` — JSONL writer, markdown render, compare
- `benchmarks/corpus/voice/*.wav` — 5–10 clips, <2 MB total
- `benchmarks/corpus/prompts/*.json` — captured prompts
- `benchmarks/corpus/texts/*.txt` — TTS inputs
- `benchmarks/_out/` — gitignored, already added
- `pyproject.toml` — new `benchmarks` optional-dep group:
  `pynvml`, `tabulate` (or handrolled). Reuses daemon group for
  `faster-whisper` + `kokoro` + `requests` + `soundfile`.

## Non-goals

- CI integration. Runs on the dev box, on demand.
- Quality / accuracy metrics. Latency only. WER / BLEU / audio
  quality is a separate plan — put a TODO pointer in the summary
  output so we remember.
- Multi-machine / cloud runs. Single-box, single-GPU.
- Concurrency / lock-contention benchmarks — that's 015's L2
  `--concurrency N`, don't duplicate.
- Deploy gating. 016 is a research tool, not a guard. If we want
  gating, 015 L2 is the spot.
- Multi-user scenarios (Discord-side queueing).

## Decisions (resolved 2026-04-24)

1. **CLI shape:** sub-commands (`benchmarks stt-sweep`, `benchmarks
   llm-sweep`, ...). `benchmarks --list` still works.
2. **Corpus:** commit wavs under `benchmarks/corpus/voice/`. Tiny,
   reproducible, no regen dance.
3. **LLM prompts:** captured from `halbot.log`, no PII redaction —
   private server, not in scope. Copy lines verbatim into
   `benchmarks/corpus/prompts/*.json`.
4. **Warmup count:** 1 everywhere. Ollama runs `OLLAMA_MAX_LOADED_
   MODELS=1` by default — one warmup absorbs the swap-in cost when
   a scenario changes model. Whisper same (lazy-load on first
   transcribe). No scenarios need 2.
5. **Accuracy:** log transcripts + LLM completions + TTS output-
   sample-count next to timings. No WER / audio quality this plan.

## Rough order if approved

1. `stages.run_stt` + baseline STT scenario + JSONL writer. Proves
   the shape end-to-end with one stage.
2. `run_llm` + llm-sweep — answers the "gemma vs qwen" question
   we've been punting on.
3. `run_tts` + tts-sweep — fastest wins here don't block anything,
   lowest priority.
4. Full-pipeline chained scenarios + markdown report.
5. `compare()` + a convention for committing `baseline.json` under
   `benchmarks/_baselines/` so "vs main" diffs are one command.
