# R2 S3-compatible API token, scoped to one bucket, object read+write.
#
# Cloudflare's S3-compatible token model:
#   - access_key_id     = cloudflare_account_token.r2_bot.id
#   - secret_access_key = sha256(cloudflare_account_token.r2_bot.value)
# (See outputs.tf for the wiring; tfstate holds `value` sensitively, but
# the SHA-256 step happens in terraform output, not in HCL state.)
#
# permission_groups are global Cloudflare IDs (stable across accounts).
# Looked up via `GET /accounts/<id>/tokens/permission_groups` if you ever
# need to confirm; the comments below are the human-readable names.

locals {
  # R2 object permission groups (global IDs, see CF dashboard ->
  # API Tokens -> Permission Groups).
  r2_perm_group_write = "2efd5506f9c8494dacb1fa10a3e7d5b6" # Workers R2 Storage Bucket Item Write
  r2_perm_group_read  = "6a018a9f2fc74eb6b293b0c548f38b39" # Workers R2 Storage Bucket Item Read

  # Resource scope: one bucket, by full edge identifier. Format is
  #   com.cloudflare.edge.r2.bucket.<account_id>_<jurisdiction>_<bucket_name>
  r2_bucket_resource_key = format(
    "com.cloudflare.edge.r2.bucket.%s_%s_%s",
    var.cloudflare_account_id,
    var.bucket_jurisdiction,
    cloudflare_r2_bucket.halbot_stats.name,
  )
}

resource "cloudflare_account_token" "r2_bot" {
  account_id = var.cloudflare_account_id
  name       = var.bucket_key_name

  policies = [{
    effect = "allow"
    permission_groups = [
      { id = local.r2_perm_group_write },
      { id = local.r2_perm_group_read },
    ]
    resources = jsonencode({
      (local.r2_bucket_resource_key) = "*"
    })
  }]

  # Optional IP clamp. Empty list -> omit `condition` entirely.
  condition = length(var.token_allowed_ips) > 0 ? {
    request_ip = {
      in = var.token_allowed_ips
    }
  } : null
}
