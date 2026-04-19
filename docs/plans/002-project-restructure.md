# Project Restructure — Design

Status: design locked. Scope and outcome. Implementation plan at
[003-project-restructure-impl.md](003-project-restructure-impl.md).

## Goals

- Decouple tray GUI from bot runtime. Tray never import bot code.
- Single declarative contract for tray ↔ bot management via gRPC.
- Productionize build: packaged tray exe + packaged daemon exe.
- Replace `.env` with secret storage fit for Windows service.

## Decisions

### Architecture

- **IPC: gRPC over localhost.** Insecure channel, bound `127.0.0.1` only. No
  auth — toy app, single host, no loopback security concern.
- **Rationale:** `.proto` = single readable source of truth for management
  surface. Blocks schema drift ad-hoc JSON allow.
- **Generated code committed to repo at `halbot/_gen/`** (importable as
  `from halbot._gen import mgmt_pb2`). No protoc on end-user machines.
  Regen via `scripts/gen_proto.ps1` when `.proto` changes.
- **Log streaming stay file-tail**, not gRPC server-streaming RPC. Keep
  viewer trivial, unchanged from today.
- **Log level = single global setting** on daemon root logger — no
  per-source filtering at write time. Per-source verbosity handled in
  log viewer UI (match logger name in tray), not by routing levels into
  handler. Keeps daemon simple, avoids config explosion.

### Daemon lifecycle — SCM, not gRPC

- **Start / Stop / Restart of daemon go through Windows Service Control
  Manager** (pywin32 `win32serviceutil.StartService` etc.), not gRPC.
  Can't gRPC into stopped daemon, so Start must use SCM anyway; putting
  Stop/Restart there too keep model coherent.
- Tray need `SERVICE_START / STOP / QUERY_STATUS` granted to installing
  user — set via `sc sdset` by `halbot-daemon setup` at install.
- Bury SCM controls under "Service" submenu in tray. 95% daily ops go
  through module-level RPCs below, not service restarts.

### Management RPCs (v1)

Two concepts tray need, kept distinct:

- **Service state** (stopped / starting / running / stopping) — SCM query
  answer, works even when daemon dead.
- **Health** — gRPC answer, meaningful only when daemon up.

Tray poll loop: SCM query first → if running, call `Health()` → else
show "stopped".

```proto
// Health / introspection
rpc Health(Empty) returns HealthReply;

// Config — see Configuration section for full shape.
rpc GetConfig(Empty) returns ConfigState;
rpc UpdateConfig(ConfigPatch) returns ConfigState;
rpc PersistConfig(FieldList) returns ConfigState;
rpc ResetConfig(FieldList) returns ConfigState;
rpc SetSecret(SecretUpdate) returns StatusReply;

// Module lifecycle — reduce need for full daemon restart
rpc RestartDiscord(Empty) returns StatusReply;  // disconnect + reconnect
rpc LeaveVoice(Empty) returns StatusReply;      // drop voice session, keep Discord
rpc LoadWhisper(Empty) returns StatusReply;     // pre-warm
rpc UnloadWhisper(Empty) returns StatusReply;   // free VRAM; lazy reload next use
rpc LoadTTS(Empty) returns StatusReply;
rpc UnloadTTS(Empty) returns StatusReply;

message HealthReply {
  DiscordState discord = 1;      // CONNECTED | RECONNECTING | DISCONNECTED | RATE_LIMITED | TOKEN_INVALID
  bool llm_reachable = 2;
  VoiceState voice = 3;          // IDLE | IN_CHANNEL { guild, channel }
  bool whisper_loaded = 4;
  bool tts_loaded = 5;
  int64 uptime_seconds = 6;
  string daemon_version = 7;
}
```

Expand as real needs emerge (persona mgmt, sound CRUD). Don't pre-add
RPCs for subsystems with no state worth managing.

**Safety rules:**

- **Overlapping ops refused.** Concurrent `RestartDiscord` returns
  `FAILED_PRECONDITION`. Per-module lock in daemon.
- **`UnloadWhisper` while voice active → refuse** with clear error. User
  leave voice first. No magic auto-leave.
- **Rate-limit `RestartDiscord`** (e.g. 1 per 10s) to avoid gateway abuse.
- **Idempotent where sensible.** `UnloadWhisper` on already-unloaded =
  no-op success.

### Secret update UX — TBD / future

Scope locked, implementation deferred. Initial impl use
`halbot-daemon setup set-secret ...` from elevated shell. Tray UI later.
Target flow:

- **Tray "Settings → Discord token"** → masked input dialog → OK →
  tray send gRPC `SetSecret(DISCORD_TOKEN, <value>)`.
