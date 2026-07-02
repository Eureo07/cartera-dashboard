@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo  Inversión Dashboard - Servidor
echo ========================================
echo.
echo  Cerrando procesos en puerto 5000...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do (
    echo  Matando PID %%p...
    taskkill /F /PID %%p >nul 2>&1
)
timeout /t 2 /nobreak >nul
echo.
echo  Iniciando servidor...
set DASHBOARD_PASSWORD=Eureo2026
start "Dashboard Server" cmd /c "python server.py & pause"
echo.
echo  Servidor iniciado. Abre http://localhost:5000/dashboard.html
echo  Usuario: admin  /  Contraseña: la configurada
echo  Cierra esta ventana para no acumular procesos.
