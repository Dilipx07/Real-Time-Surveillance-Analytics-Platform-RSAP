param(
    [string]$EnvFile = ".\.env",
    [string[]]$ComposeFile = @(".\infra\docker-compose.yml", ".\infra\docker-compose.dev.yml"),
    [switch]$Volumes
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

if (-not (Test-Path $EnvFile)) {
    throw "Missing $EnvFile. Create it first: Copy-Item .\.env.example .\.env"
}

$args = @("compose", "--env-file", $EnvFile)
foreach ($file in $ComposeFile) {
    $args += @("-f", $file)
}
$args += "down"
if ($Volumes) {
    $args += "--volumes"
}

Write-Host "Stopping RSAP Docker services"
& docker @args
if ($LASTEXITCODE -ne 0) {
    throw "docker compose down failed with exit code $LASTEXITCODE"
}