- **Daemon handle write itself** — run as LocalSystem, encrypt via
  DPAPI, persist to `HKLM\SOFTWARE\Halbot\Secrets`, trigger in-process
  Discord reconnect. No SCM restart needed.
- **Write before reconnect.** If reconnect fail, secret still persisted
  — `Health().discord = TOKEN_INVALID` surface failure; user retry with
  corrected token.
- **Write-only semantics.** `GetConfig()` never return secret values.
  Tray show "set / not set" indicator from whether key exists, nothing
  more. Combined with `Health()`, enough feedback for rotation.

### Subsystem fault isolation — crash vs degrade

- **Daemon process crash only on fatal top-level failures**
  (unrecoverable init errors, unhandled exceptions outside subsystem
  boundaries). NSSM `AppExit Default Restart` handle these.
- **Subsystem failures degrade, not crash.** Discord login fail, LLM
  backend unreachable, voice gateway error, whisper load fail — each
  caught at subsystem boundary, recorded in `Health()`, rest of daemon
  keep running.
- **Health enum cover degraded states:** `DiscordState` include
  `TOKEN_INVALID`, `RATE_LIMITED`, `DISCONNECTED`. Similar enums for
  voice / LLM as grow.
- **Every subsystem get room for future control RPCs** (retry,
  reconfigure, reload). Starting set in RPC list above; add more as
  subsystems gain runtime-controllable state.
- **NSSM throttle** (`AppThrottle` default 1500ms, plus
  `AppRestartDelay ~30000`) = belt-and-suspenders guard vs crash-loop
  leak, not primary defense.

### Secrets

- **Storage: DPAPI-encrypted blobs in `HKLM\SOFTWARE\Halbot\Secrets`.**
  `LocalMachine` scope so secrets survive service-account changes and
  don't need user login context.
- **Library:** pywin32 `win32crypt.CryptProtectData` /
  `CryptUnprotectData` with `CRYPTPROTECT_LOCAL_MACHINE`.
- **Keys stored:** `DISCORD_TOKEN`. Only true secret. Former candidates
  `LMSTUDIO_URL` / `OLLAMA_URL` **not secrets** — move to plaintext
  registry config (see Configuration).
- **Writes go through daemon, not tray.** Daemon run as LocalSystem, can
  always write HKLM. Tray call gRPC `SetSecret` (write-only); daemon
  encrypt and persist, then trigger relevant subsystem reload (e.g.
  Discord reconnect) in-process. Avoid SCM-restart-on-token-change and
  avoid granting tray HKLM write rights for secret storage.
- **Write-ordering:** daemon write DPAPI **before** attempting reconnect.
  If reconnect fail with new token, value still persisted and next daemon
  restart use it. Reconnect fail become
  `Health().discord = TOKEN_INVALID`, not crash.
- **Bootstrap:** `halbot-daemon setup` can set secrets from elevated
  shell (`halbot-setup set-secret DISCORD_TOKEN ...`) for first-run /
  headless rotation. Useful before tray installed.
- **No dev mode. `uv run` iteration explicitly unsupported.** One code
  path: read from HKLM, run as installed service. Iteration flow =
  **edit → pyinstaller → replace onedir → restart service**. No `.env`
  fallback, no HKCU shadow tree, no `HALBOT_DEV` branch, no env-var
  overrides, no alt config sources. Any code existing only to enable
  `uv run` loop = cut. Keep daemon coherent and trivially small.
- **Caveats:** LocalMachine scope mean any process on host running as
  any user can decrypt — fine given threat model. Keys tied to machine;
  reinstall on new box require re-entering secrets. No backup/restore
  story for DPAPI blob itself.

### Configuration

