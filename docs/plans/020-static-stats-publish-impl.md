# Publish Static Stats Web View — Implementation Plan

## Context

Discord users on Halbot's private server want to see bot stats (latencies, soundboard usage, wake-word counts, top users, etc.) without joining the user's LAN or running the pywebview dashboard. Today the dashboard is reachable only by the bot operator via the tray app.

Solution: a `/halbot-stats` slash command that publishes a frozen snapshot of the existing React dashboard to a public URL and replies with that URL in a Discord embed. The static page reuses the existing React build with the live data baked in as JSON, so no API server, websocket, or auth surface is added.

**User-confirmed decisions:**
- **Hosting:** Cloudflare R2 (S3-compatible) upload via boto3, served on `stats.gdewald.com` via R2 Custom Domain (free SSL via Cloudflare).
- **DNS:** User-managed. Plan does **not** assume `gdewald.com` is hosted in any particular DNS provider; user handles whatever migration is needed (full-zone move to Cloudflare, subdomain NS delegation, etc.).
- **User IDs:** Resolved to current Discord display names at publish time.
- **Refresh:** On-demand only (throttled). No periodic loop.
- **Command:** Top-level public `/halbot-stats`, runnable by any server member.

## Approach

### 1. Snapshot pipeline (`halbot/stats_publisher.py`, NEW)

- `snapshot_stats(client) -> dict`
  - Calls `analytics.compute_dashboard_stats()` (the same path `GetStats` RPC uses, halbot/mgmt_server.py:272-326).
  - Runs the four fixed `analytics.query_stats()` aggregates that `Analytics.jsx` displays (event-kind mix, top sounds, top commands, top users).
  - Calls `db.db_list()` + `db.emoji_db_list_full()` for the soundboard table + emoji metadata, mirroring `bridge.py` `soundboard_list()` / `emoji_list()`.
  - Resolves user IDs in `top_users` and the embedded event feed to display names via `client.get_user(int(user_id))` / `guild.get_member()`. Cache lookups per snapshot. Fallback: `User#1234` short form, then raw ID if even that fails.
  - Returns `{schema_version: 1, generated_at_utc, stats, analytics, soundboard, emoji}`.

- `render_snapshot_html(template_html: str, snapshot: dict) -> str`
  - Locates `</head>` in `frontend/dist/index.html`; injects `<script>window.__STATS_SNAPSHOT__ = JSON.parse({js_string_literal});</script>` immediately before. JSON-encode then JS-string-quote to dodge `</script>` in any string field.

- `publish_now(client, force=False) -> PublishResult`
  - Throttle: in-process `_last_publish_at` timestamp; if `now - last < stats.min_publish_interval_seconds` and not `force`, return cached `PublishResult` (URL + cached_at).
  - Snapshot → copy `frontend_dist_dir()` tree to a tmp staging dir → rewrite `index.html` → hand staging dir to the configured publisher → return URL + `published_at`.
  - Fire `analytics.record('stats_publish', user_id=..., target='r2', latency_ms=..., bytes=...)`.

### 2. Publisher abstraction (`halbot/publishers/`, NEW)

- `__init__.py`: `Publisher` ABC with `publish(local_dir: Path) -> str` returning the public URL; `get_publisher(name) -> Publisher` factory keyed off `stats.publisher` config.
- `s3.py`: ships now. Walks `local_dir`, uploads each file via boto3 `s3.put_object(Bucket=..., Key=prefix+relpath, Body=..., ContentType=guess_type(...))`. Custom `endpoint_url` makes the same publisher work for AWS S3 and Cloudflare R2 — selected by whether `stats.s3_endpoint` is set.
- Returns `f"{stats.public_url}{stats.s3_key_prefix}index.html"` (or `/` if the host serves index automatically).
- `_stubs.py`: `FilesystemPublisher`, `GitHubPagesPublisher` placeholder classes raising `NotImplementedError("not yet implemented; see plan 019")`. Keeps the selector enum honest for later.

### 3. Frontend snapshot mode (`frontend/src/bridge.js`, MODIFIED)

`bridge.js` lines 1-37 currently checks for `window.pywebview` and falls through to a `STUB` dict in dev. Add a third branch ahead of the pywebview check:

```js
if (typeof window !== 'undefined' && window.__STATS_SNAPSHOT__) {
  const S = window.__STATS_SNAPSHOT__;
  return makeSnapshotBridge(S);  // resolved promises for read methods, no-ops for writes/streams
}
```

`makeSnapshotBridge` returns synchronous resolved values for read methods (`get_stats`, `query_stats`, `soundboard_list`, `emoji_list`, `health`) and inert no-ops for everything mutation/stream-shaped (`update_config`, `service_*`, `pop_event_batch`, `pop_log_batch`, `backlog_events`, `window_*`).

