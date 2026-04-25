# Halbot

A Discord bot that manages your server's soundboard using natural language. Mention the bot and tell it what to do — it uses a local LLM (Ollama by default, LM Studio also supported via OpenAI-compatible endpoint) to interpret your requests and take action.

> DISCLAIMER: THIS IS A TOY PROJECT DON'T TRY TO USE IT OUTSIDE YOUR PERSONAL PRIVATE DISCORD SERVER WITH TRUSTED USERS
> YOU CAN AND WILL BE PWND IF YOU TRY TO RUN THIS AGAINST A PUBLIC DISCORD SERVER OR A DISCORD SERVER WITH RANDOM PEOPLE
> I TAKE 0% LIABILITY FOR YOU IGNORING THIS MESSAGE FOR ANYTHING WHATSOEVER.

## Architecture (v0.6+)

Halbot runs as two PyInstaller onedir bundles on Windows:

- **Daemon** (`halbot-daemon.exe`) — NSSM-managed Windows service running under
  `LocalSystem`. Hosts the Discord client, LLM bridge (Ollama / LM Studio /
  any OpenAI-compatible endpoint), faster-whisper STT, Kokoro TTS, persona
  + analytics stack. Exposes an async gRPC management API on
  `127.0.0.1:50199`.
- **Tray** (`halbot-tray.exe`) — user-mode pystray app. Start/Stop/Restart the
  service, view logs, toggle log level, trigger reconnect, unload VRAM-heavy
  models, launch the pywebview **dashboard** (Stats / Logs / Daemon / Config
  / Analytics panels). Pure gRPC client — no bot logic.

Configuration lives in `HKLM\SOFTWARE\Halbot\Config` (plain REG_SZ) and secrets
(e.g. `DISCORD_TOKEN`) in `HKLM\SOFTWARE\Halbot\Secrets` (DPAPI-encrypted with
`CRYPTPROTECT_LOCAL_MACHINE`). No `.env`. Data (sqlite, logs) in
`%ProgramData%\Halbot\`.

## Features

### Soundboard Management
- **List sounds** — view all sounds on the server soundboard, or filter by user, date, or keyword
- **Remove sounds** — delete specific sounds or clear the entire soundboard
- **Edit sounds** — rename sounds or change their emoji (supports both unicode and custom server emojis)

### Sound Library
Local SQLite-backed library for saving and organizing sounds beyond what's on the live soundboard.

- **Save from soundboard** — back up sounds from the live soundboard to the library
- **Upload files** — save audio attachments (from the current message or recent chat history) directly to the library
- **Restore to soundboard** — re-upload saved sounds back to the live soundboard
- **Browse & manage** — list, rename, update metadata, or delete saved sounds

### Audio Effects
Apply effects to saved sounds and store the results as new clips. Powered by pydub/ffmpeg.

- **Echo**, **Reverb**, **Pitch shift** with presets or custom params
- **Composable & non-destructive** — effect chains re-apply from the original audio
- **Conversational** — bot asks for preset if params omitted

### Voice Channel Interaction
- **Join / leave** voice channels on command
- **Wake-word-triggered playback** via faster-whisper STT
- **TTS replies** via Kokoro-82M (local, ~300 MB VRAM)
- **Idle auto-disconnect** after configurable timeout

### Custom Emoji Awareness
- Syncs server custom emojis to sqlite on startup
- Uses a vision-capable LLM (whatever `llm_url` points at — Ollama
  multimodal models, LM Studio vision models, etc.) to auto-describe
  each emoji
- Re-syncs when emojis change

### Persona System
Users change the bot's voice via natural language directives stored in the DB and injected into the LLM system prompt. Max 200 chars × 10 directives.

### Conversation Context
Last N channel messages passed to the LLM so follow-ups resolve
correctly. N is set by `chat_history_limit` (default 50, tunable
in the dashboard Config panel).

### Owner Slash Commands
`/halbot-admin` exposes owner-only recovery surfaces:

- `status` — live + tombstoned row counts per kind
- `deleted` / `undelete` / `undelete-all` — soft-delete browser + restore
- `panic` — soft-clear personas/facts/triggers/grudges (recoverable)
- `purge` — permanent delete of tombstoned rows (irreversible)
- `wake-variants` — sub-group: `generate` (LLM-generate phonetic
  variants of the wake word), `list`, `add`, `remove`, `clear`

### Voice Transcript Logging
Off by default. Toggle `transcript_log_enabled` in the dashboard
Config panel to start writing every STT input + LLM reply + TTS
string to `%ProgramData%\Halbot\logs\transcripts.jsonl` as one
JSON object per line (rotating, 20 MB × 20 files).

## Requirements

- Windows 10/11 (DPAPI, NSSM, pywin32 hard dependencies)
- Python 3.12+ (build-time only; end users run PyInstaller exes)
- A local LLM endpoint — [Ollama](https://ollama.com/) (default) or
  [LM Studio](https://lmstudio.ai/) or any OpenAI-compatible
  `/v1/chat/completions` server. Configure via `llm_url` +
  `llm_model` in the dashboard Config panel.
- [ffmpeg](https://ffmpeg.org/) on PATH (audio effects)
- Discord bot token with message content + guild + voice intents

## Build + deploy

All commands assume CWD is repo root and PowerShell is **elevated**. Copy
blocks verbatim — no placeholders to fill in. One-time tooling:
`winget install 7zip.7zip` (10× faster archiving than
`Compress-Archive`).

### First install (one-time setup)

Builds both bundles, extracts to `Program Files`, creates NSSM service,
stores Discord token, launches tray.

```powershell
scripts\build.ps1

