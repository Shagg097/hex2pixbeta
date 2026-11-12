# -*- mode: python ; coding: utf-8 -*-

# Portable build: a folder with the executable and its runtime beside it.
#
# The single-file hex2pix.exe unpacks python38.dll and friends into
# %TEMP%\_MEIxxxxxx on every start. Antivirus tools that quarantine or block
# that freshly written DLL make the bootloader fail with
#     Failed to load Python DLL ...\_MEIxxxxxx\python38.dll
# This build keeps those DLLs on disk next to the executable, so nothing has
# to be extracted and loaded from %TEMP% at all. Ship it as a ZIP.
from pathlib import Path

ROOT = Path(SPECPATH).parent
ASSETS = ROOT / "hex2pix" / "assets"

a = Analysis(
    [str(ROOT / "run_hex2pix.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / "hex2pix" / "README.md"), "hex2pix"),
           (str(ASSETS), "hex2pix/assets")],
    hiddenimports=['PIL._tkinter_finder'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # WIP code lives in ../wip and must never be bundled into a release EXE.
    excludes=['numpy', 'PyQt5', 'wip', 'hex2pix.wip'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='hex2pix',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS / "logoicon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='hex2pix-portable',
)
