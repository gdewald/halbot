# 014 — Discord embed + component flows (mockup v3 integration)

Implementation record. Approved 2026-04-22. Supersedes draft.

## Goal

Adopt the v3 mockup's visual grammar — [docs/mockups/discord_interactions/](../mockups/discord_interactions/) —
across every user-facing Discord surface. Preserve the bot's spirit: LLM authors the voice of every
reply *and* decides whether persona allows the action at all. Structured fields (IDs, counts, kinds)
stay neutral; italic subtext + embed description carry LLM output.

## Decisions locked

| # | Pick | Why |
|---|---|---|
| Q1 Admin surface | **Slash only.** `/halbot-admin status\|deleted\|undelete\|undelete-all\|panic\|purge`. Retire `!halbot admin` prefix. | Ephemeral default, native autocomplete, owner gate via `default_member_permissions=0`. Prefix plaintext retired. |
| Q2 Voice-card destination | **`VoiceChannel.chat`** (Discord voice-chat since 2023). Fallback to guild system channel if voice-chat disabled. | Native, zero config, cards render inline with voice activity. |
| Q3 Persona scope | **Per-user default + `scope` column** (`"user"` \| `"guild"`). "Make guild-wide" button promotes row. | Matches Discord profile-bound mental model; additive schema change. |
| Q4 Progress-edit backup | **Skip.** v3 dropped it deliberately. | No flow to mirror. Revisit if backup UX respecced. |
| Q5 Subtext + persona enforcement | **Always LLM-customize** (single call returns `(subtext, body)` tuple). Typing indicator hides latency. Persona can **refuse** actions via new `refuse` intent. | Users want persona fully in-character, including refusals. `async with channel.typing()` covers long calls. |

## Shape

### Central helper

```python
# halbot/bot_ui.py (new)
class Mode(StrEnum):
    SOUNDBOARD = "soundboard"; SAVED = "saved"; TRIGGER = "trigger"
    ADMIN_STATUS = "admin/status"; ADMIN_DELETED = "admin/deleted"
    ADMIN_UNDELETE = "admin/undelete"; ADMIN_PANIC = "admin/panic"
    PERSONA_SAVED = "persona saved"; PERSONA_ACTIVE = "active persona"
    NOTED = "noted"; GRUDGE_LEDGER = "grudge ledger"
    REFUSED = "persona declined"; DENIED = "permission denied"
    WAKE = "wake"; VOICE_TRIGGER = "voice trigger"

COLORS = {  # oklch → 24-bit
    "amber": 0xE8B15C, "good": 0x7CCFA0, "warn": 0xE8A361,
    "bad": 0xD55D48, "violet": 0xB88AD0, "cyan": 0x7EC3D8,
}

async def send_halbot_reply(
    dest, *, mode, color, title,
    description=None,          # persona-voiced LLM body
    subtext=None,              # italic lead-in, LLM or templated
    fields=(), footer=None,
    view=None, ephemeral=False,
) -> discord.Message: ...
```

### Persona-gated intent

Extend `parse_intent` schema. Persona may refuse any action:

```json
{"action": "refuse", "reason": "<in-character one-liner>"}
```

System prompt gains:
> You may decline user requests that conflict with your active persona.
> Return `{"action": "refuse", "reason": "<short in-character>"}`.
> Never refuse owner admin commands.

Refusal renders `Mode.REFUSED` embed, `warn` color, zero side-effects. Analytics event:
`hook_fired` with `target=persona.refuse` + `reason`.

### Typing indicator

Every mention/reply handler wraps work in `async with message.channel.typing():` —
auto-refreshes until `send_halbot_reply` exits the block. Single line, covers both LLM
roundtrips (intent + customize).

### Persistent views

`halbot/interactions.py` houses `discord.ui.View` subclasses. All registered via
`client.add_view(...)` on `on_ready` for cross-restart button survival. Owner-only views
override `interaction_check` → `interaction.user.id == guild.owner_id`.

### LLM customization

Single call returns tuple — piggybacks existing `customize_response_async`:

```python
# halbot/llm.py
async def customize_response_async(
    raw: str, *, context: str = "", persona: str | None = None,
) -> tuple[str, str]:
    """Return (subtext, body). Subtext = one-line intent-resolution italic.
    Body = persona-voiced reply (full markdown allowed)."""
```

Prompt update in `halbot/prompts/response_customization_prompt.txt`: instruct model to emit JSON
`{"subtext": "...", "body": "..."}`. Structured-output mode on Ollama if supported; else parse
loosely. Template fallback on parse fail: `subtext = f"*Intent: {mode}*"`, `body = raw`.

