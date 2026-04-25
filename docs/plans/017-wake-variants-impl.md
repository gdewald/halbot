# 017 — LLM-generated wake-word variant dictionary

## Problem

Whisper mistranscribes the wake word ("robot") as homophones — "row bot",
"roebot", "rowbot" today; presumably others we haven't seen yet. Each
miss = the user said the wake word and Halbot ignored them.

Current matcher: hard-coded tuple in [halbot/voice_session.py:44](halbot/voice_session.py:44).
Adding a new variant requires a code edit and redeploy. Want: an admin
slash command that asks the LLM to enumerate phonetic / Whisper-typical
mishearings of the configured wake word, then persists the result so
the runtime matcher reads from it.

## Shape

### Slash command
Sub-command tree under existing `/halbot-admin`:

```
/halbot-admin wake-variants generate [word:<override>]
/halbot-admin wake-variants list
/halbot-admin wake-variants clear
/halbot-admin wake-variants add <token>
/halbot-admin wake-variants remove <token>
```

`generate` is the headline action: prompts LLM, returns JSON list of
variants, persists, replies in-channel with the proposed list +
old→new diff. No confirmation gate (admin-only command, reversible
via `clear` + regenerate).

### LLM prompt
~6-line system prompt asking for phonetic + whisper-typical mishearings
of the given word. Constraints in the prompt:
- 20–40 lowercase items
- include "<word>" itself first (so the canonical token is always present)
- prefer 1–3 word forms (matches our substring scanner; longer phrases
  rarely match)
- no homonyms-with-actual-meaning unless they're whisper-likely
  ("row bot" yes, "rowboat" yes, "rowing" no)
- return JSON: `{"variants": ["robot", "ro bot", ...]}`

### Storage
New sqlite table `wake_variants`:
```sql
CREATE TABLE wake_variants (
    token TEXT PRIMARY KEY,
    source TEXT NOT NULL,          -- 'llm' | 'manual' | 'seed'
    created_at INTEGER NOT NULL
);
```
Seeded on first daemon boot with the existing hardcoded tuple under
`source='seed'`. `generate` deletes prior `source='llm'` rows and
inserts the LLM output. `add`/`remove` write `source='manual'` rows.
`list` returns all rows. `clear` deletes everything except `source='seed'`.

### Runtime
[halbot/voice_session.py](halbot/voice_session.py):
- Drop the module-level constant `_WAKE_PREFILTER_TOKENS`.
- `_has_wake_candidate()` and `_extract_command()` read from a lazy
  in-memory cache populated from the `wake_variants` table.
- Cache invalidates on wake-variant table writes (slash command path
  flips an event to refresh).

### Files touched
- `halbot/db.py` — table schema + CRUD helpers (`wake_variant_list`,
  `wake_variant_replace_llm`, `wake_variant_add`, `wake_variant_remove`,
  `wake_variant_clear`).
- `halbot/voice_session.py` — drop constant, swap to cache lookup.
- `halbot/llm.py` — new helper `generate_wake_variants_async(word)`
  returning `list[str]`.
- `halbot/slash.py` (or wherever `/halbot-admin` lives) — sub-command
  group `wake-variants`.
- `halbot/voice.py` — bias prompt `WHISPER_INITIAL_PROMPT` could
  optionally be regenerated alongside (out of scope unless we want to).

## Open questions

1. **Wake word source of truth.** Right now `WAKE_WORD = "robot"` is a
   constant in [voice.py:146](halbot/voice.py:146). CLAUDE.md mentions a
   `voice_wake_word` config field, but it isn't wired (drift). Two
   options: (a) plumb the config field first so `generate` reads from
   it, OR (b) keep "robot" hardcoded and let `generate` accept an
   override arg. Cheaper: (b) — defer config plumbing.
2. **Replace vs merge.** `generate` could (a) replace the entire dict
   with LLM output, OR (b) keep the seed/manual rows and only swap the
   `source='llm'` slice. (b) is safer — a bad LLM run won't break wake
   detection because the seed tokens stay live. Recommend (b).
3. **Cache invalidation strategy.** Simplest: read table once on every
   `_has_wake_candidate()` call (sqlite is microseconds, list is
   bounded ~50 rows). Skip the cache entirely. OK?
4. **LLM model.** Use the configured `llm_model` (gemma4:e4b)? It's
   small but should handle this prompt fine. Fall back to a manual
   list on LLM failure?
5. **Slash command output.** Reply in-channel with the full dict, or
   ephemeral DM the admin? Discord channel pollution vs visibility for
   other admins.

## Non-goals

- Acoustic / phonetic matching at the audio level. Substring scan over
  Whisper output stays.
- Per-guild custom wake words (we have one server).
- Real-time A/B comparing wake-detection hit rate. Could add as a
  separate analytics event later (`wake_match` with `matched_token`
  meta) but out of scope here.
- Whisper bias prompt regeneration. Stays as the hand-written line.
