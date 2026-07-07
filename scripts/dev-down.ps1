param(
    [string]$ComposeFile = ".\infra\docker-compose.yml",
    [switch]$Volumes
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

$args = @("compose", "-f", $ComposeFile, "down")
if ($Volumes) {
    $args += "--volumes"
}

Write-Host "Stopping RSAP Docker services"
& docker @args
if ($LASTEXITCODE -ne 0) {
    throw "docker compose down failed with exit code $LASTEXITCODE"
}
