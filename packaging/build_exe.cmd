@echo off
setlocal
rem Build helper for the Windows releases: run it from anywhere, it always
rem works on the repository root and leaves build\ and dist\ there.
cd /d "%~dp0.."
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
    echo Python 3 was not found. Install Python 3 with tkinter, then run this again.
    exit /b 1
)
%PY% -c "import tkinter" >nul 2>nul
if errorlevel 1 (
    echo This Python has no tkinter. Reinstall Python 3 and keep the "tcl/tk and IDLE" option.
    exit /b 1
)
%PY% -c "import PIL, PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo Installing build requirements from requirements.txt ...
    %PY% -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 (
        echo Could not install the build requirements. Check the network and run this again.
        exit /b 1
    )
)
echo Building hex2pix.exe with %PY% ...
%PY% -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\hex2pix.spec
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
echo Building the portable folder (no extraction to TEMP) ...
%PY% -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\hex2pix_onedir.spec
if errorlevel 1 (
    echo Portable build failed.
    exit /b 1
)
if not exist "dist\hex2pix-portable\hex2pix.exe" (
    echo The portable build is missing dist\hex2pix-portable\hex2pix.exe.
    exit /b 1
)
pushd dist
%PY% -m zipfile -c hex2pix-portable.zip hex2pix-portable
if errorlevel 1 (
    popd
    echo Built the portable folder, but dist\hex2pix-portable.zip could not be created.
    exit /b 1
)
popd
echo Built hex2pix.exe and dist\hex2pix-portable.zip
