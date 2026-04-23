# Handoff: Halbot — Discord Bot Flows

## Overview

This bundle contains visual design references for **Halbot**, a Discord bot that manages server soundboards, remembers facts/personas/grudges, fires on text and voice triggers, and exposes an owner-only admin recovery shell. The designs cover ten user-facing interaction flows across the Discord text-channel and voice-channel surfaces described in `interactions.md`.

The goal of the handoff is to have a developer (using Claude Code or similar) wire these interactions into the real Halbot codebase so that the bot's responses in Discord look and feel like these mocks.

## About the Design Files

The `.html` and `.jsx` files in this bundle are **design references**, not production code. They are a React + inline JSX prototype that renders a *fake* Discord surface to communicate how Halbot's responses should be composed.

**Do not ship these files.** The implementation target is the real Discord bot runtime (Python per the interactions doc — `discord.py` / `py-cord` / similar). The prototype's job is to show:

- What components Halbot uses in each flow (embed / buttons / select menu / modal / ephemeral / reactions / message-edits / markdown / typing).
- How those components are composed, colored, and worded for each flow.
- What interaction patterns the bot supports (e.g. disambiguation via select menu, type-to-confirm via modal, progress via an edited message).

Everything shown is already constrained to **real Discord bot capabilities** — see "Component Constraints" below.

## Fidelity

**Hi-fi for composition; neutral-fidelity for chrome.**

- **Hi-fi:** The composition *inside* Halbot's responses — embed colors, field structure, button styles and order, select menu options, modal copy, subtext lines, reactions, markdown choices. These should be reproduced faithfully in the bot's outgoing payloads.
- **Neutral-fidelity:** The surrounding Discord UI (channel header, message rows, voice tiles, input bar). The prototype draws an *original* chat surface — not a Discord clone. The real client will render Halbot's messages in Discord's own UI; no chrome work is needed.

## Files in this bundle

| File | Purpose |
|---|---|
| `Halbot Flows v3.html` | Entry point. Loads React + Babel and the four JSX files below. |
| `design-canvas.jsx` | Pan/zoom canvas that lays out the ten flows in sections. Not part of the bot — just a presentation wrapper. |
| `chat-primitives-v2.jsx` | Text-channel primitives: `Message`, `Embed`, `ActionRow`, `Btn`, `SelectMenu`, `Modal`, `ModalInput`, `Reactions`, `Typing`, `Subtext`, `Code`, `CodeBlock`, `Mention`, `ChatFrame`, plus the `USERS` map and `hb` token object. **This is the canonical source for tokens and component shapes.** |
| `voice-primitives.jsx` | Voice-channel primitives: `VoiceFrame`, `VoiceTile`, `VoiceEvent`, `Waveform`. |
| `flows-v3.jsx` | The ten flow mocks. Each `Flow*` function renders one conversation thread. |
| `app-v3.jsx` | Wires flows into the canvas; not part of the bot. |
| `interactions.md` | The source-of-truth interactions reference for the bot. |

## Design System

All tokens live in `chat-primitives-v2.jsx` as the `hb` object. The bot should map them to Discord-native equivalents.

### Colors (used as embed sidebar colors)

| Token | oklch | Approx hex | Meaning in this bot |
|---|---|---|---|
| `hb.amber`  | `oklch(0.78 0.15 70)`  | `#E8B15C` | Halbot's default voice / soundboard / ops |
| `hb.good`   | `oklch(0.78 0.14 155)` | `#7CCFA0` | Success, completion, restored |
| `hb.warn`   | `oklch(0.80 0.15 45)`  | `#E8A361` | Clarification / tombstone listing |
| `hb.bad`    | `oklch(0.70 0.17 25)`  | `#D55D48` | Destructive / denied / panic |
| `hb.violet` | `oklch(0.72 0.14 300)` | `#B88AD0` | Trigger fired / persona active |
| `hb.cyan`   | `oklch(0.80 0.11 210)` | `#7EC3D8` | Informational / admin status / facts |

Convert to Discord's 24-bit integer per embed via its hex equivalent. Pin exact hex by eyeballing the rendered prototype or computing from oklch.

### Typography / markdown

Discord renders its own fonts; don't try to override. Follow the markdown usage shown in the mocks:

- **Bold** for action words and key values
- *Italic + subtext* (`-# …`) for the "Halbot's one-line lead-in" above every embed
- `` `inline code` `` for IDs, filenames, and field codes
- ``` ```fenced blocks``` ``` for tables, progress bars, and transcripts
- `@user`, `@role`, `#channel` mentions rendered as real Discord mentions

### Voice (chrome only)

The voice flow cards (`VoiceFrame`, `VoiceTile`, etc.) are illustrative — the real Discord client already shows the voice channel. In production, Halbot posts its voice-event "cards" as **text messages in the voice channel's chat** (or a paired text channel) with the same embed shape as other flows.

## Component Constraints (hard rules)

The prototype sticks to real Discord bot capabilities. The implementation must too.

