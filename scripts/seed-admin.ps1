param(
    [string]$BackendPath = ".\apps\webapp-backend"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

if (-not (Test-Path ".\.env")) {
    throw "Missing .env. Create it first: Copy-Item .\.env.example .\.env"
}

$python = Get-Command py -ErrorAction SilentlyContinue
if ($python) {
    & py -3.12 "$BackendPath\scripts\seed.py"
} else {
    & python "$BackendPath\scripts\seed.py"
}

if ($LASTEXITCODE -ne 0) {
    throw "Admin seed failed with exit code $LASTEXITCODE"
}
