# Plan: Decouple Voice Sessions from Text Channels

**Branch:** `dev/tts-and-reply-routing`
**Status:** Steps 1–3 landed 2026-04-17; Step 4 subsumed into Step 2 (sink_spec already in place)
**Author:** proposed 2026-04-17

## Motivation

Voice currently child of text channel bot @-mentioned in to join. Coupling shows several places:

- [`VoiceListener.__init__(vc, text_channel, on_command)`](../../voice.py) pins voice
  session to text channel that triggered join.
- [`handle_voice_command`](../../bot.py) writes all voice-session feedback (unknown
  command, miss, processing failure) to stored text channel — regardless of
  whether conversation moved on.
- [`_voice_reconnect`](../../bot.py) snapshots `(voice_ch_id, text_ch_id)` as
  pair for restart recovery.
- Idle-disconnect "👋 Left X" message posts to that text channel.
- **Memory asymmetry:** `parse_intent` fed 50 messages of channel history;
  `parse_voice_intent` / `parse_voice_combined` get zero. Voice has amnesia; text
  can confuse from unrelated voice activity logged in chat.

Goal: treat voice session as first-class object with own feedback surface and own conversational memory — not leaf hanging off text channel.

## Scope

### In scope
- Remove `text_channel` constructor arg on `VoiceListener`.
- Introduce `MessageSink` abstraction for voice-session feedback.
- Default voice-session feedback to voice channel's built-in chat pane.
- Give voice LLM own short rolling conversational memory.
- Preserve reconnect-on-restart behavior.

### Out of scope (possible follow-ups)
- Cross-channel guild-level summary memory (option B4 below).
- Per-guild admin-configurable feedback channel (option A2 below).
- Auto-created threads per voice session (option A3 below).
- Persisting voice history across sessions.

## Design options

### A. Where should voice-session system messages go?

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A1. Voice channel chat** ✅ recommended | `VoiceChannel` is `Messageable` — post into built-in chat | Native Discord idiom; scoped to voice participants; zero config | Some servers disable VC chat; bot needs `send_messages` there |
| A2. Configurable `halbot_channel` per guild | Admin sets one channel via command | Predictable | Needs DB + command; easy forget to set |
| A3. Auto-thread on voice channel chat | Thread per session, archive on leave | Keeps repeated noise out of main chat | More moving parts; needs `create_public_threads` |
| A4. TTS + logs only | Errors spoken, details in `halbot.log` | Cleanest for TTS-first future | Users can't scroll to see what said wrong |

### B. What conversational context should voice LLM see?

| Option | Voice LLM sees | Good when | Bad when |
|---|---|---|---|
| B1. No history (status quo) | Just transcript | Simple, fast, cheap | Can't handle "play it again" / "the other one" |
| **B2. Voice-only rolling buffer** ✅ recommended | Last N voice transcripts + bot responses from session | Continuous voice conversation; zero cross-contamination | Bot no idea what's happening in text chat |
| B3. Text channel history | Last 50 messages from joined-from text channel | Voice reacts to text ("sound Dave posted") | Exactly coupling we removing |
| B4. Cross-channel guild summary | Background-maintained summary, fed to both text and voice LLMs | "Halbot knows what's going on" — richest UX | Needs summarizer loop + decay policy |
| **B5. Separate memories** ✅ recommended (pairs with B2) | Voice sees voice; text sees text; no crossover | Clean separation; matches refactor thesis | Can't bridge modalities explicitly |

### Recommendation
**A1 + B2/B5.** Voice feedback goes into voice channel's own chat pane
(graceful fallback — see open question 1). Voice gets rolling
in-memory buffer of own session; text keeps per-channel history. No
cross-modality leakage.

B4 (guild-level summary) interesting follow-up once this lands and we
see whether voice + text feel too siloed in practice.

## Structural changes

Replace current ad-hoc `VoiceListener(vc, text_channel, on_command)` model
with `VoiceSession` object owning everything voice-scoped:

