@echo off
REM ---------------------------------------------------------------------------
REM Wrapper for the scheduled refresh. Task Scheduler starts jobs in
REM C:\Windows\System32 regardless of what you set, and refresh_all.py resolves
REM dashboard/data.json relative to the working directory, so the cd is not
REM optional.
REM
REM Writes a timestamped log per run and keeps the last 30.
REM ---------------------------------------------------------------------------
setlocal

set REPO=C:\Solent Marketshare\marketshare_project\marketshare
set PY=C:\Users\ruari\AppData\Local\Python\bin\python.exe
set LOGDIR=%REPO%\logs

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

REM sortable yyyy-MM-dd_HHmm stamp, locale-independent
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmm"') do set STAMP=%%i
set LOG=%LOGDIR%\refresh_%STAMP%.log

cd /d "%REPO%" || exit /b 1

echo ===== refresh started %DATE% %TIME% ===== > "%LOG%"
"%PY%" scripts\refresh_all.py db\marketshare.db >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%
echo ===== finished rc=%RC% %DATE% %TIME% ===== >> "%LOG%"

REM keep the 30 most recent logs
powershell -NoProfile -Command ^
  "Get-ChildItem '%LOGDIR%\refresh_*.log' | Sort-Object LastWriteTime -Descending | Select-Object -Skip 30 | Remove-Item -Force -ErrorAction SilentlyContinue"

exit /b %RC%