Export `IS_SNAPSHOT` const from `bridge.js`. Consumers:
- `Stats.jsx`: skip the 10s `REFRESH_MS` interval when `IS_SNAPSHOT`.
- `Analytics.jsx`: skip the 10s aggregate refresh AND the 500ms live-feed poll; hide the live-feed table entirely; render bar charts once from snapshot.
- New `SnapshotBanner.jsx`: small strip at top — "Static snapshot — generated {snapshot.generated_at_utc}". Only renders when `IS_SNAPSHOT`.

### 4. Slash command (`halbot/slash.py`, MODIFIED)

Add a flat top-level `/halbot-stats` command (no subcommands) following the `app_commands.Command` pattern used elsewhere in this file. Register at `register_slash` (slash.py:504-514).

Handler shape:
1. `await interaction.response.defer(thinking=True)` (publish + upload can take seconds).
2. `result = await asyncio.to_thread(stats_publisher.publish_now, client)` (boto3 is sync; thread it).
3. Build `ReplyPayload` (bot_ui.py:85-93): `mode=Mode.NOTED` (neutral/info color), title `"Stats"`, `description=f"Snapshot ready:\n{result.url}"`, `subtext=None`, `footer=f"Generated {iso_timestamp}{' (cached)' if result.cached else ''}"`.
4. `await send_halbot_reply(interaction.followup, payload=payload)` (bot_ui.py:125-163).
5. On publisher error: `mode=Mode.ERROR`, surface short error string (no stack) — boto3 errors get noisy, take only `e.__class__.__name__` + first line.

### 5. Config schema (`halbot/config.py`, MODIFIED)

Eight new fields in `DEFAULTS` + `SCHEMA` (config.py:15-271):

| Key | Default | Notes |
|---|---|---|
| `stats.publisher` | `"s3"` | Publisher selector. Other valid values fail fast for now. |
| `stats.s3_endpoint` | `""` | R2: `https://<account>.r2.cloudflarestorage.com`. AWS: leave empty. |
| `stats.s3_bucket` | `""` | Bucket name. Empty → `/halbot-stats` returns "not configured". |
| `stats.s3_region` | `"auto"` | R2 wants `auto`. |
| `stats.s3_key_prefix` | `""` | Optional path under bucket, e.g. `"halbot/"`. |
| `stats.public_url` | `""` | Public URL base, e.g. `https://stats.example.com/` or `https://pub-xxx.r2.dev/`. Trailing slash recommended. |
| `stats.min_publish_interval_seconds` | `60` | Throttle floor. Cached URL returned within this window. |
| `stats.user_id_treatment` | `"display_name"` | Fixed for now per user decision; left as a knob for later flexibility (`raw|display_name|hash|omit`). |

R2 credentials are secrets, not config. Add to `SetSecret` RPC handling (mirrors `DISCORD_TOKEN` pattern under `HKLM\SOFTWARE\Halbot\Secrets` as DPAPI-encrypted REG_BINARY):
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`

Boto3 client construction reads these via the existing secrets-getter (whatever the daemon already uses to fetch `DISCORD_TOKEN` at startup — same path).

### 6. Frontend assets bundled into daemon

`build_daemon.spec` (MODIFIED): add `('frontend/dist', 'frontend/dist')` to the spec's `datas` list, mirroring how `build_tray.spec` already does it. Daemon binary grows by the dist size (~few MB). Pass `-Clean` on the next build (CLAUDE.md "When to use -Clean": spec datas changed).

`halbot/paths.py` (MODIFIED): add helpers using the existing `_frozen()` pattern (paths.py:14-25):

```python
def frontend_dist_dir() -> Path:
    if _frozen():
        return Path(sys._MEIPASS) / "frontend" / "dist"
    return Path(__file__).resolve().parent.parent / "frontend" / "dist"
