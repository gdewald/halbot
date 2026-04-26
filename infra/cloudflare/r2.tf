locals {
  fqdn = "${var.subdomain}.${var.apex_domain}"
}

resource "cloudflare_r2_bucket" "halbot_stats" {
  account_id = var.cloudflare_account_id
  name       = var.bucket_name
  location   = var.r2_location_hint != "" ? var.r2_location_hint : null
}

# Bind the R2 bucket to a Cloudflare-managed custom domain. Cloudflare
# auto-issues a TLS cert (Universal SSL) for the FQDN; "Active" status
# typically reaches the dashboard within a few minutes.
resource "cloudflare_r2_custom_domain" "halbot_stats" {
  account_id  = var.cloudflare_account_id
  bucket_name = cloudflare_r2_bucket.halbot_stats.name
  domain      = local.fqdn
  zone_id     = var.cloudflare_zone_id
  enabled     = true
  min_tls     = "1.2"
}
