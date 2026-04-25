# Halbot — Interaction Reference

All ways a user can drive the bot in the current build. Surfaces grouped:
Discord text, Discord voice, tray, dashboard, gRPC.

## Discord — text channel

| Trigger | Who | What |
|---|---|---|
| `@Halbot …` mention or reply to bot | anyone in guild | Routed through LLM. Handles soundboard intents (list / play / save / remove / edit / clear / upload) plus free-form chat. Last ~50 messages in channel used as context. Attached audio files get saved as sounds. |
| Any message containing a configured **text trigger** phrase (case-insensitive) | anyone | Fires `reply` (post text) or `voice_play` (play sound if bot is in voice). Per-guild, configurable. Fire count tracked. |
| `/halbot admin …` slash | **guild owner only** | Recovery/admin commands. See below. |
| `/halbot wake-variants …` slash | **guild owner only** | Wake-token dictionary management. See below. |

### `/halbot admin` subcommands

| Sub | Effect |
|---|---|
| `status` | Counts of live vs. tombstoned rows per kind (sounds, personas, facts, triggers, grudges). |
| `deleted` | List soft-deleted rows of one kind (newest first). |
| `undelete` | Restore one soft-deleted row by id. |
| `undelete-all` | Restore every tombstoned row of that kind. |
| `panic` | Soft-clear personas / facts / triggers / grudges. Optional `include_sounds` also tombstones sounds. |
| `purge` | **Permanently** delete tombstoned rows. Irreversible. |

Deletes are soft by default — recoverable until `purge`.

### `/halbot wake-variants` subcommands

| Sub | Effect |
|---|---|
| `list` | Show current wake-variant dictionary (seed + llm + manual rows). |
| `add` | Add a manual wake-variant token. |
| `remove` | Remove a wake-variant token by exact match. |
| `generate` | LLM-generate variants of the wake word; replaces only the `llm` slice. |

## Discord — voice channel

| Trigger | What |
|---|---|
| Wake word **"Robot"** (or any token in the sqlite `wake_variants` dictionary — seed list covers common whisper homophones) in a channel the bot is in | VAD + faster-whisper STT captures the phrase after the wake word (1.5s silence = end-of-utterance). Transcript goes to the LLM; reply is spoken via TTS (falls back to text if TTS not loaded). Rolling 10-turn per-session history. |
| Voice **text-trigger** phrase in any transcript (no wake word needed) | Same hook-fire semantics as the text-channel triggers: `reply` or `voice_play`. |
| Soundboard emoji effect in the voice channel | Plays the effect sound (Discord `VoiceChannelEffect`). |
| User join/leave | Bot tracks presence, schedules idle-disconnect timer. No user-visible output. |

Voice commands cover the same soundboard verbs as text (list / play / save / remove / edit / clear / upload).

## Tray menu (system tray, per-user)

- **Open dashboard** — launches the pywebview window (default item).
- **Service → Start / Stop / Restart** — drives the `halbot` NSSM service.
- **Log level → DEBUG / INFO / WARNING / ERROR** — runtime change; auto-persists to registry.
- **Reset overrides** — drop all runtime config overrides.
- **Quit** — exit tray only (service keeps running).

## Dashboard (pywebview + React)

Panels in nav order (Config stays last by convention):

### Logs
Live tail with backlog (up to 200 lines). Min-level filter (DEBUG/INFO/WARN/ERROR).

### Daemon
- Service **Start / Stop / Restart** buttons.
- **Reconnect Discord**, **Leave voice**.
- **Load / Unload Whisper**, **Load / Unload TTS**.
- **Auto-restart** toggle (NSSM flag).
- Live status cards: uptime, daemon version, Discord state, voice state, LLM reachability, Whisper/TTS loaded, CPU%, RSS MB.
- Boot/shutdown and state-transition event log.

### Stats
Read-only cards & tables:
- Soundboard: total sounds, bytes stored, new-since-sync.
- Voice playback: plays today / all-time, session seconds, avg response latency.
- Wake word: detections today / all-time, false-positive count, avg wake→join latency.
- STT / TTS: avg + p95 duration ms, count today.
- LLM: response latency, TTFT, tokens/sec, requests today, context-usage %.
- Soundboard table: name, emoji, size, saved-by, 30-day play count, last-played.

### Analytics
Query events from the analytics store. Filter by kind (`mention`, `cmd_invoke`, `hook_fired`, `soundboard_play`, `llm_call`, …), user, target, date range; group-by; drill-down.

### Emojis
Gallery of synced custom server emojis with names, IDs, descriptions, images.

### Config
Edit any config field with type-aware widgets (string / number / bool / select / url / range). Per field: **Update** (runtime override), **Persist** (write to `HKLM\SOFTWARE\Halbot\Config`), **Reset** (drop override). Secrets (`discord_token`, etc.) go through `SetSecret` — daemon DPAPI-encrypts before storage.

### Title bar (always visible)
Minimize / maximize / close buttons in the custom WinTitleBar. Window
itself is draggable.

## gRPC management API (`127.0.0.1:50199`)

Internal surface consumed by tray + dashboard. `proto/mgmt.proto`:

| RPC | Purpose |
|---|---|
| `Health` | Uptime, version, Discord + voice state, LLM reachability, Whisper/TTS loaded flags. |
| `GetConfig` / `UpdateConfig` / `PersistConfig` / `ResetConfig` | Layered config CRUD. |
| `SetSecret` | DPAPI-encrypted secret write (e.g. `DISCORD_TOKEN`). |
| `RestartDiscord` / `LeaveVoice` | Discord-client control. |
| `LoadWhisper` / `UnloadWhisper` / `LoadTTS` / `UnloadTTS` | STT/TTS engine lifecycle. |
| `StreamLogs` | Streaming log lines with backlog + min-level filter. |
| `StreamEvents` | Streaming analytics events. |
| `GetStats` / `QueryStats` | Aggregate stats + filtered event queries. |

No auth on the gRPC port — it only listens on loopback.

## Gating summary

- **Guild owner only**: `/halbot admin …`, `/halbot wake-variants …`.
- **Anyone in guild**: mentions, replies, text/voice triggers, wake word, soundboard commands.
- **Local user on host**: tray menu, dashboard, gRPC (loopback only).
