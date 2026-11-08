# -*- mode: python ; coding: utf-8 -*-

# Portable build: a folder with the executable and its runtime beside it.
#
# The single-file hex2pix.exe unpacks python310.dll and friends into
# %TEMP%\_MEIxxxxxx on every start. Antivirus tools that quarantine or block
# that freshly written DLL make the bootloader fail with
#     Failed to load Python DLL ...\_MEIxxxxxx\python310.dll
# This build keeps those DLLs on disk next to the executable, so nothing has
# to be extracted and loaded from %TEMP% at all. Ship it as a ZIP.

a = Analysis(
    ['run_hex2pix.py'],
    pathex=['.'],
    binaries=[],
    datas=[('hex2pix\\README.md', 'hex2pix'), ('hex2pix\\assets', 'hex2pix\\assets')],
    hiddenimports=['PIL._tkinter_finder'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'PyQt5'],
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
    icon='hex2pix\\assets\\logoicon.ico',
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
