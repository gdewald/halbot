# Cloudflare R2 — `/halbot-stats` infra

Provisions the Cloudflare side of the [`/halbot-stats`](../../docs/plans/drafts/020-static-stats-publish.md) public snapshot pipeline:

- one R2 bucket
- one Cloudflare-managed custom domain (`<subdomain>.<apex_domain>`)
- TLS via Cloudflare Universal SSL (free, auto-provisioned)

S3-compat credentials for the daemon are minted out-of-band by [`scripts/apply-r2-secrets.ps1`](../../scripts/apply-r2-secrets.ps1) so they never land in Terraform state.

## Two-token model

There are **two** distinct Cloudflare credentials involved. Don't confuse them:

| Credential | Who creates it | What scopes | Where it lives | Used by |
|---|---|---|---|---|
| **Infra token** | You, manually in CF dashboard | Account → R2 Edit, Account → API Tokens Edit, Zone → DNS Edit (apex zone only) | `CLOUDFLARE_API_TOKEN` env var on your workstation | `terraform apply` |
| **R2 S3 keys** | Terraform (`cloudflare_account_token` in `token.tf`) | Object Read & Write on the one bucket; optional IP clamp | tfstate (sensitive); DPAPI-encrypted under `HKLM\SOFTWARE\Halbot\Secrets` after `apply-r2-secrets.ps1` | Daemon at runtime (boto3) |

The infra token is yours; rotate via CF dashboard. The R2 S3 keys are derived from the Terraform-managed account token: `Access Key ID = token.id`, `Secret Access Key = sha256(token.value)` per Cloudflare's R2 S3 model. Rotate by `terraform apply -replace=cloudflare_account_token.r2_bot` then re-run `apply-r2-secrets.ps1`.

## Manual prerequisites (one-time)

1. **Cloudflare account exists** for your apex domain. If the apex isn't on Cloudflare yet, either move the full zone or delegate just `<subdomain>` via NS records — your call.
2. **Create the infra token** in the CF dashboard → My Profile → API Tokens → Create Token. Custom token with the scopes in the table above (note: needs **API Tokens Edit** so Terraform can create the bot's R2 token). Save as `CLOUDFLARE_API_TOKEN` env var (PowerShell: `$env:CLOUDFLARE_API_TOKEN = "..."`).
3. **Capture IDs** — Cloudflare Account ID (sidebar on Account Home → API), Zone ID for your apex (zone overview right sidebar). Drop both, plus your apex domain, into `terraform.tfvars`.

## Apply

```powershell
# From repo root:
cp infra/cloudflare/terraform.tfvars.example infra/cloudflare/terraform.tfvars
# edit infra/cloudflare/terraform.tfvars
$env:CLOUDFLARE_API_TOKEN = "<your-infra-token>"

terraform -chdir=infra/cloudflare init
terraform -chdir=infra/cloudflare apply
```

`terraform apply` blocks until the custom-domain resource sees the DNS verification CNAME resolve. If the apex is on Cloudflare, this is automatic and quick. If the apex is on a third-party DNS provider with subdomain delegation, you may need to add a CNAME at the parent zone manually.

After apply succeeds, verify SSL:

```powershell
curl -I https://stats.<apex>/
# Expect: HTTP/2 404 (bucket empty), valid cert.
```

## Push secrets + config to the daemon

```powershell
# Elevated PowerShell:
scripts\apply-r2-secrets.ps1
```

What it does:

1. Reads `terraform output -json` to learn `account_id`, `bucket_name`, `endpoint_url`, `public_url`.
2. Reads `terraform output -raw s3_access_key_id` and `s3_secret_access_key` (Terraform-derived from the `cloudflare_account_token` resource).
3. DPAPI-encrypts both as `REG_BINARY` values under `HKLM\SOFTWARE\Halbot\Secrets`. Same format `halbot/secrets.py` reads — no daemon RPC needed, so this works even if the service is stopped.
4. Writes `stats_s3_endpoint`, `stats_s3_bucket`, `stats_public_url`, `stats_publisher=s3` into `HKLM\SOFTWARE\Halbot\Config` via `reg add`.
5. Restarts the daemon so it picks up the new config.

End-to-end: **`terraform apply` + `apply-r2-secrets.ps1` = daemon ready**. No dashboard clicks for the runtime credentials.

## Verify

```powershell
reg query HKLM\SOFTWARE\Halbot\Config /v stats_s3_bucket    # should print the bucket name
curl -I https://stats.<apex>/                                # 404 + valid cert
# In Discord: /halbot-stats — embed reply with the URL.
```

## Teardown

```powershell
terraform -chdir=infra/cloudflare destroy
```

Then in elevated PowerShell:

```powershell
reg delete HKLM\SOFTWARE\Halbot\Secrets /v R2_ACCESS_KEY_ID /f
reg delete HKLM\SOFTWARE\Halbot\Secrets /v R2_SECRET_ACCESS_KEY /f
reg delete HKLM\SOFTWARE\Halbot\Config  /v stats_s3_bucket   /f
reg delete HKLM\SOFTWARE\Halbot\Config  /v stats_s3_endpoint /f
reg delete HKLM\SOFTWARE\Halbot\Config  /v stats_public_url  /f
reg delete HKLM\SOFTWARE\Halbot\Config  /v stats_publisher   /f
```

## State file

Local backend; `terraform.tfstate` is gitignored. It contains nothing sensitive (the bot S3 keys live in DPAPI, not state). Remote backend deferred — R2-hosted TF state is chicken-and-egg.
