output "account_id" {
  description = "Cloudflare account ID — feeds apply-r2-secrets.ps1 + the daemon endpoint URL."
  value       = var.cloudflare_account_id
}

output "bucket_name" {
  description = "R2 bucket name. Maps to stats_s3_bucket in HKLM\\SOFTWARE\\Halbot\\Config."
  value       = cloudflare_r2_bucket.halbot_stats.name
}

output "endpoint_url" {
  description = "boto3 endpoint_url for this bucket's account. Maps to stats_s3_endpoint."
  value       = "https://${var.cloudflare_account_id}.r2.cloudflarestorage.com"
}

output "public_url" {
  description = "Public base URL the daemon advertises in its Discord embed."
  value       = "https://${local.fqdn}/"
}

output "fqdn" {
  description = "Stats subdomain FQDN bound to the bucket via Cloudflare custom-domain."
  value       = local.fqdn
}

output "custom_domain_status_hint" {
  description = "Where to verify SSL is Active in the dashboard."
  value       = "https://dash.cloudflare.com/${var.cloudflare_account_id}/r2/default/buckets/${cloudflare_r2_bucket.halbot_stats.name}/settings"
}

# S3-compatible credentials derived from the cloudflare_account_token
# resource. boto3 (or any S3 client) consumes these directly.
#   access_key_id     = token id (hex, ~32 chars)
#   secret_access_key = sha256(token value), hex-encoded
# Both pulled by scripts/apply-r2-secrets.ps1 via `terraform output -raw`.
output "s3_access_key_id" {
  description = "R2 S3-compatible Access Key ID (= cloudflare_account_token id)."
  value       = cloudflare_account_token.r2_bot.id
  sensitive   = true
}

output "s3_secret_access_key" {
  description = "R2 S3-compatible Secret Access Key (= sha256(token value), hex)."
  value       = sha256(cloudflare_account_token.r2_bot.value)
  sensitive   = true
}