## Files touched

- `halbot/bot_ui.py` **(new)** — `Mode`, `COLORS`, `send_halbot_reply`, fenced-code table
  formatter, common-footer builder (persona-active line).
- `halbot/interactions.py` **(new)** — views: `SoundboardActionsView` (Stop/Replay/Louder),
  `AdminStatusView` (View deleted / Undelete / Panic / help),
  `UndeleteView` (embed + StringSelect ≤25 + "undelete all" success button),
  `PanicConfirmView` + `PanicModal` (code word `PANIC` equality check, reason field for
  audit log), `TriggerActionsView` (Mute / See triggers link), `PersonaActionsView`
  (Edit / Make guild-wide / Remove), `GrudgeForgiveView` (≤5 Forgive buttons; ≥6 →
  `GrudgeForgiveSelect`), `FactNotedView`.
- `halbot/slash.py` **(new)** — `@app_commands` group `/halbot-admin` with subcommands.
  `default_member_permissions=0` + `dm_permission=False`. Register on `on_ready` via
  `tree.sync(guild=...)`.
- `halbot/bot.py` — rewire outbound sends:
  - mention path (~653+): wrap in `typing()`, route through `send_halbot_reply`.
  - `_fire_text_triggers` (509): reply branch uses `Mode.TRIGGER` embed.
  - `on_voice_channel_effect` (227): optional effect card.
  - delete `_handle_admin_command`, `_admin_send`, `ADMIN_PREFIX` (→ slash).
- `halbot/voice_session.py` — post wake-card (`Mode.WAKE`, amber, fenced transcript) +
  TTS-reply card on successful wake; voice-trigger card (`Mode.VOICE_TRIGGER`, violet)
  on keyword_voice fire. Target = `VoiceChannel.chat` if text-enabled else
  `guild.system_channel`. New helper: `voice_log_dest(vc) → Messageable`.
- `halbot/llm.py` —
  - `parse_intent` + `parse_voice_command`: accept/route `refuse` action.
  - `customize_response_async` signature change → `tuple[str, str]`.
  - Persona block gets refusal-permission clause.
- `halbot/prompts/system_prompt.txt`, `response_customization_prompt.txt`,
  `voice_command_prompt.txt` — refusal grammar + subtext/body JSON.
- `halbot/db.py` — `personas` table: `ALTER TABLE personas ADD COLUMN scope TEXT NOT NULL
  DEFAULT 'user'`. Migration idempotent (check `pragma table_info`).
- `halbot/config.py` — `halbot_avatar_url: str = ""` (author icon). No
  `voice_log_channel_id` — auto-resolve.
- `halbot/analytics.py` — ensure `trigger_fire_count(tid)` query for trigger embed field.

## Phases (one PR each)

1. **Skeleton + soundboard** — `bot_ui.py`, `interactions.py` stub,
   `customize_response_async` tuple return, mention + upload flows (01 + 02).
   Typing indicator wiring. Sanity test: `@Halbot play X` emits amber embed + buttons.
2. **Persona refusal** — `refuse` intent, `Mode.REFUSED` embed, prompt updates,
   analytics event. Test: persona "grumpy night-shift DJ" refuses absurd requests.
3. **Slash admin** — `/halbot-admin *` slash tree, flows 06 + 07 + 08.
   Persistent `UndeleteView`, `PanicModal`. Retire `!halbot admin` prefix handlers.
4. **Triggers + persona + facts + grudges** — flows 03, 09, 10. `personas.scope`
   migration. `GrudgeForgiveView` with select-menu fallback ≥6.
5. **Voice cards** — flows 04, 05. `voice_log_dest` helper.
   Wake + trigger cards.
6. **Polish** — avatar config wired, persona-active footer fire count, "See triggers"
   link → dashboard deeplink, fenced-code table formatter edge cases (empty tombstones,
   overflow trimming).

## Non-goals

- Chrome fidelity (Discord renders its own UI).
- Dashboard-side trigger CRUD beyond link button destination.
- Opt-out/consent surfaces (CLAUDE.md privacy policy).
- TTS rendering of embeds (voice path stays plain-text-into-TTS).
- Progress-edit backup flow (v2 only).

## Validation

Per phase, manual smoke-test in dev Discord server:
- mention path produces embed, buttons clickable, persona voice visible in description.
- persona refusal: ask persona to do out-of-character thing, confirm warn embed + no action.
- slash admin: non-owner sees ephemeral denial; owner sees full flows.
- voice cards: wake in voice → card in voice-chat; trigger fire → violet card + audio.
- restart bot → persistent views still respond.
