@echo off
setlocal
cd /d "%~dp0"
set "CODEX_PY=C:\Users\27732\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%CODEX_PY%" (
  start "" http://127.0.0.1:8000
  "%CODEX_PY%" server.py
  exit /b
)
where py >nul 2>nul
if %errorlevel%==0 (
  start "" http://127.0.0.1:8000
  py server.py
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    start "" http://127.0.0.1:8000
    python server.py
  ) else (
    echo.
    echo 未找到 Python。请先安装 Python 3.10 或更新版本，并在安装时勾选 Add Python to PATH。
    echo.
    pause
  )
)