```python
@dataclass
class VoiceSession:
    guild_id: int
    vc: HalbotVoiceRecvClient
    listener: VoiceListener          # pure STT/VAD service; no text_channel
    message_sink: MessageSink        # polymorphic feedback target
    history: deque[VoiceTurn]        # rolling voice memory (B2)
    idle_task: asyncio.Task | None
    joined_at: float
```

### `MessageSink` — one method, `async send(text)`

```python
class MessageSink(Protocol):
    async def send(self, text: str) -> None: ...

class VoiceChatSink:       # posts into vc.channel (A1 default)
    ...
class TextChannelSink:     # posts into an explicit text channel (fallback / A2)
    ...
class LogOnlySink:         # no user-facing output
    ...
```

### `VoiceListener` loses `text_channel` parameter

Becomes pure STT/VAD service. Every call site reaching into
`listener.text_channel` today moves to `session.message_sink.send(...)`.

### `voice_listeners` becomes `dict[int, VoiceSession]`

All current single-file code paths (`voice_join`, `voice_leave`,
`handle_voice_command`, `_voice_idle_disconnect`, `on_voice_state_update`,
`_voice_reconnect`) switch to operating on `VoiceSession` objects.

### Reconnect snapshot carries sink config

`_voice_reconnect[gid] = (voice_ch_id, sink_spec)` where `sink_spec`
enough to rebuild sink (e.g. `("voice_chat",)` or
`("text", text_channel_id)`).

### Voice LLM sees own history

`parse_voice_intent` and `parse_voice_combined` gain optional
`history: list[dict]` parameter populated from `session.history`. Shape
matches `parse_intent`'s `channel_history` (role/content dicts), so no
prompt-engineering drift.

## Rollout plan — each step separate commit

### Step 1. Introduce `MessageSink` + refactor `VoiceListener`
- Add `MessageSink` protocol + `VoiceChatSink`, `TextChannelSink`, `LogOnlySink`.
- Remove `text_channel` param from `VoiceListener.__init__`; plumb sink through
  new `VoiceSession` dataclass.
- No behavior change yet — this step sink always `TextChannelSink`
  built from `message.channel`.
- Verification: bot feels identical to today.

### Step 2. Default to voice-channel chat (option A1)
- Switch `voice_join` to construct `VoiceChatSink(vc.channel)`.
- Implement fallback path per open question 1.
- Update idle-disconnect and "bot was disconnected" telemetry to use sink.
- Verification: join from `#bot-commands`, voice feedback appears in voice
  channel's chat, not `#bot-commands`.

### Step 3. Voice rolling history (options B2 + B5)
- Add `VoiceSession.history: deque[VoiceTurn]` with env-configurable
  `VOICE_HISTORY_TURNS` (default: TBD per open question 3).
- Append `(user_display_name, transcript, bot_response)` after each voice
  command resolves.
- Pass `history` into `parse_voice_intent` / `parse_voice_combined`; fold into
  messages list as user/assistant turns.
- Clear on voice-leave unless persistence chosen (open question 2).
- Verification: say "play cheering", then "play it again" — second command
  should resolve to same sound.

### Step 4. Update reconnect snapshot
- Snapshot `sink_spec` alongside voice channel id.
- Rebuild sink on reconnect.
- Verification: restart bot while in voice, feedback continues to go to
  right place after reconnect.

## Decisions (locked 2026-04-17)

Each decision records chosen option, alternatives weighed,
and tradeoff that drove pick. Future changes should amend this section, not rewrite.

### 1. Voice channel chat fallback — **1a: silent, log-only**
When bot can't post into voice channel's chat (feature disabled by
server, missing `send_messages` permission, channel-type quirk), posts
nowhere. Failed message written to `halbot.log` at WARNING
level; user sees nothing in Discord.