```

### 7. Dependencies (`pyproject.toml`, MODIFIED)

Add `boto3` to the `daemon` dep group. Heavy (~30MB unpacked) but standard, well-supported, and works for both R2 and AWS S3 with no fork or custom signer. Acceptable for a daemon already shipping faster-whisper + torch.

### 8. Files touched

**New:**
- `halbot/stats_publisher.py` — snapshot, render, throttle, orchestrator.
- `halbot/publishers/__init__.py` — `Publisher` ABC + `get_publisher()`.
- `halbot/publishers/s3.py` — boto3 uploader, S3 + R2 via custom endpoint.
- `halbot/publishers/_stubs.py` — placeholder classes for filesystem/github_pages.
- `frontend/src/SnapshotBanner.jsx` — "Static snapshot — generated …" banner.
- `tests/test_stats_publisher.py` — snapshot shape, HTML injection round-trip, throttle, display-name resolution fallback.
- `infra/cloudflare/versions.tf`, `providers.tf`, `variables.tf`, `r2.tf`, `dns.tf`, `token.tf`, `outputs.tf` — Terraform root module.
- `infra/cloudflare/terraform.tfvars.example` — example inputs.
- `infra/cloudflare/README.md` — bootstrap + apply + teardown instructions.
- `infra/cloudflare/.gitignore` — `*.tfstate*`, `.terraform/`, `terraform.tfvars`.
- `scripts/apply-r2-secrets.ps1` — reads `terraform output -json`, pushes secrets + config into daemon (HKLM via SetSecret RPC + `reg add`).

**Modified:**
- `halbot/slash.py` — register top-level `/halbot-stats` command at register_slash boundary (line 504-514).
- `halbot/config.py` — 8 new keys in DEFAULTS + SCHEMA (lines 15-271).
- `halbot/paths.py` — add `frontend_dist_dir()` helper (lines 14-25 pattern).
- `halbot/mgmt_server.py` — add R2 secret keys to `SetSecret` allowlist (find existing `DISCORD_TOKEN` handling).
- `build_daemon.spec` — bundle `frontend/dist/` as `datas`.
- `pyproject.toml` — add `boto3` to daemon group.
- `frontend/src/bridge.js` — snapshot-mode branch ahead of pywebview/STUB fallback (lines 32-38); export `IS_SNAPSHOT`.
- `frontend/src/Analytics.jsx` — gate live feed + polling on `!IS_SNAPSHOT`; show banner.
- `frontend/src/Stats.jsx` — gate refresh timer on `!IS_SNAPSHOT`; show banner.
- `CLAUDE.md` — document the new public-URL boundary as **not** an opt-out surface (per existing privacy policy section), just a snapshot pipeline.

### 9. Critical files

- [halbot/stats_publisher.py](halbot/stats_publisher.py) — NEW orchestrator + snapshot + HTML injection.
- [halbot/publishers/s3.py](halbot/publishers/s3.py) — NEW boto3 uploader (R2 + AWS).
- [halbot/slash.py:504](halbot/slash.py:504) — register `/halbot-stats` here.
- [frontend/src/bridge.js:32](frontend/src/bridge.js:32) — snapshot detection branch ahead of pywebview/STUB.
- [halbot/config.py:15](halbot/config.py:15) — 8 new DEFAULTS + SCHEMA entries.
- [build_daemon.spec](build_daemon.spec) — bundle `frontend/dist/`.

## Infrastructure (Terraform)

New root module `infra/cloudflare/` provisions the Cloudflare side end-to-end. Manual clicks limited to one bootstrap token + DNS migration (user-managed).

**Files (all NEW):**
- `versions.tf` — pin `terraform >= 1.5`, `cloudflare/cloudflare ~> 4.x`.
- `providers.tf` — Cloudflare provider auth from env var `CLOUDFLARE_API_TOKEN` (the **infra token**, see prereqs).
- `variables.tf` — inputs:
  - `cloudflare_account_id` (string, required)
  - `cloudflare_zone_id` (string, required)
  - `apex_domain` (string, required — no default; user supplies in `terraform.tfvars`)
  - `bucket_name` (string, default `"halbot-stats"`)
  - `subdomain` (string, default `"stats"`)
- `r2.tf` — `cloudflare_r2_bucket` + `cloudflare_r2_custom_domain` resources binding `stats.gdewald.com` to the bucket. Cloudflare auto-provisions SSL.
- `dns.tf` — any DNS records the R2 custom-domain resource doesn't auto-create (verification CNAME etc.). May be empty if R2 custom-domain handles everything; resource exists for explicit control.
- `token.tf` — `cloudflare_api_token` scoped to the **bucket only**: permission group "Workers R2 Storage Bucket Item Write" + "Workers R2 Storage Bucket Item Read", resource scope = `com.cloudflare.api.account.<account_id>:r2-bucket:<bucket_name>`. No zone perms, no account-wide R2 perms, no other buckets.
- `outputs.tf` — emits `endpoint_url` (`https://<account_id>.r2.cloudflarestorage.com`), `bucket_name`, `public_url` (`https://stats.gdewald.com/`), and credentials marked `sensitive = true`. **See caveat below re: S3-compat key derivation.**
- `terraform.tfvars.example` — example values, no secrets.
- `README.md` — bootstrap, apply, secret-push, teardown instructions.
- `.gitignore` — `*.tfstate*`, `.terraform/`, `terraform.tfvars`.

**Caveat — S3-compat keys (flagged for impl-time verification):** Cloudflare R2 supports two auth surfaces: native CF API tokens AND S3-compatible Access Key ID + Secret pairs (what boto3 uses). The v4 provider's `cloudflare_api_token` resource emits a CF API token; whether it also yields the S3-compat key pair, or whether a `null_resource` + `local-exec` calling the CF API endpoint for R2 temp-access credentials is needed, must be verified during implementation. If a separate API call is needed, the `local-exec` reads the just-created token, calls CF, parses access_key_id + secret, and writes them to a sensitive output. Either way: keys never land in plaintext anywhere outside terraform state and the daemon's DPAPI store.