| Component | Real Discord equivalent | Limits |
|---|---|---|
| `Embed` | `discord.Embed` | Color, author(name, icon_url), title(+url), description, fields[{name, value, inline}], footer(text, icon_url), timestamp. Max 10 embeds per message. Size limits per field (256/1024). |
| `ActionRow` + `Btn` | Components v1 ActionRow | Styles: primary(blurple) / secondary(grey) / success(green) / danger(red) / link(url). Max 5 buttons per row, max 5 rows per message. |
| `SelectMenu` | StringSelect (also User/Role/Channel/Mentionable selects exist) | One per ActionRow. Up to 25 options, each with label/description/emoji. |
| `Modal` | Modal (text input) | Only invoked from an interaction (button/select). 1-5 TextInputs. |
| `Subtext` / `Quote` | Discord markdown `-#` (subtext) / `>` (quote) | Supported natively. |
| `Typing` | `channel.typing()` context / `trigger_typing` | Purely visual cue; no state. |
| `Reactions` | `message.add_reaction(emoji)` | Guild custom emoji or standard emoji. |
| `Ephemeral` message | `flags=MessageFlags.EPHEMERAL` on interaction response | Only available in interaction responses, not plain messages. |
| Progress via edits | `message.edit(embed=...)` | Keep the embed shape stable; swap description/fields. |

## The Ten Flows

Each flow lives in `flows-v3.jsx` as a function. Details below reference the exact component calls to mirror.

### 01 — Mention routing (`FlowMention`)
**Trigger:** `@Halbot …` in any channel the bot can read.
**What happens:** Last ~50 channel messages are sent as LLM context. LLM classifies intent (soundboard verbs or free-form). For soundboard intents, bot executes and posts one embed.
**Components:** `Typing` indicator while thinking → italic-subtext "how I resolved this" → amber embed with author + title + fields(From, Voice, Requested) + footer → ActionRow: Stop / Replay / Louder.

### 02 — Attach audio to save (`FlowUpload`)
**Trigger:** `@Halbot save this as <name>, emoji <e>` + audio attachment.
**What happens:** Bot pulls the attachment, persists it as a sound, assigns the requested emoji.
**Components:** Subtext resolved intent → green embed with slot/size/length/emoji/format/saved-by inline fields → ActionRow: Play / Rename / Edit / Remove.

### 03 — Text trigger fires (`FlowTextTrigger`)
**Trigger:** Any message containing a configured trigger phrase (case-insensitive). No mention.
**What happens:** Bot posts the configured reply *or* fires `voice_play` if it's in voice. Fire counter increments.
**Components:** Subtext "Text trigger fired" → violet embed (author uses ⚡ icon) with Matched phrase / Scope / Fire count inline fields → ActionRow: Mute trigger here / See triggers.

