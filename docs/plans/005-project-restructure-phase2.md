# Project Restructure — Phase 2 Implementation Plan

Integration Target: Discord bot code from main branch moves into skeleton daemon service.
Scope Focus: Replicate existing Discord functionality (command parsing, message handling) within the halbot/daemon.py process
lifecycle.
Mechanics:
1. Discord Listener Hook: Implement background listener in Daemon to intercept Discord events.
2. Message Router: Develop internal router: takes incoming Discord message -> parses command/intent -> calls
appropriate service handler (e.g., voice, LLM).
3. State Management: Integrate necessary session state management previously handled by the full bot context into the daemon's
persistent memory structure.
4. Inter-Component Call: Use internal function calls/module imports (`halbot.voice.VoiceSession`) instead of network RPCs
for LLM/voice stack execution.

Deliverable: Functional, skeleton Discord bot integrated with Phase 1 service framework. gRPC remains limited to tray utility
functions.

## Phase 2b — close design gaps from [002](002-project-restructure.md)

Initial phase 2 port reinstated `.env` + env-var config to move fast. That
conflicts with 002 (no-dev-mode, registry-only config, DPAPI secrets).
This sub-phase closes the gap without starting a new branch.

### Scope

1. **Registry-backed config expansion.** Expand `halbot/config.py`
   `DEFAULTS` to cover the 002 table: `llm.backend`, `llm.url`,
   `llm.model`, `llm.max_tokens_text`, `llm.max_tokens_voice`,
   `voice.wake_word`, `voice.idle_timeout_seconds`,
   `voice.energy_threshold`, `voice.history_turns`,
   `voice.llm_combine_calls`, `tts.engine`, `tts.voice`, `tts.lang`,
   `tts.speed`. Bot/voice/LLM/TTS modules read from `config.get(...)`
   not `os.getenv(...)`. Proto `ConfigState` + `UpdateConfig` / server
   handlers extended accordingly.

2. **DPAPI secrets.** New `halbot/secrets.py` wrapping
   `win32crypt.CryptProtectData` / `CryptUnprotectData` with
   `CRYPTPROTECT_LOCAL_MACHINE`. Storage: values written as REG_BINARY
   under `HKLM\SOFTWARE\Halbot\Secrets\<NAME>`. Only key this phase:
   `DISCORD_TOKEN`.

3. **Installer extension.** `halbot-daemon setup --install` creates
   `HKLM\SOFTWARE\Halbot\Secrets` subkey and grants installing user
   `KEY_WRITE` (mirrors `Config` grant). Uninstall tears down same.

4. **Bootstrap CLI.** `halbot-daemon setup set-secret DISCORD_TOKEN
   <value>` — one-shot write from elevated shell. Enables first-run
   before tray UI exists.

5. **Kill `.env` path.** Remove `load_dotenv()` from `halbot/bot.py` and
   `.env.example` from repo. `DISCORD_TOKEN` read only from DPAPI at
   startup. If missing, bot subsystem logs `TOKEN_INVALID`-equivalent
   and sits idle — daemon + gRPC still alive. No `.env` fallback
   branch.

6. **Proto v2 — module lifecycle + SetSecret.** Extend `proto/mgmt.proto`:
   - `SetSecret(SecretUpdate)` → write-only, persists via DPAPI, then
     triggers Discord in-process reconnect.
   - `RestartDiscord`, `LeaveVoice`, `LoadWhisper`, `UnloadWhisper`,
     `LoadTTS`, `UnloadTTS` — all `(Empty) → StatusReply`. Rate-limit
     `RestartDiscord` (1 per 10s). Per-module lock refuses overlapping
     ops with `FAILED_PRECONDITION`. `UnloadWhisper` while voice active
     → refuse.
   - `HealthReply` gains `DiscordState discord`, `bool llm_reachable`,
     `VoiceState voice`, `bool whisper_loaded`, `bool tts_loaded`.
     `DiscordState = { UNKNOWN, CONNECTED, RECONNECTING, DISCONNECTED,
     RATE_LIMITED, TOKEN_INVALID }`. `VoiceState` oneof: `IDLE` |
     `InChannel { guild_id, channel_id }`.
   Regenerate stubs via `scripts/gen_proto.ps1`. Server handlers
   implemented; tray integration deferred (tray UI for these lives in
   later phase).

7. **Subsystem state tracking.** `halbot/bot.py` exports module-global
   `discord_state()` returning current `DiscordState`. Updated from
   discord event handlers (`on_ready` → CONNECTED, `on_disconnect` →
   DISCONNECTED, login exception → TOKEN_INVALID). LLM reachability =
   cheap HEAD/GET probe cached ≤5s. Voice state derived from
   `voice_listeners` dict. Whisper/TTS loaded flags exposed from
   `halbot.voice` / `halbot.tts`.

### Out of scope (deferred)

- Tray UI for new RPCs (Settings → Discord token dialog, module
  controls).
- Persona CLI re-add (low value until tray covers it).
- Ollama migration (own plan doc per 002).
- LM-Studio-specific helper renames (contained future change).

### Commit strategy

Landed as sequential commits on `restructure/phase-2-migration`. Each
commit independently runnable (daemon boots, gRPC serves) so bisect
remains useful.

1. doc: phase 2b scope
2. config: expand DEFAULTS + proto ConfigState + server handlers
3. config: wire bot/llm/voice/tts modules to registry-backed config
4. secrets: DPAPI module + installer Secrets key + `setup set-secret`
5. bot: read DISCORD_TOKEN from DPAPI; drop `.env` path
6. proto: SetSecret + module lifecycle RPCs + HealthReply expansion
7. daemon: subsystem state tracking wired into Health
