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

REM ---------------------------------------------------------------------------
REM Off-machine backup of the database.
REM
REM db\*.db is gitignored (30MB, and regenerable in principle), so the database
REM has never been on GitHub. But "regenerable" is not true of what actually
REM matters in it: ~130 boat merges and their aliases, the researched sailmaker
REM findings, the JOG data supplied by hand that no scraper can reach, the
REM charter flags, and RORC 2007-2022 whose source has since moved behind a
REM crawler block. The .bak files sit on the same disk, so they protect against
REM a bad script, not a dead drive.
REM
REM Copied to OneDrive, which syncs off the machine. Keeps the last 8 weekly
REM copies.
REM ---------------------------------------------------------------------------
set BACKUPDIR=C:\Users\ruari\OneDrive - North Technology Group\Marketshare backups
if not exist "%BACKUPDIR%" mkdir "%BACKUPDIR%"
copy /Y "%REPO%\db\marketshare.db" "%BACKUPDIR%\marketshare_%STAMP%.db" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo !! DATABASE BACKUP FAILED >> "%LOG%"
) else (
  echo database backed up to "%BACKUPDIR%\marketshare_%STAMP%.db" >> "%LOG%"
)

powershell -NoProfile -Command ^
  "Get-ChildItem '%BACKUPDIR%\marketshare_*.db' | Sort-Object LastWriteTime -Descending | Select-Object -Skip 8 | Remove-Item -Force -ErrorAction SilentlyContinue"

REM keep the 30 most recent logs
powershell -NoProfile -Command ^
  "Get-ChildItem '%LOGDIR%\refresh_*.log' | Sort-Object LastWriteTime -Descending | Select-Object -Skip 30 | Remove-Item -Force -ErrorAction SilentlyContinue"

exit /b %RC%
