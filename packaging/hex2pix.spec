# -*- mode: python ; coding: utf-8 -*-

# Single-file build. Run it through packaging\build_exe.cmd, which also
# produces the portable folder build and the release ZIP.
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
    a.binaries,
    a.datas,
    [],
    icon=str(ASSETS / "logoicon.ico"),
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
)