**State file:** local backend (`infra/cloudflare/terraform.tfstate`), gitignored. Contains tokens in plaintext — file ACLs only. Remote backend deferred (R2-hosted TF state is chicken-and-egg).

**Helper script `scripts/apply-r2-secrets.ps1` (NEW):** elevated PowerShell. Runs `terraform -chdir=infra/cloudflare output -json`, parses `r2_access_key_id` + `r2_secret_access_key`, pushes via daemon `SetSecret` RPC (mirrors how `DISCORD_TOKEN` ships). Also writes `stats.s3_endpoint`, `stats.s3_bucket`, `stats.public_url` to `HKLM\SOFTWARE\Halbot\Config` via `reg add`. End-to-end: one `terraform apply` + one `apply-r2-secrets.ps1` run = daemon ready.

## Manual setup prerequisites (user, one-time)

Two distinct Cloudflare API tokens are involved:

- **Infra token** — user creates manually in CF dashboard. Used by Terraform to provision bucket + custom domain + DNS + the bot token. Broad scope (R2 Edit, API Tokens Edit, DNS Edit on apex zone).
- **Bot token** — Terraform creates it. Used by daemon at runtime. Narrow scope (read+write objects in the one bucket only).

Steps:

1. **Cloudflare account** + **DNS migration**: user-managed. `stats.gdewald.com` must resolve through Cloudflare DNS — full-zone move or subdomain NS delegation, user's call.
2. **Create the infra token**: CF dashboard → My Profile → API Tokens → Create Token. Scopes: Account → R2 Edit, Account → API Tokens Edit, Zone → DNS Edit (your apex zone only). Export as `CLOUDFLARE_API_TOKEN` env var.
3. **Capture IDs**: Cloudflare Account ID (dashboard sidebar) + Zone ID for your apex (zone overview page right sidebar) + your apex domain. Drop into `infra/cloudflare/terraform.tfvars`.
4. `cd infra/cloudflare && terraform init && terraform apply` — provisions infra + emits the bot token as a sensitive output.
5. `scripts\apply-r2-secrets.ps1` (elevated) — pushes the bot token (as S3-compat key pair) + config to HKLM for the daemon to consume.

## Verification

0. **Infra**: `cd infra/cloudflare && terraform plan` → no diff after `apply`. CF dashboard shows bucket exists, custom domain SSL = "Active" (not "Initializing"). Bot token visible in CF dashboard with bucket-only scope.
1. Build frontend: `cd frontend; npm run build` → confirms `frontend/dist/` exists.
2. Build + deploy daemon: `scripts\deploy.ps1 -Daemon -Clean` (Clean required because `build_daemon.spec` datas changed).
3. Push secrets + config: `scripts\apply-r2-secrets.ps1` (elevated). Verify via `reg query HKLM\SOFTWARE\Halbot\Config /v stats.s3_bucket`.
4. Smoke-test custom domain: `curl -I https://stats.gdewald.com/` returns 404 (bucket empty) with valid SSL cert. SSL fail or DNS unresolvable → fix DNS before going further.
5. From Discord, run `/halbot-stats`. Confirm embed reply with URL.
6. Tail `%ProgramData%\Halbot\logs\halbot.log` for `[stats_publisher]` lines — should show snapshot ms, upload ms, file count.
7. `curl -I {url}` — confirm 200 OK + correct content-type.
8. Browse the URL in a real browser:
   - Stats.jsx cards render with current values.
   - Analytics bar charts render.
   - Live event feed is hidden.
   - SnapshotBanner shows "generated {timestamp}".
   - Top Users column shows display names, not 18-digit IDs.
9. Re-run `/halbot-stats` within 60 s — embed footer says `(cached)`, no new R2 uploads (check R2 dashboard or `aws s3 ls --endpoint-url=...`).
10. Wait 61 s, re-run — fresh upload, new timestamp.

## Post-approval housekeeping

After ExitPlanMode + user approval, per CLAUDE.md convention: copy this plan to `docs/plans/drafts/019-static-stats-publish.md` for in-repo tracking; promote to `docs/plans/019-static-stats-publish-impl.md` after merge.

## Non-goals

- Real-time updates (no websocket/SSE on the static page).
- Auth/login on the public URL — URL is the secret. Friends with link see stats.
- Multi-tenant or per-viewer views.
- Mobile-specific layout (existing CSS carries over).
- Opt-out / consent UI per CLAUDE.md privacy policy section.
- Filesystem / GitHub Pages / cloudflared publishers — interface stubbed, implementations deferred.
- Periodic publish loop — deferred until a need surfaces.
