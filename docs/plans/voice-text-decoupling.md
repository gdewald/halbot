# Plan: Decouple Voice Sessions from Text Channels

**Branch:** `dev/tts-and-reply-routing`
**Status:** Proposal — awaiting answers on open questions before implementation starts
**Author:** proposed 2026-04-17

## Motivation

Voice is currently treated as a child of the text channel the bot was @-mentioned
in to join. That coupling shows up in several places:

- [`VoiceListener.__init__(vc, text_channel, on_command)`](../../voice.py) pins a voice
  session to whichever text channel triggered the join.
- [`handle_voice_command`](../../bot.py) writes all voice-session feedback (unknown
  command, miss, processing failure) to that stored text channel — regardless of
  whether the conversation has moved on.
- [`_voice_reconnect`](../../bot.py) snapshots `(voice_ch_id, text_ch_id)` as a
  pair for restart recovery.
- The idle-disconnect "👋 Left X" message posts to that text channel.
- **Memory asymmetry:** `parse_intent` is fed 50 messages of channel history;
  `parse_voice_intent` / `parse_voice_combined` get zero. Voice has amnesia; text
  can be confused by unrelated voice activity logged in chat.

The goal of this refactor is to treat a voice session as a first-class object
with its own feedback surface and its own conversational memory — not a leaf
hanging off a text channel.

## Scope

### In scope
- Remove `text_channel` as a constructor arg on `VoiceListener`.
- Introduce a `MessageSink` abstraction for voice-session feedback.
- Default voice-session feedback to the voice channel's built-in chat pane.
- Give the voice LLM its own short rolling conversational memory.
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
| **A1. Voice channel chat** ✅ recommended | `VoiceChannel` is `Messageable` — post into its built-in chat | Native Discord idiom; scoped to voice participants; zero config | Some servers disable VC chat; bot needs `send_messages` there |
| A2. Configurable `halbot_channel` per guild | Admin sets one channel via a command | Predictable | Needs DB + command; easy to forget to set |
| A3. Auto-thread on voice channel chat | Thread per session, archive on leave | Keeps repeated noise out of main chat | More moving parts; needs `create_public_threads` |
| A4. TTS + logs only | Errors spoken, details in `halbot.log` | Cleanest for the TTS-first future | Users can't scroll to see what they said wrong |

### B. What conversational context should the voice LLM see?

| Option | Voice LLM sees | Good when | Bad when |
|---|---|---|---|
| B1. No history (status quo) | Just the transcript | Simple, fast, cheap | Can't handle "play it again" / "the other one" |
| **B2. Voice-only rolling buffer** ✅ recommended | Last N voice transcripts + bot responses from this session | Continuous voice conversation; zero cross-contamination | Bot has no idea what's happening in text chat |
| B3. Text channel history | Last 50 messages from joined-from text channel | Voice reacts to text ("the sound Dave posted") | This is exactly the coupling we're removing |
| B4. Cross-channel guild summary | A background-maintained summary, fed to both text and voice LLMs | "Halbot knows what's going on" — richest UX | Needs summarizer loop + decay policy |
| **B5. Separate memories** ✅ recommended (pairs with B2) | Voice sees voice; text sees text; no crossover | Clean separation; matches the refactor thesis | Can't bridge modalities explicitly |

### Recommendation
**A1 + B2/B5.** Voice feedback goes into the voice channel's own chat pane
(with a graceful fallback — see open question 1). Voice gets a rolling
in-memory buffer of its own session; text keeps per-channel history. No
cross-modality leakage.

B4 (guild-level summary) is an interesting follow-up once this lands and we
see whether voice + text feel too siloed in practice.

## Structural changes

Replace the current ad-hoc `VoiceListener(vc, text_channel, on_command)` model
with a `VoiceSession` object that owns everything voice-scoped:

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

### `VoiceListener` loses its `text_channel` parameter

It becomes a pure STT/VAD service. Every call site that reaches into
`listener.text_channel` today moves to `session.message_sink.send(...)`.