$dst = "$env:ProgramFiles\Halbot"
New-Item -ItemType Directory -Force -Path "$dst\daemon","$dst\tray" | Out-Null
Expand-Archive -Force -Path ".\dist\halbot-daemon.zip" -DestinationPath "$dst\daemon"
Expand-Archive -Force -Path ".\dist\halbot-tray.zip"   -DestinationPath "$dst\tray"

# NSSM service + HKLM ACLs + ProgramData ACLs + service-control ACL, auto-start.
& "$dst\daemon\halbot-daemon.exe" setup --install

# Store Discord token (DPAPI, CRYPTPROTECT_LOCAL_MACHINE).
& "$dst\daemon\halbot-daemon.exe" setup --set-secret DISCORD_TOKEN <paste-token-here>

# Launch tray (no autostart this phase — relaunch after each login, or pin to Startup).
Start-Process "$dst\tray\halbot-tray.exe"
```

### Update existing install (operational)

Rebuild + hot-swap binaries. Preserves config, secrets, logs, sqlite
data. Day-to-day Start / Stop / Restart: use the **tray menu** (no
elevation needed — ACL granted at install time).

```powershell
# One command. Fingerprints sources per target; rebuilds only what
# changed; self-elevates and streams the elevated log back.
scripts\deploy.ps1

# Single target:
scripts\deploy.ps1 -Daemon
scripts\deploy.ps1 -Tray

# Force a full rebuild + redeploy:
scripts\deploy.ps1 -Force -Clean

# Print the plan without running anything:
scripts\deploy.ps1 -DryRun
```

Under the hood `deploy.ps1` calls `build.ps1` only for stale targets,
then robocopy-mirrors `dist\halbot-{daemon,tray}\` into
`%ProgramFiles%\Halbot\`, stopping/starting the service and bouncing
the tray as needed. Run `scripts\build.ps1` directly if you want to
build without deploying.

### When to pass `-Clean`

Default build is incremental — reuses PyInstaller's analysis cache in
`build/`, cutting a daemon rebuild from ~150 s to ~20–30 s. Safe for
pure Python edits in `halbot/` or `tray/`. Add `-Clean` when any of
these changed:

- `build_daemon.spec` / `build_tray.spec` (hiddenimports, datas,
  `collect_submodules` / `collect_data_files` calls) — PyInstaller's
  cache invalidation on spec changes is flaky; stale analysis can ship
  a bundle missing newly-referenced modules.
- `proto/mgmt.proto` (or anything regenerating gRPC stubs).
- `pyproject.toml` / `uv.lock` dependency bumps that move import graph
  (new packages, major version jumps, removed deps).
- Python interpreter upgrade.

If the daemon crashes on startup with `ModuleNotFoundError: No module
named 'halbot.<something>'` after a rebuild, that's the cache-staleness
symptom — rerun with `-Clean`.

Other flags: `-NoZip` skips the archive step (leaves
`dist\halbot-{daemon,tray}\` only — handy when iterating locally).

### Rotating the Discord token or editing config

```powershell
# Rotate token:
& "$env:ProgramFiles\Halbot\daemon\halbot-daemon.exe" setup --set-secret DISCORD_TOKEN <new-token>

