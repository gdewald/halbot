# Project Restructure — Design

Status: design locked. Scope and outcome. Implementation plan lives in
[003-project-restructure-impl.md](003-project-restructure-impl.md).

## Goals

- Decouple tray GUI from bot runtime. Tray never imports bot code.
- Single declarative contract for tray ↔ bot management via gRPC.
- Productionize build: packaged tray exe + packaged daemon exe.
- Replace `.env` with secret storage appropriate for a Windows service.

## Decisions

### Architecture

- **IPC: gRPC over localhost.** Insecure channel, bound `127.0.0.1` only. No
  auth — toy app, single host, no security concerns on loopback.
- **Rationale:** `.proto` is a single readable source of truth for the
  management surface. Prevents schema drift that ad-hoc JSON would allow.
- **Generated code committed to repo at `halbot/_gen/`** (importable as
  `from halbot._gen import mgmt_pb2`). No protoc on end-user machines.
  Regen via `scripts/gen_proto.ps1` whenever `.proto` changes.
- **Log streaming stays file-tail**, not a gRPC server-streaming RPC. Keeps
  viewer logic trivial and unchanged from today.

### Daemon lifecycle — SCM, not gRPC

- **Start / Stop / Restart of the daemon itself go through the Windows
  Service Control Manager** (pywin32 `win32serviceutil.StartService`
  etc.), not through gRPC. You can't gRPC into a stopped daemon, so Start
  must use SCM anyway; putting Stop/Restart there too keeps the model
  coherent.
- Tray needs `SERVICE_START / STOP / QUERY_STATUS` granted to the
  installing user — set via `sc sdset` by `halbot-daemon setup` at install.
- Bury SCM controls under a "Service" submenu in the tray. 95% of daily
  operations should go through module-level RPCs below, not service
  restarts.

### Management RPCs (v1)

Two concepts the tray needs, kept distinct:

- **Service state** (stopped / starting / running / stopping) — answered
  by SCM query, available even when daemon is dead.
- **Health** — answered by gRPC, meaningful only when daemon is up.

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

Expand as real needs emerge (persona mgmt, sound CRUD). Do not pre-add
RPCs for subsystems without state worth managing.

**Safety rules:**

- **Overlapping ops refused.** Concurrent `RestartDiscord` returns
  `FAILED_PRECONDITION`. Per-module lock in the daemon.
- **`UnloadWhisper` while voice active → refuse** with clear error. User
  leaves voice first. No magical auto-leave.
- **Rate-limit `RestartDiscord`** (e.g. 1 per 10s) to avoid gateway abuse.
- **Idempotent where sensible.** `UnloadWhisper` on already-unloaded is
  a no-op success.

### Secret update UX — TBD / future

Scope locked but implementation deferred. Initial implementation uses
`halbot-daemon setup set-secret ...` from an elevated shell. Tray UI comes
later. Target flow:

- **Tray "Settings → Discord token"** → masked input dialog → OK →
  tray sends gRPC `SetSecret(DISCORD_TOKEN, <value>)`.
- **Daemon handles the write itself** — runs as LocalSystem, encrypts via
  DPAPI, persists to `HKLM\SOFTWARE\Halbot\Secrets`, then triggers
  in-process Discord reconnect. No SCM restart required.
- **Write before reconnect.** If reconnect fails, secret is still
  persisted — `Health().discord = TOKEN_INVALID` surfaces the failure;
  user retries with a corrected token.
- **Write-only semantics.** `GetConfig()` never returns secret values.
  Tray shows a "set / not set" indicator derived from whether the key
  exists, nothing more. Combined with `Health()`, that's enough feedback
  for rotation workflows.

### Subsystem fault isolation — crash vs degrade

- **Daemon process crashes only on fatal top-level failures**
  (initialization errors it cannot recover from, unhandled exceptions
  outside subsystem boundaries). NSSM's `AppExit Default Restart`
  handles these.