### `voice_listeners` becomes `dict[int, VoiceSession]`

All the current single-file code paths (`voice_join`, `voice_leave`,
`handle_voice_command`, `_voice_idle_disconnect`, `on_voice_state_update`,
`_voice_reconnect`) switch to operating on `VoiceSession` objects.

### Reconnect snapshot carries sink config

`_voice_reconnect[gid] = (voice_ch_id, sink_spec)` where `sink_spec` is
enough to rebuild the sink (e.g. `("voice_chat",)` or
`("text", text_channel_id)`).

### Voice LLM sees its own history

`parse_voice_intent` and `parse_voice_combined` gain an optional
`history: list[dict]` parameter populated from `session.history`. Shape
matches `parse_intent`'s `channel_history` (role/content dicts), so no
prompt-engineering drift.

## Rollout plan — each step is a separate commit

### Step 1. Introduce `MessageSink` + refactor `VoiceListener`
- Add `MessageSink` protocol + `VoiceChatSink`, `TextChannelSink`, `LogOnlySink`.
- Remove `text_channel` param from `VoiceListener.__init__`; plumb sink through
  a new `VoiceSession` dataclass.
- No behavior change yet — at this step the sink is always `TextChannelSink`
  built from `message.channel`.
- Verification: bot feels identical to today.

### Step 2. Default to voice-channel chat (option A1)
- Switch `voice_join` to construct `VoiceChatSink(vc.channel)`.
- Implement fallback path per open question 1.
- Update idle-disconnect and "bot was disconnected" telemetry to use the sink.
- Verification: join from `#bot-commands`, voice feedback appears in the voice
  channel's chat, not in `#bot-commands`.

### Step 3. Voice rolling history (options B2 + B5)
- Add `VoiceSession.history: deque[VoiceTurn]` with env-configurable
  `VOICE_HISTORY_TURNS` (default: TBD per open question 3).
- Append `(user_display_name, transcript, bot_response)` after each voice
  command resolves.
- Pass `history` into `parse_voice_intent` / `parse_voice_combined`; fold into
  the messages list as user/assistant turns.
- Clear on voice-leave unless persistence is chosen (open question 2).
- Verification: say "play cheering", then "play it again" — second command
  should resolve to the same sound.

### Step 4. Update reconnect snapshot
- Snapshot `sink_spec` alongside the voice channel id.
- Rebuild the sink on reconnect.
- Verification: restart the bot while in voice, feedback continues to go to the
  right place after reconnect.

## Open questions

These block starting Step 2 — they're asked as direct prompts to the user
after this document is committed:

1. **Voice channel chat fallback.** If a server has voice-chat disabled or the
   bot lacks `send_messages` in it, which fallback do you want?
   - a) Silent — log-only, no user-facing feedback.
   - b) Fall back to the text channel the bot was joined from.
   - c) Fall back to the guild's system channel, then log-only.

2. **Voice history retention.** When the bot leaves voice, should the rolling
   buffer be:
   - a) Cleared — predictable stateless sessions.
   - b) Kept in memory per guild — "remember what I asked you to play last session."
   - c) Persisted to SQLite — survives bot restarts.

3. **Voice buffer sizing.** How should the buffer be bounded?
   - a) By turn count (e.g. last 10 voice turns).
   - b) By token count (e.g. last 2000 tokens).
   - c) By wall-clock age (e.g. last 30 minutes of session).

4. **TTS + feedback dual output.** When TTS is enabled and the bot is in voice,
   should errors/unknowns still post to the voice channel chat, or be spoken
   only?
   - a) Both — spoken AND posted to voice channel chat.
   - b) Spoken only; logged server-side.
   - c) Spoken for the LLM's flavor text; voice channel chat gets a terse status line.

5. **Scope confirmation.** Is cross-channel guild-level summary memory (option
   B4) worth doing in this refactor, or strictly a follow-up? My default
   recommendation is follow-up.
