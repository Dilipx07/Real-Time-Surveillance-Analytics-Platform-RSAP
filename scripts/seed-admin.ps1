param(
    [string]$EnvFile = ".\.env",
    [string[]]$ComposeFile = @(".\infra\docker-compose.yml")
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
$args += @("exec", "webapp-backend", "python", "scripts/seed.py")

& docker @args

if ($LASTEXITCODE -ne 0) {
    throw "Admin seed failed with exit code $LASTEXITCODE"
}
