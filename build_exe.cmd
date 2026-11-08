@echo off
setlocal
cd /d "%~dp0"
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
    python --version >nul 2>nul && set "PY=python"
)
if not defined PY (
    python3 --version >nul 2>nul && set "PY=python3"
)
if not defined PY (
    if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe -3"
)
if not defined PY (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
        if not defined PY if exist "%%D\python.exe" set "PY=%%D\python.exe"
    )
)
if not defined PY (
    for /d %%D in ("C:\Python3*") do (
        if not defined PY if exist "%%D\python.exe" set "PY=%%D\python.exe"
    )
)
if not defined PY (
    echo Python 3 was not found. Install Python 3 with tkinter and Pillow, then run this again.
    exit /b 1
)
echo Building hex2pix.exe with %PY% ...
%PY% -m PyInstaller --noconfirm --clean hex2pix.spec
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)
copy /Y dist\hex2pix.exe hex2pix.exe >nul
if errorlevel 1 (
    echo Built dist\hex2pix.exe, but hex2pix.exe could not be replaced.
    echo Close the running hex2pix window and run this again.
    exit /b 1
)
echo Built hex2pix.exe
