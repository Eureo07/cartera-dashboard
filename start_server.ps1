$host.ui.RawUI.WindowTitle = "Dashboard Cartera - Servidor"
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Inversión Dashboard - Servidor" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Kill any existing process on port 5000
$existing = Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Cerrando proceso en puerto 5000 (PID $($existing.OwningProcess))..." -ForegroundColor Yellow
    Stop-Process -Id $existing.OwningProcess -Force
    Start-Sleep -Seconds 2
}

# Kill stale python processes (except current)
$currentPid = $pid
Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object { $_.Id -ne $currentPid } | Stop-Process -Force

Write-Host "Iniciando servidor..." -ForegroundColor Green
$env:DASHBOARD_PASSWORD = "Eureo2026"
Set-Location $PSScriptRoot
python server.py
pause
