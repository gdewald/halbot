# cloudflare_r2_custom_domain creates the verification CNAME automatically
# under the supplied zone — no extra cloudflare_record resources needed.
#
# If the apex zone is hosted outside Cloudflare (subdomain NS delegation),
# the user has to add the CNAME themselves and `terraform apply` will hang
# on the custom-domain resource until DNS resolves. Plan for the
# subdomain-delegation case is documented in README.md.
