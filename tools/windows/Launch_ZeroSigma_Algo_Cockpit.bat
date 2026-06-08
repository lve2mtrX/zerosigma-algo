@echo off
title ZeroSigma Algo Cockpit
color 0A

set "REPO=C:\Users\danca\Dropbox\Trading\ZeroSigma\zerosigma-algo"
set "URL=http://localhost:8501"
set "PORT=8501"

echo.
echo ========================================
echo   Starting ZeroSigma Algo Cockpit
echo ========================================
echo.

if not exist "%REPO%" (
echo ERROR: Repo folder not found:
echo %REPO%
echo.
pause
exit /b 1
)

cd /d "%REPO%"

if not exist "%REPO%\.venv\Scripts\activate.bat" (
echo ERROR: Python virtual environment not found:
echo %REPO%\.venv
echo.
pause
exit /b 1
)

call "%REPO%\.venv\Scripts\activate.bat"

echo Starting Streamlit on port %PORT%...
echo.

start "ZeroSigma Streamlit Server" /min cmd /d /c python -m streamlit run ".\src\app\streamlit_main.py" --server.port %PORT% --server.address 127.0.0.1 --server.headless true

echo Waiting for dashboard to start...
timeout /t 4 /nobreak >nul

echo Opening dashboard app window...
echo Close the dashboard window to stop Streamlit.
echo.

set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"

if exist "%EDGE%" (
start /wait "" "%EDGE%" --app="%URL%"
) else (
echo Edge not found. Opening default browser instead.
echo Auto-close will NOT work reliably with a normal browser tab.
start "" "%URL%"
pause
)

echo.
echo Dashboard window closed.
echo Stopping Streamlit on port %PORT%...
echo.

for /f "tokens=5" %%a in ('netstat -ano ^| findstr :%PORT% ^| findstr LISTENING') do (
taskkill /PID %%a /F >nul 2>&1
)

echo Done.
exit
