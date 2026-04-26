# The infra token (broad scopes) is read from the CLOUDFLARE_API_TOKEN env
# var. See README.md for the scope list. Terraform uses this token to
# provision the bucket + custom domain + bot token; runtime uploads use the
# narrow bot token instead.
provider "cloudflare" {}
