"""The tool palette artwork must exist and cover every tool."""

import os
import unittest

from PIL import Image

from hex2pix import icons


class IconMapTests(unittest.TestCase):
    def test_every_tool_has_an_icon(self):
        from hex2pix.app import TOOLS

        for name, label, _key, _tip in TOOLS:
            self.assertIn(name, icons.ICON_FILES, label)
            self.assertIsNotNone(icons.icon_path(name), label)

    def test_mapping_names_only_real_tools(self):
        from hex2pix.app import TOOLS

        known = {name for name, _label, _key, _tip in TOOLS}
        self.assertEqual(set(icons.ICON_FILES) - known, set())

    def test_icons_are_sixteen_pixel_tiles(self):
        for tool in icons.ICON_FILES:
            path = icons.icon_path(tool)
            with Image.open(path) as image:
                self.assertEqual(image.size, (icons.ICON_SIZE, icons.ICON_SIZE),
                                 tool)

    def test_pattern_and_structure_art_is_not_swapped(self):
        self.assertEqual(icons.ICON_FILES["pattern"], "linebylinebtn.bmp")
        self.assertEqual(icons.ICON_FILES["structure"], "paternbtn.bmp")

    def test_filenames_are_unique(self):
        names = list(icons.ICON_FILES.values())
        self.assertEqual(len(names), len(set(names)))

    def test_unknown_tool_and_missing_folder(self):
        self.assertIsNone(icons.icon_path("nope"))
        self.assertIsNone(icons.icon_path("pencil", folder=os.devnull))
        self.assertTrue(os.path.isdir(icons.asset_dir()))
