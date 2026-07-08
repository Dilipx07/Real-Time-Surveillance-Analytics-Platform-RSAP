param(
    [switch]$Build,
    [switch]$DependenciesOnly,
    [string[]]$ComposeFile = @(".\infra\docker-compose.yml", ".\infra\docker-compose.dev.yml")
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

if (-not (Test-Path ".\.env")) {
    throw "Missing .env. Create it first: Copy-Item .\.env.example .\.env"
}

$services = @("postgres", "redis", "minio")
if (-not $DependenciesOnly) {
    $services += @("webapp-backend", "file-server", "webapp-frontend")
}

$args = @("compose")
foreach ($file in $ComposeFile) {
    $args += @("-f", $file)
}
$args += @("up", "-d")
if ($Build) {
    $args += "--build"
}
$args += $services

Write-Host "Starting RSAP services: $($services -join ', ')"
& docker @args
if ($LASTEXITCODE -ne 0) {
    throw "docker compose up failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "RSAP URLs"
Write-Host "  Central webapp:  http://localhost:3000"
Write-Host "  Central API:     http://localhost:8000"
Write-Host "  File server:     http://localhost:8002"
Write-Host "  MinIO console:   http://localhost:9001"
Write-Host ""
Write-Host "Run .\scripts\dev-health.ps1 to verify service health."