# Config values not exposed in the tray: edit HKLM\SOFTWARE\Halbot\Config in regedit,
# then restart via tray menu.
```

## Uninstall (destructive — **wipes all config and data**)

```powershell
# Elevated. Removes:
#   - NSSM service
#   - HKLM\SOFTWARE\Halbot tree (Config *and* DPAPI-encrypted Secrets, incl. DISCORD_TOKEN)
#   - %ProgramData%\Halbot\ (logs, sqlite sounds.db, everything)
# Does NOT remove %ProgramFiles%\Halbot\ binaries — rm them manually.
& "$env:ProgramFiles\Halbot\daemon\halbot-daemon.exe" setup --uninstall
Remove-Item -Recurse -Force "$env:ProgramFiles\Halbot"
```

There is no "soft" uninstall that preserves config. Back up
`HKLM\SOFTWARE\Halbot` (`reg export`) and `%ProgramData%\Halbot\` first
if you want to restore state later.

## Usage

Mention the bot in any channel:

```
@Halbot what sounds are on the soundboard
@Halbot remove the airhorn sound
@Halbot save all sounds to the library
@Halbot add reverb to big-yoshi
@Halbot from now on talk like a pirate
@Halbot join voice
```

Attach audio (MP3, OGG, WAV) to save directly to the library.

### Soundboard Limits

- Max file size: 512 KB
- Max duration: 5.2 seconds
- Formats: MP3, OGG, WAV

## Source run (dev, no build)

```powershell
uv sync --only-group daemon
uv run python -m halbot.daemon run
```

Data lands in `.\_dev_data\`. Source-run cannot `PersistConfig` or write
secrets unless `setup --install` has already granted HKLM ACLs to the current
user, or the shell is elevated.

## Repo layout

```
halbot/                 daemon package (Discord + voice + LLM + TTS + gRPC server)
  _gen/                 generated gRPC stubs
  daemon.py             CLI: run / setup --install|--uninstall|--set-secret
  mgmt_server.py        async gRPC server (Health, UpdateConfig, SetSecret, …)
  bot.py                Discord client + state machine
  voice_session.py      voice lifecycle + TTS orchestration
  voice.py              voice-receive + faster-whisper STT
  tts.py                Kokoro engine (+ pluggable registry)
  llm.py                LLM bridge (Ollama / LM Studio), intent parsing,
                        wake-variant generator, persona prompt assembly
  slash.py              /halbot-admin slash command tree
  transcript_log.py     rotating JSONL transcript logger (off by default)
  analytics.py          events.db writer + dashboard stat rollups
  db.py                 sqlite: sounds, personas, voice history, emojis
  audio.py              validation, format detection, pydub effects
  config.py             layered config (DEFAULTS → HKLM → runtime override)
  secrets.py            DPAPI wrapper (HKLM\SOFTWARE\Halbot\Secrets)
  installer.py          NSSM + registry ACLs + icacls
  paths.py              data_dir(): %ProgramData%\Halbot / ./_dev_data
  logging_setup.py      rotating file handler
  prompts/              system prompt text
tray/                   pystray app + grpc client + dashboard launcher
dashboard/              pywebview shell (app.py / bridge.py / log_stream.py)
frontend/               Vite/React dashboard (panels: Stats / Logs / Daemon
                        / Config / Analytics)
proto/mgmt.proto
scripts/
  build.ps1             full build: stamp _build_info, gen_proto, uv sync,
                        pyinstaller, frontend npm build, zip
  deploy.ps1            smart fingerprinted incremental rebuild + swap
  gen_proto.ps1
  extract_transcripts.py  one-shot scrape of halbot.log → _data/transcripts.jsonl
docs/plans/             design + impl records (002, 003, 007, 008, 014, 016,
                        017, 018, …)
```

