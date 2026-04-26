variable "cloudflare_account_id" {
  description = "Cloudflare account ID (dashboard sidebar → Account Home → API)."
  type        = string
}

variable "cloudflare_zone_id" {
  description = "Zone ID of the apex domain hosting the stats subdomain."
  type        = string
}

variable "apex_domain" {
  description = "Apex domain whose Cloudflare zone hosts the stats subdomain (e.g. example.com)."
  type        = string

  validation {
    condition     = length(var.apex_domain) > 0 && !can(regex("^https?://", var.apex_domain))
    error_message = "apex_domain must be a bare domain (no scheme, e.g. example.com)."
  }
}

variable "subdomain" {
  description = "Hostname label for the public stats site, joined to apex_domain."
  type        = string
  default     = "stats"
}

variable "bucket_name" {
  description = "R2 bucket name. Must be globally unique within your account."
  type        = string
  default     = "halbot-stats"
}

variable "bucket_key_name" {
  description = "R2 bucket API key name. Must be globally unique within your account."
  type        = string
  default     = "halbot-daemon"
}

variable "r2_location_hint" {
  description = "Optional R2 jurisdiction hint (e.g. wnam, enam, weur, eeur, apac). Empty = automatic."
  type        = string
  default     = ""
}

variable "bucket_jurisdiction" {
  description = "R2 bucket jurisdiction segment in the policy resource path. 'default' for non-jurisdictional buckets; 'eu' or 'fedramp' otherwise."
  type        = string
  default     = "default"
}

variable "token_allowed_ips" {
  description = "Optional CIDR allowlist clamping where the R2 S3 token can be used. Empty list = no IP restriction. Single-host example: [\"203.0.113.5/32\"]."
  type        = list(string)
  default     = []
}
