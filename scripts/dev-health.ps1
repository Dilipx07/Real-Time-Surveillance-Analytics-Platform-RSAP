param(
    [string]$BackendUrl = "http://localhost:8000",
    [string]$FileServerUrl = "http://localhost:8002",
    [string]$WebappUrl = "http://localhost:3000",
    [string]$DesktopBackendUrl = "http://127.0.0.1:8001",
    [string]$DesktopFrontendUrl = "http://localhost:5173",
    [switch]$SkipFrontend,
    [switch]$SkipDesktop
)

$ErrorActionPreference = "Stop"

function Test-HttpJson {
    param([string]$Name, [string]$Url)
    try {
        $response = Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 10
        Write-Host "[ok] $Name $Url"
        return $response
    } catch {
        Write-Host "[fail] $Name $Url - $($_.Exception.Message)"
        throw
    }
}

function Test-HttpStatus {
    param([string]$Name, [string]$Url)
    try {
        $response = Invoke-WebRequest -Uri $Url -Method Get -TimeoutSec 10 -UseBasicParsing
        if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 400) {
            throw "HTTP $($response.StatusCode)"
        }
        Write-Host "[ok] $Name $Url"
    } catch {
        Write-Host "[fail] $Name $Url - $($_.Exception.Message)"
        throw
    }
}

Test-HttpJson "webapp-backend health" "$BackendUrl/health" | Out-Null
Test-HttpJson "file-server health" "$FileServerUrl/health" | Out-Null

if (-not $SkipFrontend) {
    Test-HttpStatus "webapp-frontend" $WebappUrl
}

if (-not $SkipDesktop) {
    Test-HttpJson "desktop-backend health" "$DesktopBackendUrl/health" | Out-Null
    Test-HttpStatus "desktop-frontend" $DesktopFrontendUrl
}

Write-Host "Health checks completed."
