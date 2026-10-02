# Starts InfoBot and its public tunnel, each in its own window, independent of any editor.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1            start (or restart) everything, then check it
#   powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1 -Stop      stop both
#   powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1 -NoCheck   start without the pre-flight check
param(
    [switch]$Stop,
    [switch]$NoCheck
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Stop-Bot {
    # whatever listens on 8000 is an old copy of the bot
    Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    Get-Process ngrok -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}

if ($Stop) {
    Stop-Bot
    Write-Host "Stopped the bot and the tunnel."
    return
}

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "No virtual environment at $python" }
if (-not (Test-Path (Join-Path $root ".env"))) { throw ".env is missing: the bot has no keys without it" }

$ngrok = (Get-Command ngrok -ErrorAction SilentlyContinue).Source
if (-not $ngrok) {
    $ngrok = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter ngrok.exe -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $ngrok) { throw "ngrok.exe not found. Install it and run 'ngrok config add-authtoken <token>' once." }

Stop-Bot
Start-Sleep -Seconds 1

Write-Host "Starting the bot..."
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "`"$root\scripts\run_server.cmd`""

Write-Host "Waiting for it to answer..."
$ready = $false
for ($i = 0; $i -lt 40; $i++) {
    try {
        if ((Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2).StatusCode -eq 200) { $ready = $true; break }
    } catch { Start-Sleep -Milliseconds 750 }
}
if (-not $ready) { throw "The bot did not come up in 30 seconds. Look at its window and logs\server.log." }

Write-Host "Starting the tunnel (127.0.0.1, not localhost: 'localhost' adds ~0.2 s per message on Windows)..."
Start-Process -FilePath $ngrok -ArgumentList "http", "127.0.0.1:8000"
Start-Sleep -Seconds 5

try {
    $tunnel = (Invoke-RestMethod -Uri "http://127.0.0.1:4040/api/tunnels").tunnels[0]
    Write-Host ""
    Write-Host "Public address: $($tunnel.public_url)"
    Write-Host "Meta webhook:   $($tunnel.public_url)/webhook"
} catch {
    Write-Warning "ngrok is not answering on :4040 yet. Check its window."
}

if (-not $NoCheck) {
    Write-Host ""
    & $python (Join-Path $root "scripts\check_demo.py")
}
