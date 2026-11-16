@echo off
rem Double-click to start Learn My Voice on Windows.
rem UTF-8 for this window and for Python, so Chinese text is shown and printed as written.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo Python 3.9 or newer is needed and was not found.
  echo Install "Python" from the Microsoft Store, or from python.org, then open this file again.
  echo 需要 Python 3.9 或更新的版本，但没有找到。请在 Microsoft Store 里安装 Python，然后再双击一次本文件。
  pause
  exit /b 1
)

%PY% -m studio.server %*
if errorlevel 1 pause
