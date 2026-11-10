# -*- mode: python ; coding: utf-8 -*-

# Portable retro build, frozen with cx_Freeze instead of PyInstaller.
#
# PyInstaller 6's bootloader declares a Windows 6.0 subsystem, so its output
# cannot start on Windows XP no matter which Python is bundled. cx_Freeze's
# launcher from the CPython 3.8.20 WinXP project declares 5.1, which XP accepts.
# The result is a folder (cx_Freeze has no one-file mode); ship it as a ZIP.
#
# Run from the repository root:
#     .py38xp\python.exe packaging\cxfreeze_setup.py build_exe --build-exe dist\retro
import sys
from pathlib import Path

from cx_Freeze import Executable, setup

ROOT = Path(__file__).resolve().parent.parent
# cx_Freeze starts its module search at the setup script's directory; the
# hex2pix package lives one level up.
sys.path.insert(0, str(ROOT))
ASSETS = ROOT / "hex2pix" / "assets"

setup(
    name="hex2pix",
    version="1.3.0b3",
    options={
        "build_exe": {
            # The application locates its manual and pixel-art tiles relative
            # to hex2pix/__file__, so the package must stay outside library.zip
            # with its data beside it.
            "include_files": [
                (str(ASSETS), "lib/hex2pix/assets"),
                (str(ROOT / "hex2pix" / "README.md"), "lib/hex2pix/README.md"),
            ],
            "excludes": ["numpy", "PyQt5"],
            "include_msvcr": True,
            "zip_exclude_packages": ["*"],
        }
    },
    executables=[
        Executable(
            str(ROOT / "run_hex2pix.py"),
            base="gui",
            target_name="hex2pix",
            icon=str(ASSETS / "logoicon.ico"),
        )
    ],
)