### 04 — Voice wake-word (`FlowVoiceWake`)
**Trigger:** "Halbot, …" spoken in a voice channel the bot is in.
**What happens:** VAD captures ~1.5s-of-silence-bounded utterance → faster-whisper STT → LLM → TTS playback. Rolling 10-turn session history.
**Components:** Voice-channel chrome is illustrative only. The real implementation posts an **embed-shaped card** (in a paired text channel, or the voice channel's chat) with: amber left border, eyebrow `WAKE`, waveform line (`Waveform` — ASCII block chars in a code fence), the transcript quoted with speaker name, and a footer with `VAD end-of-utterance · 1.52s silence · faster-whisper · session turn 3/10`. A second card shows the TTS reply with `HALBOT · ▶ TTS · speaking` and `LLM 640ms · TTS 220ms` timing footer.

### 05 — Voice trigger (`FlowVoiceTrigger`)
**Trigger:** A trigger phrase appears in the STT transcript — no wake word.
**What happens:** Bot fires the `voice_play` action immediately.
**Components:** Similar card shape to wake flow, but violet eyebrow `VOICE TRIGGER`, the matching phrase is highlighted in an amber-soft span, a sub-card shows `▶ Playing vine-boom.mp3`.

### 06 — `!halbot admin status` (`FlowAdminStatus`)
**Trigger:** `!halbot admin status` — owner only. Bot must reject non-owner callers.
**What happens:** Bot reports live vs. tombstoned row counts for each kind.
**Components:** Subtext verifying owner → cyan embed, title "Store counts · live vs. tombstoned", description is a fenced code block with a fixed-width table, fields for Retention and Next purge → ActionRow: View deleted / Undelete… / Panic… / admin help.

### 07 — `admin deleted` + `admin undelete` (`FlowAdminUndelete`)
**Trigger:** `!halbot admin deleted sounds 5`, then `!halbot admin undelete sounds <id>` (or via select menu).
**What happens:** First reply lists tombstones as block embed fields. A StringSelect offers per-row undelete. Follow-up reply confirms with the restored row's ID and new state.
**Components:** Warn-colored embed for listing, SelectMenu with up to 25 options (emoji 🪦), ActionRow with a green success button "Undelete all sounds". Follow-up uses a green embed with Kind / ID / State inline fields.

### 08 — `admin panic [all]` (`FlowAdminPanic`)
**Trigger:** `!halbot admin panic all` — owner only.
**What happens:** Soft-clears personas/facts/triggers/grudges (+ sounds with `all`). A Modal demands the code word `PANIC`. On submit, bot posts a final red embed summarizing tombstones and explaining `undelete-all` / `purge`.
**Components:** Red embed → Danger button "Confirm panic…" triggers Modal with two TextInputs (code word required, reason optional) → ephemeral modal completion → red embed result.

### 09 — Persona saved (`FlowPersonas`)
**Trigger:** `@Halbot from now on be <persona>. save it`.
**What happens:** Bot persists a per-user persona in the `personas` kind. Subsequent mentions from that user use the persona voice.
**Components:** Violet embed for the save confirmation with Trigger / Scope / Saved fields → ActionRow: Edit wording / Make guild-wide / Remove persona. Follow-up mention from the same user shows the bot replying in character, with footer identifying the active persona and its fire count.

### 10 — Facts + grudges ledger (`FlowGrudgesFacts`)
**Trigger (fact):** `@Halbot remember: <fact about subject>`.
**Trigger (grudge list):** `@Halbot still mad at me?` (or similar).
**What happens:** Facts are stored in the `facts` kind with subject + source message. Grudges are their own kind; bot enumerates held grudges with severity markers and per-item "Forgive" buttons (which soft-delete the grudge).
**Components:** Cyan embed for fact-stored. Warn-colored embed titled "What I'm holding onto about <user>" with one block field per grudge, ActionRow with per-item Forgive buttons (max 5).

## Cross-cutting behavior

1. **Subtext lead-in.** Every Halbot response is preceded by a one-line italic `-# *…*` subtext that states how the LLM resolved the request (intent, persona, confidence note). This is the bot's "thinking out loud" — it replaces the dashed reasoning box from earlier mocks and keeps users able to catch misinterpretations.
2. **Author block.** Every embed uses `author = { name: "Halbot · <mode>", icon_url: <halbot avatar> }`. The `<mode>` varies: `soundboard`, `saved`, `trigger`, `admin/status`, `admin/undelete`, `admin/panic`, `persona saved`, `night shift`, `grudge ledger`, `actioned`, `permission denied`, etc.
3. **Soft delete language.** Deletes are tombstones until `purge`. Every destructive-but-soft reply tells the user how to undo (`!halbot admin undelete-all <kind>`) and that permanence only comes via purge.
4. **Ephemeral for owner-gated failures.** A non-owner hitting `!halbot admin …` gets an ephemeral "permission denied" — this is shown in the permission-denied error pattern from earlier flows; in v3 it's implicit in admin flows (if caller isn't owner, reply ephemerally and stop).
5. **Button caps.** Never exceed 5 buttons per row / 5 rows per message. For longer option lists, use a StringSelect.
6. **Fenced blocks for grids.** Anything tabular (status counts, progress bars, transcripts) goes in a fenced code block inside the embed description — Discord renders monospace reliably there.
7. **Reactions.** Use sparingly as a light confirmation (e.g. 🚀 after backup, 🪦 after delete). Don't depend on reactions for state — they're decorative.
8. **Persona-aware copy, neutral fields.** When a persona is active, only the embed description/subtext adopts the voice; field names and identifiers stay neutral so downstream tooling can still parse.

## Implementation notes (suggested, not prescriptive)

- A `send_halbot_reply(ctx, *, mode, color, title, description=None, fields=(), footer=None, components=(), subtext=None, ephemeral=False)` helper will eliminate boilerplate across all ten flows. All flows match that shape.
- `color` should be taken from a module-level enum matching the `hb` tokens above.
- For the progress-bar flow (full backup in v2 / referenced in the mention flow), implement it as a single `channel.send` followed by `message.edit(embed=...)` in a loop; keep the embed shape stable and only mutate description + two fields (`Stage`, `ETA`).
- `Modal` handlers receive the submitted values via the interaction payload — wire the `PANIC` input to an equality check before performing the soft-clear.
- Voice "cards" in flows 04 and 05 should be sent to whatever text surface you use for voice events (a paired `#voice-log` channel is common; alternatively the guild's system channel).

## Assets

No proprietary assets are used. Halbot's avatar in the mocks is a Unicode glyph (`✦`) on an amber tile — swap in the real avatar at deploy time.

## Open questions / flagged decisions

1. The admin prefix command `!halbot admin` is not a slash command in the interactions spec. Slash-command equivalents (`/halbot-admin status`, etc.) would unlock ephemeral replies by default and feel more modern — worth a discussion.
2. Persona storage scope: mocks show per-user; is guild-wide also supported via the "Make guild-wide" button?
3. Grudge-forgive buttons assume ≤5 grudges at a time. For more, paginate via a select menu instead.