- **Subsystem failures degrade, not crash.** Discord login failure, LLM
  backend unreachable, voice gateway error, whisper load failure — each
  is caught at the subsystem boundary, recorded in `Health()`, and
  leaves the rest of the daemon running.
- **Health enum covers degraded states:** `DiscordState` includes
  `TOKEN_INVALID`, `RATE_LIMITED`, `DISCONNECTED`. Similar enums for
  voice / LLM as they grow.
- **Every subsystem gets room for future control RPCs** (retry,
  reconfigure, reload). Starting set is in the RPC list above; add
  more as subsystems gain runtime-controllable state.
- **NSSM throttle** (`AppThrottle` default 1500ms, plus
  `AppRestartDelay ~30000`) is a belt-and-suspenders guard against a
  crash-loop leaking through, not the primary defense.

### Secrets

- **Storage: DPAPI-encrypted blobs in `HKLM\SOFTWARE\Halbot\Secrets`.**
  `LocalMachine` scope so secrets survive service-account changes and do
  not require a user login context.
- **Library:** pywin32 `win32crypt.CryptProtectData` /
  `CryptUnprotectData` with `CRYPTPROTECT_LOCAL_MACHINE`.
- **Keys stored:** `DISCORD_TOKEN`. That is the only true secret. Former
  candidates `LMSTUDIO_URL` / `OLLAMA_URL` are **not secrets** — they move
  to plaintext registry config (see Configuration).
- **Writes go through the daemon, not the tray.** Daemon runs as
  LocalSystem and can always write HKLM. Tray calls gRPC `SetSecret`
  (write-only); daemon encrypts and persists, then triggers the relevant
  subsystem reload (e.g. Discord reconnect) in-process. Avoids
  SCM-restart-on-token-change and avoids granting the tray HKLM write
  rights for secret storage.
- **Write-ordering:** daemon writes DPAPI **before** attempting reconnect.
  If reconnect fails with the new token, the value is still persisted and
  a subsequent daemon restart uses it. Reconnect failure becomes
  `Health().discord = TOKEN_INVALID`, not a crash.
- **Bootstrap:** `halbot-daemon setup` can still set secrets from an elevated
  shell (`halbot-setup set-secret DISCORD_TOKEN ...`) for first-run /
  headless rotation. Useful before the tray is installed.
- **No dev mode. `uv run` iteration is explicitly unsupported.** One
  code path: read from HKLM, run as the installed service. Iteration
  flow is **edit → pyinstaller → replace onedir → restart service**.
  No `.env` fallback, no HKCU shadow tree, no `HALBOT_DEV` branch, no
  env-var overrides, no alternate config sources. Any code that would
  exist only to enable a `uv run` loop is cut. Keeps the daemon
  coherent and trivially small.
- **Caveats:** LocalMachine scope means any process on the host running as
  any user can decrypt — acceptable given threat model. Keys are tied to
  the machine; reinstall on a new box requires re-entering secrets. No
  backup/restore story for the DPAPI blob itself.

### Configuration

