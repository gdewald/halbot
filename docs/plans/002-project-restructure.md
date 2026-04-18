# Project Restructure Plan

Status: draft, decisions landing iteratively.

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

### Management RPCs (v1)

```
rpc Start(Empty)    returns StatusReply;
rpc Stop(Empty)     returns StatusReply;
rpc Restart(Empty)  returns StatusReply;
rpc GetStatus(Empty) returns StatusReply;   // running, pid, uptime
rpc Health(Empty)   returns HealthReply;    // LM Studio reachable, Discord connected, voice active
```

Expand as needs emerge (persona mgmt, sound CRUD, etc.).

### Secrets

- **Storage: DPAPI-encrypted blobs in `HKLM\SOFTWARE\Halbot`.**
  `LocalMachine` scope so secrets survive service-account changes and do not
  require a user login context.
- **Library: pywin32 `win32crypt.CryptProtectData` / `CryptUnprotectData`**
  with `CRYPTPROTECT_LOCAL_MACHINE`.
- **Keys stored:** `DISCORD_TOKEN`, `LMSTUDIO_URL`. `LOG_LEVEL` stays as
  plain registry value (not secret).
- **Bootstrap / rotation:** standalone `halbot-setup.exe` run elevated
  (one-time at install, or on token rotation). Sets HKLM values. Tray itself
  does not need elevation at runtime. Avoids exposing `SetSecret` over gRPC
  and avoids elevating the tray.
- **Dev fallback:** read order is env var → DPAPI registry → hard error.
  Keeps `uv run bot.py` frictionless for local development without HKLM
  write rights. `.env` is no longer a supported production path.
- **Caveats noted:** LocalMachine scope means any process on the host can
  decrypt — acceptable given threat model. Keys are tied to the machine;
  reinstall on a new box requires re-entering secrets. No backup/restore
  story for the DPAPI blob itself.

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
├── setup/
│   └── halbot_setup.py         # elevated one-shot: writes HKLM secrets
├── scripts/
│   └── gen_proto.ps1           # protoc codegen wrapper
├── build_daemon.spec           # PyInstaller spec (onedir)
├── build_tray.spec             # PyInstaller spec (onedir)
└── pyproject.toml
```

### Packaging

- **Dev = `uv run -m halbot.daemon` + `uv run -m tray.tray` in two
  terminals.** No combined dev launcher.
- **Prod = PyInstaller `--onedir`, not `--onefile`.** Onefile extracts to
  temp on every launch — slow startup, DLL-search headaches, awful for
  faster-whisper + CUDA bundling.
- **Two specs, two zips:** `halbot-daemon.zip`, `halbot-tray.zip`. Keeps
  tray-only updates possible without touching daemon. `halbot-setup.exe`
  is a third small spec (or folded into the daemon build as a CLI
  subcommand).
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

`halbot-setup.exe`, run elevated, one-shot:

- Writes DPAPI secrets to `HKLM\SOFTWARE\Halbot` (`DISCORD_TOKEN`,
  `LMSTUDIO_URL` — until Ollama migration, then adjust).
- Runs `nssm install halbot ...` pointing at daemon exe.
- Registers HKCU Run entry for tray exe.
- Creates `%ProgramData%\Halbot\` with appropriate ACLs (LocalSystem write,
  user read for log viewing).

CLI flags only. No GUI wizard. Matching `halbot-setup --uninstall`
reverses all four steps.

### LLM backend: Ollama migration (TBD)

- **Migrate off LM Studio to Ollama.** Rationale and details TBD; will be
  its own plan doc. Flagged here because the restructure should not bake
  in LM Studio assumptions (e.g. the `LMSTUDIO_URL` secret key, the
  `LMSTUDIO_MODEL` constant in `bot.py`, the `ensure_model_loaded()`
  JIT-reload dance) any deeper than today.
- During restructure, rename the LM Studio-specific helpers so the
  eventual swap is a contained change to `halbot/llm.py` plus a secret
  rename in DPAPI storage.

### Restructure phasing

- **Big-bang refactor on `main`**, not feature branches. Solo project, no
  collaborators to unblock. Push to remote only when the whole stack is
  green.
- Phase order (sketch only; may collapse as work proceeds):
  1. Move flat modules into `halbot/` package; fix imports.
  2. Add proto + `_gen/` + gRPC server.
  3. Build `tray/` package; tray talks only to daemon over gRPC.
  4. Secrets: `halbot/secrets.py` + `halbot-setup.exe`; drop `.env`.
  5. NSSM integration + installer steps 2–4.
  6. PyInstaller specs + two-zip build + update scripts.

## Open questions

(None currently tracked — all prior questions resolved. Re-open as
implementation surfaces new ones.)

