# Project Restructure — Phase 3 Implementation Plan

Status: MVP. Lands on branch `restructure/phase-3-migration`.

## Goal

One-shot migration from pre-restructure halbot v0.5.0 into the new
daemon/tray layout. Phase 2 delivered functional daemon + gRPC + DPAPI
secrets + registry config, but nothing moves v0.5.0 user state
(`.env`, `sounds.db`, persona/emoji rows) into the new locations.

Phase 3 = write the migration tool + document the flow. Tray UI for
the new RPCs (SetSecret dialog, module lifecycle controls, Health
subsystem badges) is **deferred** to a later phase — see TODOs below.

## Scope — MVP

### Migration script

New: `scripts/migrate_v050.py`. Run from elevated shell once per
upgrade. Input: repo root or explicit paths. Effect:

1. **Parse `.env`.** Extract keys `DISCORD_TOKEN`, `LMSTUDIO_URL`,
   `LMSTUDIO_MODEL`, `VOICE_IDLE_TIMEOUT_SECONDS`,
   `VOICE_HISTORY_TURNS`, `VOICE_LLM_COMBINE_CALLS`, `TTS_ENGINE`,
   `KOKORO_VOICE`, `KOKORO_LANG`, `KOKORO_SPEED`.
2. **`DISCORD_TOKEN` → DPAPI** via `halbot.secrets.set_secret`. Written
   to `HKLM\SOFTWARE\Halbot\Secrets\DISCORD_TOKEN`.
3. **Other keys → registry** via `halbot.config.update` +
   `halbot.config.persist`. Key-name translation table:
   - `LOG_LEVEL`          → `log_level`
   - `LMSTUDIO_URL`       → `llm_url`
   - `LMSTUDIO_MODEL`     → `llm_model`
   - `VOICE_IDLE_TIMEOUT_SECONDS` → `voice_idle_timeout_seconds`
   - `VOICE_HISTORY_TURNS`        → `voice_history_turns`
   - `VOICE_LLM_COMBINE_CALLS`    → `voice_llm_combine_calls`
   - `TTS_ENGINE`         → `tts_engine`
   - `KOKORO_VOICE`       → `tts_voice`
   - `KOKORO_LANG`        → `tts_lang`
   - `KOKORO_SPEED`       → `tts_speed`
   Unknown keys: logged + skipped, not an error. Known-unmigrated
   (no corresponding registry field yet): `HF_HUB_OFFLINE`,
   `LLM_DISABLE_THINKING`. Leave in `.env` until config schema
   expands.
4. **Copy `sounds.db`** from source path (default `./sounds.db`) into
   `halbot.paths.data_dir()`. Skip-with-warn if destination already
   exists unless `--force`. Schema migration not required:
   `halbot.db.db_init()` uses `CREATE TABLE IF NOT EXISTS`, compatible
   with v0.5.0 schema.
5. **Idempotent.** Re-run = no-op for already-migrated values. Safe to
   retry after partial failure.

Flags:
- `--repo PATH` — source repo root (default: cwd).
- `--env PATH` — override `.env` path.
- `--db PATH` — override `sounds.db` path.
- `--force` — overwrite destination `sounds.db` and re-encrypt secrets
  even if already present.
- `--dry-run` — print actions, write nothing.
- `--skip-secrets`, `--skip-config`, `--skip-db` — selective phases.

Requires admin: DPAPI `CRYPTPROTECT_LOCAL_MACHINE` write + HKLM
registry write. Script errors clearly if not elevated.

### Post-migration checklist (docs only)

Document in the migration script's `--help` output and a top-of-file
comment:

1. Stop v0.5.0 bot (`halbot_tray.py`, `pythonw bot.py`).
2. Build + install v0.6 per CLAUDE.md (`scripts/build.ps1` → unzip →
   `halbot-daemon.exe setup --install`).
3. Run migration: `python scripts/migrate_v050.py --repo .`
4. Start service: tray → Service → Start (or `sc start halbot`).
5. Verify `Health()` reports `discord=CONNECTED`.

## TODO — deferred to later phases

- **Tray UI for phase 2b RPCs.** Currently unreachable from tray:
  - `Settings → Discord token…` masked input dialog calling
    `SetSecret(DISCORD_TOKEN, …)`.
  - Service submenu items for `RestartDiscord`, `LeaveVoice`.
  - Submenu for Whisper / TTS load/unload.
  - Status section showing `Health.discord` enum, `llm_reachable`,
    `voice` oneof, `whisper_loaded`, `tts_loaded`. Icon tint by
    aggregate health.
  - Tray indicator when `discord_state == NO_TOKEN` or
    `TOKEN_INVALID` → single-click open SetSecret dialog.
- **Persona CLI re-add** inside tray (list / clear). Removed in 2a.
- **Ollama migration.** Own plan doc per 002.
- **Secret rotation feedback.** After `SetSecret(DISCORD_TOKEN)`,
  in-process reconnect already triggered; tray needs toast on
  success/failure.
- **Config validation** on `UpdateConfig`. Today the server accepts
  any string. Enum / numeric range checks belong server-side.

## Commit strategy

1. doc: phase 3 scope
2. scripts: `migrate_v050.py` + dry-run verified path
