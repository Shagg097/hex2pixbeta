"""Entry point for hex2pix (also the PyInstaller script)."""

import sys

from hex2pix.app import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
