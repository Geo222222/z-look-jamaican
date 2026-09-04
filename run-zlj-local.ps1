# Requires PowerShell 5+. Starts the local read-only ZLJ operator console.
# Usage: .\run-zlj-local.ps1

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

if (-not (Test-Path (Join-Path $RepoRoot "autonomous_kernel\cli.py"))) {
    Write-Error "This script must run from the z-look-jamaican repository root. Current: $RepoRoot"
}

$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) {
    Write-Error "Python is not on PATH. Install Python 3.9+ and retry. This script does not install software."
}

$VersionText = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
$Parts = $VersionText.Split(".")
if ([int]$Parts[0] -lt 3 -or ([int]$Parts[0] -eq 3 -and [int]$Parts[1] -lt 9)) {
    Write-Error "Python 3.9+ is required. Found $VersionText"
}

$Port = 3000
$Bind = "127.0.0.1"
$Listener = netstat -ano | Select-String "LISTENING" | Select-String ":${Port}\s"
if ($Listener) {
    Write-Error "Port $Port is already in use:`n$Listener`nStop the existing listener (often Docker container z-look-jamaican-command-center-1) before starting a duplicate backend."
}

$env:ZLOOK_SOURCE_ROOT = $RepoRoot
$env:ZLOOK_OPERATOR_MUTATIONS_ENABLED = "false"
$MonitorDir = Join-Path $RepoRoot "monitor"

Write-Host "ZLJ local operator console"
Write-Host "  repo     $RepoRoot"
Write-Host "  backend  http://${Bind}:${Port}"
Write-Host "  frontend http://${Bind}:${Port}"
Write-Host "  health   http://${Bind}:${Port}/api/health"
Write-Host "Mode: SHADOW ONLY · capital authority NONE · live execution LOCKED"
Write-Host "Starting uvicorn (Ctrl+C stops both frontend and backend; they are the same process)."

& python -m uvicorn app.main:app --app-dir $MonitorDir --host $Bind --port $Port
