<#
.SYNOPSIS
  Pull R2 S3-compat credentials + bucket config from terraform state and
  push them into HKLM for the daemon.

.DESCRIPTION
  Bridges `terraform apply` (in infra/cloudflare/) and the daemon. Performs:
    1. Reads `terraform output -json` to learn account_id/bucket/endpoint/public_url.
    2. Reads sensitive `terraform output -raw s3_access_key_id` and
       `terraform output -raw s3_secret_access_key` -- terraform derives
       both from the cloudflare_account_token resource (id and SHA-256(value)
       respectively, per Cloudflare's R2 S3 token model).
    3. DPAPI-encrypts the keys (LocalMachine scope) and writes them as
       REG_BINARY values under HKLM\SOFTWARE\Halbot\Secrets -- same format
       halbot/secrets.py reads. No daemon RPC required; works even with the
       service stopped.
    4. Writes stats_publisher / stats_s3_endpoint / stats_s3_bucket /
       stats_public_url to HKLM\SOFTWARE\Halbot\Config via reg add.
    5. Restarts the halbot service so the new config + secrets take effect.

  Requires:
    - Elevated PowerShell (HKLM writes + DPAPI LocalMachine scope).
    - `terraform` on PATH; `infra/cloudflare/terraform.tfstate` present
      (with cloudflare_account_token applied).

.NOTES
  Re-running this script overwrites the existing key pair. To rotate:
  `terraform -chdir=infra/cloudflare apply -replace=cloudflare_account_token.r2_bot`
  then re-run this script.
#>
[CmdletBinding()]
param(
    [string]$InfraDir = "infra/cloudflare",
    [switch]$SkipServiceRestart
)

$ErrorActionPreference = "Stop"

function Require-Admin {
    $id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object System.Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "This script must run from an elevated PowerShell (HKLM writes)."
    }
}

function Get-TerraformOutputs {
    param([string]$Dir)
    $raw = & terraform -chdir="$Dir" output -json
    if ($LASTEXITCODE -ne 0) {
        throw "terraform -chdir='$Dir' output -json failed (state missing? run apply first)"
    }
    return ($raw | ConvertFrom-Json)
}

function Get-TfRaw {
    param([string]$Dir, [string]$Name)
    $val = & terraform -chdir="$Dir" output -raw $Name
    if ($LASTEXITCODE -ne 0) {
        throw "terraform -chdir='$Dir' output -raw $Name failed (output not defined?)"
    }
    if ([string]::IsNullOrWhiteSpace($val)) {
        throw "terraform output '$Name' was empty"
    }
    return $val
}

function Set-DpapiSecret {
    <#
      DPAPI-encrypts $Value (LocalMachine scope) and writes it as a
      REG_BINARY value at HKLM\SOFTWARE\Halbot\Secrets\$Name. Format
      matches halbot/secrets.py -- daemon decrypts via the same
      Win32 CryptUnprotectData call.
    #>
    param(
        [string]$Name,
        [string]$Value
    )
    Add-Type -AssemblyName System.Security
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    $blob = [System.Security.Cryptography.ProtectedData]::Protect(
        $bytes,
        $null,
        [System.Security.Cryptography.DataProtectionScope]::LocalMachine
    )
    $regPath = "HKLM:\SOFTWARE\Halbot\Secrets"
    if (-not (Test-Path -LiteralPath $regPath)) {
        New-Item -Path $regPath -Force | Out-Null
    }
    New-ItemProperty -Path $regPath -Name $Name -Value $blob `
        -PropertyType Binary -Force | Out-Null
}

function Set-RegConfig {
    param(
        [string]$Name,
        [string]$Value
    )
    & reg add "HKLM\SOFTWARE\Halbot\Config" /v $Name /t REG_SZ /d $Value /f | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "reg add HKLM\SOFTWARE\Halbot\Config /v $Name failed ($LASTEXITCODE)"
    }
}

# -- main ----------------------------------------------------------------

Require-Admin

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$infra    = Resolve-Path (Join-Path $repoRoot $InfraDir)
Write-Host "Reading terraform outputs from $infra"
$out = Get-TerraformOutputs -Dir $infra

$accountId  = $out.account_id.value
$bucketName = $out.bucket_name.value
$endpoint   = $out.endpoint_url.value
$publicUrl  = $out.public_url.value

Write-Host "Account ID  : $accountId"
Write-Host "Bucket      : $bucketName"
Write-Host "Endpoint    : $endpoint"
Write-Host "Public URL  : $publicUrl"

Write-Host "-> Reading R2 S3 credentials from terraform state"
$accessKey = Get-TfRaw -Dir $infra -Name "s3_access_key_id"
$secretKey = Get-TfRaw -Dir $infra -Name "s3_secret_access_key"

Write-Host "-> DPAPI-encrypting secrets to HKLM\SOFTWARE\Halbot\Secrets"
Set-DpapiSecret -Name "R2_ACCESS_KEY_ID"     -Value $accessKey
Set-DpapiSecret -Name "R2_SECRET_ACCESS_KEY" -Value $secretKey

Write-Host "-> Writing stats config to HKLM\SOFTWARE\Halbot\Config"
Set-RegConfig -Name "stats_publisher"   -Value "s3"
Set-RegConfig -Name "stats_s3_endpoint" -Value $endpoint
Set-RegConfig -Name "stats_s3_bucket"   -Value $bucketName
Set-RegConfig -Name "stats_s3_region"   -Value "auto"
Set-RegConfig -Name "stats_public_url"  -Value $publicUrl

if (-not $SkipServiceRestart) {
    Write-Host "-> Restarting halbot service to pick up new config"
    & sc.exe stop halbot | Out-Null
    Start-Sleep -Seconds 2
    & sc.exe start halbot | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Warning "sc start halbot returned $LASTEXITCODE" }
}

Write-Host "Done. Try /halbot-stats from Discord."
