#Requires -Version 5.1
# Regenerate gRPC stubs from proto/mgmt.proto into halbot/_gen/.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root "halbot\_gen"
New-Item -ItemType Directory -Force -Path $out | Out-Null
if (-not (Test-Path (Join-Path $out "__init__.py"))) {
    Set-Content -Path (Join-Path $out "__init__.py") -Value "# generated stubs package"
}
Push-Location $root
try {
    uv run python -m grpc_tools.protoc `
        -I proto `
        --python_out=halbot/_gen `
        --grpc_python_out=halbot/_gen `
        proto/mgmt.proto
    # Fix relative import in generated _grpc.py
    $grpcFile = Join-Path $out "mgmt_pb2_grpc.py"
    if (Test-Path $grpcFile) {
        (Get-Content $grpcFile) `
            -replace '^import mgmt_pb2 as mgmt__pb2', 'from . import mgmt_pb2 as mgmt__pb2' `
            | Set-Content $grpcFile
    }
    Write-Host "Generated stubs in $out"
} finally {
    Pop-Location
}
