"""Tool palette artwork.

The BMPs in ``assets/`` are 16x16 pixel-art tiles, one per tool, named after
the tool they stand for. This module maps tool ids to those files and finds the
folder for both a source checkout and the frozen executable.
"""

import os

ICON_FILES = {
    "pencil": "pencilbtn.bmp",
    "eraser": "eraserbtn.bmp",
    "spray": "Spraycanbtn.bmp",
    "picker": "pickerbtn.bmp",
    "fill": "fillbtn.bmp",
    "line": "linebtn.bmp",
    "curve": "curvebtn.bmp",
    "shape_rect": "rectanglebtn.bmp",
    "shape_ellipse": "ellipsebtn.bmp",
    "gradient": "gradientbtn.bmp",
    "pattern": "linebylinebtn.bmp",
    "structure": "paternbtn.bmp",
    "contour": "contourbtn.bmp",
    "blur": "blurbtn.bmp",
    "shuffle": "shufflebtn.bmp",
    "text": "textbtn.bmp",
    "rect": "rectselectbtn.bmp",
    "ellipse": "ellipseselectbtn.bmp",
    "polygon": "polygonbtn.bmp",
    "lasso": "lassobtn.bmp",
    "wand": "wandbtn.bmp",
    "similar": "simularbtn.bmp",
    "transform": "transfrombtn.bmp",
    "hand": "handbtn.bmp",
}

ICON_SIZE = 16
#: The H2P logo, used for the window, taskbar and executable icon.
LOGO_FILE = "logoicon.bmp"
#: Pixel size of the buttons in the tool palette; 2x keeps the art crisp.
BUTTON_ICON_SIZE = 32
BUTTON_SIZE = 36


def asset_dir():
    """The folder holding the tool tiles, source checkout or frozen build."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def icon_path(tool, folder=None):
    """Path to the tile for `tool`, or None when it has no artwork."""
    name = ICON_FILES.get(tool)
    if not name:
        return None
    path = os.path.join(folder or asset_dir(), name)
    return path if os.path.isfile(path) else None


def logo_path(folder=None):
    """Path to the H2P logo bitmap, or None when it is missing."""
    path = os.path.join(folder or asset_dir(), LOGO_FILE)
    return path if os.path.isfile(path) else None
