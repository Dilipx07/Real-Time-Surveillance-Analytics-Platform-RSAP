param(
    [string]$BackendUrl = "http://localhost:8000",
    [string]$FileServerUrl = "http://localhost:8002",
    [string]$WebappUrl = "http://localhost:3000",
    [string]$DesktopBackendUrl = "http://127.0.0.1:8001",
    [string]$DesktopFrontendUrl = "http://127.0.0.1:1420",
    [switch]$SkipFrontend,
    [switch]$SkipDesktop
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

function Read-DotEnv {
    $values = @{}
    if (Test-Path ".\.env") {
        Get-Content ".\.env" | ForEach-Object {
            $line = $_.Trim()
            if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
                $key, $value = $line.Split("=", 2)
                $values[$key.Trim()] = $value.Trim().Trim('"').Trim("'")
            }
        }
    }
    return $values
}

function Assert-HttpOk {
    param([string]$Name, [string]$Url)
    try {
        $response = Invoke-WebRequest -Uri $Url -Method Get -TimeoutSec 10 -UseBasicParsing
        if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 400) {
            throw "HTTP $($response.StatusCode)"
        }
        Write-Host "[pass] $Name"
    } catch {
        Write-Host "[fail] $Name - $($_.Exception.Message)"
        throw
    }
}

function Assert-RestOk {
    param([string]$Name, [string]$Url)
    try {
        Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 10 | Out-Null
        Write-Host "[pass] $Name"
    } catch {
        Write-Host "[fail] $Name - $($_.Exception.Message)"
        throw
    }
}

Assert-RestOk "webapp-backend /health" "$BackendUrl/health"
Assert-RestOk "file-server /health" "$FileServerUrl/health"

if (-not $SkipFrontend) {
    Assert-HttpOk "webapp frontend HTTP 200" $WebappUrl
}

if (-not $SkipDesktop) {
    Assert-RestOk "desktop-backend /health" "$DesktopBackendUrl/health"
    Assert-HttpOk "desktop frontend HTTP 200" $DesktopFrontendUrl
}

$envValues = Read-DotEnv
if (-not $envValues.ContainsKey("ADMIN_EMAIL") -or -not $envValues.ContainsKey("ADMIN_PASSWORD")) {
    throw "ADMIN_EMAIL and ADMIN_PASSWORD must be set in .env for login smoke."
}

$loginBody = @{
    email = $envValues["ADMIN_EMAIL"]
    password = $envValues["ADMIN_PASSWORD"]
    device_fingerprint = "rsap-smoke-test"
} | ConvertTo-Json

try {
    $login = Invoke-RestMethod -Uri "$BackendUrl/api/v1/auth/login" -Method Post -ContentType "application/json" -Body $loginBody -TimeoutSec 15
    $accessToken = $login.data.access_token
    $sessionToken = $login.data.session_token
    if (-not $accessToken -or -not $sessionToken) {
        throw "Login response did not contain both access_token and session_token"
    }
    Write-Host "[pass] admin login returned JWT and X-Session-Token"

    $headers = @{
        Authorization = "Bearer $accessToken"
        "X-Session-Token" = $sessionToken
    }
    Invoke-RestMethod -Uri "$BackendUrl/api/v1/auth/me" -Method Get -Headers $headers -TimeoutSec 10 | Out-Null
    Write-Host "[pass] protected central request accepted dual-token headers"
} catch {
    Write-Host "[fail] admin login or protected request - $($_.Exception.Message)"
    throw
}

if (-not $SkipDesktop) {
    try {
        Invoke-RestMethod -Uri "$DesktopBackendUrl/health" -TimeoutSec 10 | Out-Null
        Write-Host "[pass] desktop degraded/online health reachable"
    } catch {
        Write-Host "[fail] desktop login readiness - $($_.Exception.Message)"
        throw
    }
}

Write-Host "End-user smoke checks completed."
