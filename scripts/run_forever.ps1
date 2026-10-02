# Keep the Discord bot running on Windows (Task Scheduler or manual).
# Example Task Scheduler action:
#   powershell.exe -ExecutionPolicy Bypass -File "D:\GITHUB\D_BOT\scripts\run_forever.ps1"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

while ($true) {
    Write-Host "$(Get-Date -Format o) starting bot..."
    & $Python (Join-Path $Root "bot.py")
    Write-Host "$(Get-Date -Format o) bot exited; restarting in 5s..."
    Start-Sleep -Seconds 5
}
