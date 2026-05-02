# 024 — Dashboard stats fixes

## Problem

Dashboard latency cards (STT / TTS / LLM) display numbers that
disagree with reality by 50-200×. Three independent root causes:

1. **TZ off-by-7h.** `analytics.py:455` `t_today = now - (now % 86400)`
   computes UTC midnight, comment claims local. User in PDT → "today"
   boundary lands at 5pm previous local day. All `count_today` fields
   wrong window.
2. **Mean dominated by cold-load tail.** `_latency_bundle` returns
   arithmetic mean over 30-day sample. STT recent reality: avg 251 ms
   (last 50). Dashboard: 52 928 ms. Whisper warm-up spikes (max
   360 s), TDR-recovered LLM calls (max 122 s), Kokoro cold loads
   (max 182 s) blow out the mean. Median is robust; mean is not.
3. **`parse_voice_intent.latency_ms` measures wrong span.**
   `voice_session.py:586` sets `_llm_t0` *before*
   `await asyncio.to_thread(parse_voice_intent, …)`. Recorded
   latency includes thread-pool queue wait + Discord
   `fetch_soundboard_sounds` + `db_list` + prompt build, on top of
   actual HTTP round. The 4.1 s wake-gap from the 20:43:00 turn
   showed up as inflated `latency_ms`, not as a separate
   non-LLM-cost. Field name lies about what it measures.

## Shape

### Fix 1 — local-midnight `t_today`

```python
# analytics.py:455
import time as _t
lt = _t.localtime(now)
t_today = int(_t.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))
```

Mechanical. No proto change. No frontend change.

### Fix 2 — show median, not mean

Add `p50_ms` field. Keep `avg_ms` (arithmetic mean) for power users
who want to see the long tail; surface **p50** as the headline
number. Frontend renders p50 with sub-label “median (p95: NN)”.

Proto change: add `int32 p50_ms = N;` to each of `StatsReply.Stt`,
`StatsReply.Tts`, `StatsReply.Llm`. `_latency_bundle` already sorts
the sample — compute and return `p50_ms` alongside.

```python
# analytics.py _latency_bundle return
return {"avg_ms": int(avg), "p50_ms": int(_percentile(vals, 50)),
        "p95_ms": int(_percentile(vals, 95)),
        "count_today": count_today}
```

Frontend: `LatencyCard` swaps `avg=` to `p50=`; sub stays "30 d
sample · N today".

### Fix 3 — split `latency_ms` for parse_voice_intent

Two timestamps, two fields:

- `total_ms` (existing semantics: wake → action returned) — keep,
  rename in meta only.
- `llm_ms` (new): measured *inside* `parse_voice_intent` from
  `_t_enter` (already added in turn-1 instrumentation) to
  `requests.post(...)` return. Stash into `_stats_out["llm_ms"]`
  and surface in `analytics.record(...)` as the canonical latency
  the LLM panel should use.

`compute_dashboard_stats` for the LLM panel reads
`json_extract(meta_json,'$.llm_ms')` when present, falls back to
`latency_ms` for old rows. (No backfill — old rows stay; new rows
are correct.)

Same trick for tts_request: split `cold_load_ms` (kokoro+torch
import + pipeline construct) from `synth_ms` (generate+encode).
TTS panel "Full render time" should read `synth_ms`. Cold-load
spike gets its own card or is hidden.

## Files touched

- `proto/mgmt.proto` — three `int32 p50_ms` fields under
  `StatsReply.Stt|Tts|Llm`. Maybe `synth_ms` / `cold_load_ms` for
  tts.
- `halbot/_gen/*` — regenerated via `scripts\gen_proto.ps1`.
- `halbot/analytics.py` — `t_today` fix, `_latency_bundle` returns
  p50, `compute_dashboard_stats` plumbs new fields.
- `halbot/voice_session.py` — read `_stats_out["llm_ms"]`, record
  as `llm_ms` (today’s instrumentation already captures
  `build_ms`; just stop the clock around `requests.post`).
- `halbot/llm.py` — `parse_voice_intent` writes `llm_ms` into
  `_stats_out` (one extra `_time.monotonic()` pair around
  `requests.post`).
- `halbot/tts.py` — `KokoroEngine.synth` returns `cold_load_ms`
  metadata; caller computes `synth_ms = total_ms - cold_load_ms`
  and records `synth_ms` only.
- `frontend/src/panels/Stats.jsx` — `LatencyCard` switches to
  `p50_ms`; relabel sub-text. TTS panel renders `synth_ms` as
  "Synth latency" (no cold-load card).
- `frontend/src/panels/stats/HealthBanner.jsx` — same swap.

## Decisions (resolved)

1. **Headline = p50.** Drop trimmed-mean / trailing-N.
2. **Keep `avg_ms`** in proto alongside `p50_ms`.
3. **No backfill.** Old rows show `—` for `llm_ms` / `synth_ms`;
   30 d rolls past in time.
4. **DELETE `parse_voice_combined`** rows from events.db one-time
   (`DELETE WHERE target='parse_voice_combined'`).
5. **TTS subtract.** One `synth_ms` field = `total_ms - cold_load_ms`.
   No separate cold-load card.
6. **Commits per topic, no PR.** Stage: proto → analytics →
   instrumentation → frontend → db cleanup.

## Non-goals

- No retention-policy changes.
- No new event kinds.
- No alerting / threshold work (stays for HealthBanner pass).
- No replacement of stats publisher (R2 snapshot uses same shape;
  fixes propagate automatically).

## Verification

1. Unit-level: `_latency_bundle` regression test on a fixture
   sqlite with mixed cold/warm latency rows; assert p50 within
   ±10 % of expected.
2. Live: deploy daemon, refresh dashboard, compare:
   - Last 50 STT computed by ad-hoc python (251 ms today)
   - Dashboard p50 STT (should land in same ballpark, ±20 %).
3. Run Playwright suite under `tests/dashboard` after frontend
   change to confirm panels render with new fields.
