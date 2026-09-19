@echo off
REM Thin launcher for the native-Windows airlock installer.
REM Finds a Python interpreter, then hands every argument straight through.
REM Everything real lives in windows_install.py; nothing is decided here.
setlocal
set "HERE=%~dp0"
where py >nul 2>&1 && (
  py -3 "%HERE%windows_install.py" %*
  exit /b %ERRORLEVEL%
)
where python >nul 2>&1 && (
  python "%HERE%windows_install.py" %*
  exit /b %ERRORLEVEL%
)
echo No Python found. Install Python from python.org ^(not the Microsoft Store
echo stub^), or run: ^<full path to python.exe^> "%HERE%windows_install.py" %*
exit /b 2