| Option | Chosen | Rationale |
|---|---|---|
| **a) Silent — log-only** | ✅ | Keeps voice truly decoupled; no risk of surprise posts leaking into unrelated channels; aligns with decision 4 (spoken-only feedback) |
| b) Fall back to joined-from text channel | — | Reintroduces coupling this refactor removes |
| c) Fall back to system channel, then log-only | — | System channel for Discord infra events; spamming with voice feedback noisy for admins |

**Implementation note:** emit WARNING log line on first fallback per session
so confused admin has breadcrumb to find, but do not repeat for
subsequent messages in same session.

### 2. Voice history retention — **2c: persist to SQLite**
Rolling buffer survives bot restarts and leaves/rejoins. Stored per guild
(not per channel — see open tradeoff below) so moving between voice channels
in same guild keeps continuity.

| Option | Chosen | Rationale |
|---|---|---|
| a) Clear on leave | — | Loses continuity across frequent bot-restart / re-join loop |
| b) Keep in memory per guild | — | Same continuity as (c) but lost on every tray-app restart |
| **c) Persist to SQLite** | ✅ | Matches how `saved_sounds` / `personas` already work; survives restarts and deploys |

**New table:** `voice_history(guild_id, ts, user_display_name, transcript,
bot_response)`. Retrieval bounded `SELECT … WHERE guild_id = ? ORDER BY
ts DESC LIMIT N` (see decision 3). Old rows beyond retention cap get
pruned on each insert.

**Open tradeoff recorded here, not re-opened:** storage per guild, not per
voice channel. Rationale — users think "what was I just asking Halbot for,"
not "what was I asking in #general-voice specifically." Revisit if cross-VC
confusion shows up in practice.

### 3. Voice buffer sizing — **3a: turn count (default 10)**
Buffer keeps last N voice turns per guild, where "turn" is one
`(user_transcript, bot_response)` pair. Default N = 10, configurable via
`VOICE_HISTORY_TURNS` env var.

| Option | Chosen | Rationale |
|---|---|---|
| **a) Turn count** | ✅ | Predictable prompt size; matches how text's 50-message history works |
| b) Token count | — | More precise but requires tokenizer (nothing in bot tokenizes locally); premature optimization |
| c) Wall-clock age | — | Would drop context during normal pauses; 30-min gap doesn't mean previous command stale |

**Implementation note:** turn count easy to reason about and matches
prompt-shape already proven on text side. If single turn ever
balloons (unusual), existing `max_tokens=256` ceiling on voice calls
protects latency; any truncation fallout becomes prompt-engineering fix,
not architecture change.

### 4. TTS + text feedback overlap — **4b: spoken only; logged server-side**
When TTS enabled and bot in voice, voice-session feedback
(miss / unknown / failure) spoken via TTS and written to `halbot.log`.
Nothing posted to voice channel's chat. If TTS synthesis fails,
fall back to posting to voice channel chat (per decision 1, then silent).

| Option | Chosen | Rationale |
|---|---|---|
| a) Speak AND post to voice channel chat | — | Redundant; clutters chat while TTS is primary channel |
| **b) Spoken only; logged server-side** | ✅ | Cleanest voice-first experience; logs preserve debuggability |
| c) Speak flavor, post terse status | — | Two surfaces to maintain; users don't consistently read both |

**Implementation note:** `MessageSink.send()` still single entry
point. `VoiceChatSink` checks `speak_only: bool` flag — when true,
routes to TTS via voice listener and logs message; when false (no
TTS engine, or TTS disabled), falls through to posting in voice channel
chat.

### 5. Guild-level summary memory (B4) — **follow-up, out of scope**
Not included this refactor. Revisit after B2/B5 ships and we see whether
voice and text feel too siloed in practice.

| Option | Chosen | Rationale |
|---|---|---|
| Include in this refactor | — | Adds summarizer loop, decay policy, and prompt work — doubles scope |
| **Follow-up** | ✅ | Additive; doesn't block decoupling work; better informed after we see B2/B5 in use |

**Trigger for revisit:** if users regularly ask voice-side about something
discussed in text (or vice versa) and bot fails to bridge
context, open dedicated plan for B4.