**Storage model:** Windows registry under `HKLM\SOFTWARE\Halbot\`.

- `HKLM\SOFTWARE\Halbot\Secrets\` — DPAPI-encrypted values. Write-only
  via gRPC `SetSecret`. Never displayed.
- `HKLM\SOFTWARE\Halbot\Config\` — plaintext values. Readable via
  `regedit` for debugging.
- **No `config.json`** on disk. One storage backend; registry is the
  Windows-native answer.
- **Defaults live in code** (`halbot/config.py` constant dict). Not
  checked-in JSON. Registry stores only user overrides.
- **ACL treatment:** `halbot-daemon setup` at install grants the installing
  user `KEY_WRITE` on both `Config` and `Secrets` subkeys. Daemon (as
  LocalSystem) always has write. Tray can write `Config` for
  `PersistConfig`, but writes `Secrets` only indirectly via daemon
  (gRPC `SetSecret`).

**Runtime config vs startup config:** the gRPC config surface exposes
**only** fields that have a runtime effect when changed. Fields that
would need a daemon restart (gRPC port, data paths) are startup-only
and not present in the config RPCs — change via `halbot-daemon setup` +
SCM restart. Hides footguns: "change it, nothing happens till restart."

**RPC shape** — persist/reset as separate verbs, not a flag on Update:

```proto
rpc GetConfig(Empty) returns ConfigState;             // per-field source included
rpc UpdateConfig(ConfigPatch) returns ConfigState;    // runtime-only; lost on restart
rpc PersistConfig(FieldList) returns ConfigState;     // flush listed fields to registry; empty = all
rpc ResetConfig(FieldList) returns ConfigState;       // reload from registry (or code defaults)
rpc SetSecret(SecretUpdate) returns StatusReply;      // write-only; daemon persists + reloads subsystem
```

**`GetConfig()` returns per-field source** (`DEFAULT | REGISTRY |
RUNTIME_OVERRIDE`). Tray shows a "modified, not persisted" indicator so
the user can tell which live values will survive restart.

**Workflow this enables:** change `log_level` to DEBUG, reproduce bug,
decide DEBUG is worth keeping → `PersistConfig(["log_level"])`. Or roll
back without restarting → `ResetConfig(["log_level"])`.

**Initial scope (locked small):**

| Field | Storage | Runtime mutable | Notes |
|---|---|---|---|
| `DISCORD_TOKEN` | DPAPI | write-only via `SetSecret` | triggers Discord reconnect |
| `log_level` | registry plaintext | yes (`UpdateConfig`) | only initially scoped runtime-mutable field |
| `llm.backend`, `llm.url`, `llm.model` | registry plaintext | future | hot-reload LLM subsystem when added |
| `llm.max_tokens_text`, `llm.max_tokens_voice` | registry plaintext | future | read on next request |
| `voice.wake_word`, `voice.idle_timeout_seconds`, `voice.energy_threshold` | registry plaintext | future | some hot, some next-session only |
| gRPC port, data paths | registry plaintext | no (startup only) | not present in config RPCs |

Design approach for expansion is documented here and in
`halbot/config.py` module docstring once it exists: only add a field to
`UpdateConfig` if changing it actually has a runtime effect on the
daemon. Otherwise leave it startup-only.

### Folder layout

Repo root is `halbot/`. Inside it, two sibling Python packages (`halbot/`
for the daemon, `tray/` for the GUI) plus proto + build assets.

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

- **No dev-run flow. Only path is PyInstaller build → install → run
  service.** See "No dev mode" under Secrets for rationale.
- **PyInstaller `--onedir`, not `--onefile`.** Onefile extracts to temp
  on every launch — slow startup, DLL-search headaches, awful for
  faster-whisper + CUDA bundling.
- **Two build venvs via uv extras** keep onedir outputs lean:
  - `uv sync --only-group daemon` → daemon build venv (discord.py,
    whisper, grpcio, pywin32, etc.)
  - `uv sync --only-group tray` → tray build venv (tkinter, pystray,
    grpcio, pillow)
  PyInstaller bundles what's importable. Separate venvs = neither exe
  drags the other's deps.
- **Two specs, two zips:** `halbot-daemon.zip`, `halbot-tray.zip`. Keeps
  tray-only updates possible without touching daemon. Installer is
  **folded into the daemon exe as a CLI subcommand**
  (`halbot-daemon setup --install` / `--uninstall` / `set-secret`), not a
  third binary. One fewer PyInstaller spec, one fewer artifact to keep
  in sync.
- **CUDA / faster-whisper bundling:** expect pain. Plan to pin a known
  torch + cuDNN combo and add explicit `--add-binary` for CUDA DLLs. Fall
  back to CPU whisper in packaged builds if bundling proves too fragile.
- **Update delivery:** manual. Download zip, run `update-tray.bat` /
  `update-daemon.bat`. No in-app update check, no GitHub Releases
  automation. Project is not distributed.

### Independent lifecycle

Tray updates must not touch the running daemon. Consequences:

- **Daemon lifecycle managed by NSSM**, not by the tray. Subprocess-parent
  model is rejected — closing or updating the tray would kill the bot.
- **Separate install directories**, one PyInstaller onedir per component:
  `%ProgramFiles%\Halbot\daemon\` and `%ProgramFiles%\Halbot\tray\`. No
  shared DLLs. Updating one component never touches the other's files.
- **Separate autostart:** NSSM auto-starts daemon at boot (before login,
  LocalSystem). Tray via HKCU Run at user login.
- **Fixed gRPC port on loopback** (e.g. `127.0.0.1:50737`). No dynamic port
  discovery; simpler than a port-file dance.
- **Tray reconnects on `UNAVAILABLE`.** Cheap retry loop makes daemon
  restarts invisible to an already-open tray.
- **Tray self-update:** Windows locks the running exe. Update flow is tray
  → spawn `updater.exe` → tray exits → updater swaps the onedir → updater
  launches new tray. Daemon updates stop the NSSM service, swap the
  onedir, start the service — downtime acceptable for a toy app.

### Proto compatibility — explicitly out of scope

- Proto changes require rebuilding **both** daemon and tray and deploying
  them together. No forward/backward compat. No version negotiation, no
  reserved field numbers, no deprecation cycle. Keeps iteration fast.
- Update delivery is manual (download zip + run updater). No in-app auto
  update.

### Service account + data paths

- **Daemon runs as LocalSystem** under NSSM. No dedicated service user, no
  password management.
- **State relocates to `%ProgramData%\Halbot\`:** `sounds.db`, `logs/`,
  persona data, any other runtime files. Today's repo-relative paths must
  be migrated.
- **Dev mode** still uses repo-relative paths for convenience (resolve via
  `APP_DATA_DIR` env var or a `halbot/paths.py` helper that picks dev vs
  prod based on whether running from a PyInstaller bundle).

### Installer scope

`halbot-daemon setup`, run elevated, one-shot:

- Writes initial DPAPI secret (`DISCORD_TOKEN`) to
  `HKLM\SOFTWARE\Halbot\Secrets`.
- Grants installing user `KEY_WRITE` on
  `HKLM\SOFTWARE\Halbot\Config` and `HKLM\SOFTWARE\Halbot\Secrets` via
  `RegSetKeySecurity` (so tray and daemon can write without per-call
  elevation; daemon already can as LocalSystem).
- Runs `nssm install halbot ...` pointing at daemon exe.
- Grants installing user `SERVICE_START / STOP / QUERY_STATUS` on the
  halbot service via `sc sdset`.
- Registers HKCU Run entry for tray exe.
- Creates `%ProgramData%\Halbot\` with appropriate ACLs (LocalSystem
  write, user read for log viewing).

CLI flags only. No GUI wizard. Matching `halbot-setup --uninstall`
reverses all four steps.

### LLM backend: Ollama migration (TBD)

- **Migrate off LM Studio to Ollama.** Rationale and details TBD; will be
  its own plan doc. Flagged here because the restructure should not bake
  in LM Studio assumptions (e.g. the `LMSTUDIO_MODEL` constant in
  `bot.py`, the `ensure_model_loaded()` JIT-reload dance) any deeper
  than today.
- **LLM URL / model / backend are config, not secrets** — they live in
  plaintext registry under `HKLM\SOFTWARE\Halbot\Config\llm\*`. Ollama
  migration becomes a `UpdateConfig` + `PersistConfig` call (plus code
  to handle the new backend), no DPAPI rotation.
- During restructure, rename LM-Studio-specific helpers so the eventual
  swap is a contained change to `halbot/llm.py`.

### Delivery approach

- **Big-bang refactor on `main`**, not feature branches. Solo project, no
  collaborators to unblock. Push to remote only when the whole stack is
  green.
- Concrete phase breakdown and ordering: see
  [003-project-restructure-impl.md](003-project-restructure-impl.md).

## Open questions

(None currently tracked — all prior questions resolved. Re-open as
implementation surfaces new ones.)