**Storage model:** Windows registry under `HKLM\SOFTWARE\Halbot\`.

- `HKLM\SOFTWARE\Halbot\Secrets\` — DPAPI-encrypted values. Write-only
  via gRPC `SetSecret`. Never displayed.
- `HKLM\SOFTWARE\Halbot\Config\` — plaintext values. Readable via
  `regedit` for debug.
- **No `config.json`** on disk. One storage backend; registry =
  Windows-native answer.
- **Defaults live in code** (`halbot/config.py` constant dict). Not
  checked-in JSON. Registry store only user overrides.
- **ACL treatment:** `halbot-daemon setup` at install grant installing
  user `KEY_WRITE` on both `Config` and `Secrets` subkeys. Daemon (as
  LocalSystem) always has write. Tray can write `Config` for
  `PersistConfig`, but write `Secrets` only indirectly via daemon (gRPC
  `SetSecret`).

**Runtime config vs startup config:** gRPC config surface expose **only**
fields with runtime effect when changed. Fields needing daemon restart
(gRPC port, data paths) = startup-only, not in config RPCs — change via
`halbot-daemon setup` + SCM restart. Hide footgun: "change it, nothing
happen till restart."

**RPC shape** — persist/reset as separate verbs, not flag on Update:

```proto
rpc GetConfig(Empty) returns ConfigState;             // per-field source included
rpc UpdateConfig(ConfigPatch) returns ConfigState;    // runtime-only; lost on restart
rpc PersistConfig(FieldList) returns ConfigState;     // flush listed fields to registry; empty = all
rpc ResetConfig(FieldList) returns ConfigState;       // reload from registry (or code defaults)
rpc SetSecret(SecretUpdate) returns StatusReply;      // write-only; daemon persists + reloads subsystem
```

**`GetConfig()` return per-field source** (`DEFAULT | REGISTRY |
RUNTIME_OVERRIDE`). Tray show "modified, not persisted" indicator so
user can tell which live values survive restart.

**Workflow this enable:** change `log_level` to DEBUG, reproduce bug,
decide DEBUG worth keeping → `PersistConfig(["log_level"])`. Or roll
back without restart → `ResetConfig(["log_level"])`.

**Initial scope (locked small):**

| Field | Storage | Runtime mutable | Notes |
|---|---|---|---|
| `DISCORD_TOKEN` | DPAPI | write-only via `SetSecret` | triggers Discord reconnect |
| `log_level` | registry plaintext | yes (`UpdateConfig`) | only initially scoped runtime-mutable field |
| `llm.backend`, `llm.url`, `llm.model` | registry plaintext | future | hot-reload LLM subsystem when added |
| `llm.max_tokens_text`, `llm.max_tokens_voice` | registry plaintext | future | read on next request |
| `voice.wake_word`, `voice.idle_timeout_seconds`, `voice.energy_threshold` | registry plaintext | future | some hot, some next-session only |
| gRPC port, data paths | registry plaintext | no (startup only) | not present in config RPCs |

Expansion approach documented here and in `halbot/config.py` module
docstring once exists: only add field to `UpdateConfig` if changing it
has runtime effect on daemon. Else leave startup-only.

### Folder layout

Repo root = `halbot/`. Inside, two sibling Python packages (`halbot/` =
daemon, `tray/` = GUI) plus proto + build assets.

```
halbot/                         # repo root
├── proto/
│   └── mgmt.proto
├── halbot/                     # daemon package
│   ├── __init__.py
│   ├── _gen/                   # committed protoc output
│   │   ├── __init__.py
│   │   ├── mgmt_pb2.py
│   │   └── mgmt_pb2_grpc.py
│   ├── mgmt_server.py          # gRPC server, runs inside daemon
│   ├── bot.py                  # Discord bot core (today's bot.py logic)
│   ├── db.py
│   ├── llm.py
│   ├── audio.py
│   ├── voice_session.py
│   ├── voice.py
│   ├── tts.py
│   ├── secrets.py              # DPAPI read/write helpers
│   └── daemon.py               # entrypoint: starts bot + gRPC server
├── tray/                       # tray GUI package
│   ├── __init__.py
│   ├── mgmt_client.py          # gRPC client
│   └── tray.py                 # entrypoint: tray icon + log window
├── scripts/
│   └── gen_proto.ps1           # protoc codegen wrapper
├── build_daemon.spec           # PyInstaller spec (onedir)
├── build_tray.spec             # PyInstaller spec (onedir)
└── pyproject.toml
```

### Packaging

- **No dev-run flow. Only path = PyInstaller build → install → run
  service.** See "No dev mode" under Secrets for rationale.
- **PyInstaller `--onedir`, not `--onefile`.** Onefile extract to temp
  every launch — slow startup, DLL-search headaches, awful for
  faster-whisper + CUDA bundling.
- **Two build venvs via uv extras** keep onedir output lean:
  - `uv sync --only-group daemon` → daemon build venv (discord.py,
    whisper, grpcio, pywin32, etc.)
  - `uv sync --only-group tray` → tray build venv (tkinter, pystray,
    grpcio, pillow)
  PyInstaller bundle what importable. Separate venvs = neither exe drag
  other's deps.
- **Two specs, two zips:** `halbot-daemon.zip`, `halbot-tray.zip`. Keep
  tray-only updates possible without touching daemon. Installer
  **folded into daemon exe as CLI subcommand**
  (`halbot-daemon setup --install` / `--uninstall` / `set-secret`), not
  third binary. One fewer PyInstaller spec, one fewer artifact to sync.
- **CUDA / faster-whisper bundling:** expect pain. Plan pin known
  torch + cuDNN combo, add explicit `--add-binary` for CUDA DLLs. Fall
  back to CPU whisper in packaged builds if bundling too fragile.
- **Update delivery:** manual. Download zip, run `update-tray.bat` /
  `update-daemon.bat`. No in-app update check, no GitHub Releases
  automation. Project not distributed.

### Independent lifecycle

Tray updates must not touch running daemon. Consequences:

- **Daemon lifecycle managed by NSSM**, not tray. Subprocess-parent model
  rejected — closing or updating tray would kill bot.
- **Separate install directories**, one PyInstaller onedir per component:
  `%ProgramFiles%\Halbot\daemon\` and `%ProgramFiles%\Halbot\tray\`. No
  shared DLLs. Updating one never touch other's files.
- **Separate autostart:** NSSM auto-start daemon at boot (before login,
  LocalSystem). Tray autostart **manual** initially (user drags shortcut
  into `shell:startup`) — elevated installer cannot cleanly write
  invoking user's HKCU. Automated per-user tray registration = later
  enhancement.
- **Fixed gRPC port on loopback** (e.g. `127.0.0.1:50737`). No dynamic
  port discovery; simpler than port-file dance.
- **Tray reconnect on `UNAVAILABLE`.** Cheap retry loop make daemon
  restarts invisible to already-open tray.
- **Tray self-update:** Windows lock running exe. Update flow = tray →
  spawn `updater.exe` → tray exits → updater swap onedir → updater
  launch new tray. Daemon updates stop NSSM service, swap onedir, start
  service — downtime fine for toy app.

### Proto compatibility — explicitly out of scope

- Proto changes require rebuilding **both** daemon and tray, deploying
  together. No forward/backward compat. No version negotiation, no
  reserved field numbers, no deprecation cycle. Keep iteration fast.
- Update delivery manual (download zip + run updater). No in-app auto
  update.

### Service account + data paths

- **Daemon run as LocalSystem** under NSSM. No dedicated service user, no
  password management.
- **State relocate to `%ProgramData%\Halbot\`:** `sounds.db`, `logs/`,
  persona data, other runtime files. Today's repo-relative paths must
  migrate.
- **Dev mode** still use repo-relative paths for convenience (resolve via
  `APP_DATA_DIR` env var or `halbot/paths.py` helper picking dev vs
  prod based on whether running from PyInstaller bundle).

### Installer scope

`halbot-daemon setup`, run elevated, one-shot:

- Write initial DPAPI secret (`DISCORD_TOKEN`) to
  `HKLM\SOFTWARE\Halbot\Secrets`.
- Grant installing user `KEY_WRITE` on `HKLM\SOFTWARE\Halbot\Config` and
  `HKLM\SOFTWARE\Halbot\Secrets` via `RegSetKeySecurity` (so tray and
  daemon can write without per-call elevation; daemon already can as
  LocalSystem).
- Run `nssm install halbot ...` pointing at daemon exe.
- Grant installing user `SERVICE_START / STOP / QUERY_STATUS` on halbot
  service via `sc sdset`.
- (Tray autostart not handled — see Independent lifecycle.)
- Create `%ProgramData%\Halbot\` with proper ACLs (LocalSystem write,
  user read for log viewing).

CLI flags only. No GUI wizard. Matching `halbot-setup --uninstall`
reverse all four steps.

### LLM backend: Ollama migration (TBD)

- **Migrate off LM Studio to Ollama.** Rationale and details TBD; own
  plan doc. Flagged here so restructure don't bake in LM Studio
  assumptions (e.g. `LMSTUDIO_MODEL` constant in `bot.py`,
  `ensure_model_loaded()` JIT-reload dance) any deeper than today.
- **LLM URL / model / backend = config, not secrets** — live in plaintext
  registry under `HKLM\SOFTWARE\Halbot\Config\llm\*`. Ollama migration
  become `UpdateConfig` + `PersistConfig` call (plus code for new
  backend), no DPAPI rotation.
- During restructure, rename LM-Studio-specific helpers so eventual swap
  = contained change to `halbot/llm.py`.

### Delivery approach

- **Refactor on feature branch - one branch per phase**, push to remote only when whole stack green.
- Concrete phase breakdown and ordering: see
  [003-project-restructure-impl.md](003-project-restructure-impl.md).

## Open questions

(None tracked — all prior questions resolved. Re-open as implementation
surface new ones.)