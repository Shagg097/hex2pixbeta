"""Tests for installed-font discovery and the Text tool rasterizer."""

import os
import unittest

from hex2pix import textfont


class FontCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.faces = textfont.system_faces()
        cls.names = textfont.family_names()

    def test_the_builtin_choice_is_not_a_file_font(self):
        self.assertEqual(textfont.BUILTIN_FONT, "Pixel 5x7")
        self.assertNotIn(textfont.BUILTIN_FONT, self.names)

    def test_custom_labels_use_the_family_or_file_name(self):
        self.assertEqual(textfont.custom_label(r"C:\fonts\MyFont.ttf", "My Font"),
                         "Custom: My Font")
        self.assertEqual(textfont.custom_label("MyFont.ttf"), "Custom: MyFont")

    def test_the_system_catalog_has_usable_families(self):
        if not self.faces:
            self.skipTest("no system fonts available")
        self.assertTrue(self.names)
        self.assertTrue(all(not name.startswith("@") for name in self.names))

    def test_face_matching_honours_bold_and_italic(self):
        regular = textfont.face_for("Arial")
        if regular is None:
            self.skipTest("Arial is not installed")
        bold = textfont.face_for("Arial", bold=True)
        italic = textfont.face_for("Arial", italic=True)
        self.assertTrue(bold is not None and italic is not None)
        self.assertTrue(textfont._style_flags(bold.style)[0])
        self.assertTrue(textfont._style_flags(italic.style)[1])

    def test_tk_weight_aliases_resolve_to_a_face(self):
        black = textfont.face_for("Arial Black")
        if black is None:
            self.skipTest("Arial Black is not installed")
        self.assertTrue(textfont._style_flags(black.style)[0])
        self.assertIn("black", black.style.casefold())

    def test_render_text_makes_cropped_pixel_points(self):
        face = next((item for item in self.faces if item.kind == "truetype"),
                    None)
        if face is None:
            self.skipTest("no TrueType system font available")
        rendered = textfont.render_text("AB", face.family, 20,
                                        file_path=face.path)
        self.assertIsNotNone(rendered)
        self.assertGreater(rendered.width, 0)
        self.assertGreater(rendered.height, 0)
        self.assertTrue(rendered.points)
        self.assertTrue(all(0 <= x < rendered.width
                            and 0 <= y < rendered.height
                            for x, y in rendered.points))

    def test_variable_font_named_instances_render_differently(self):
        if textfont.face_for("Bahnschrift Light") is None:
            self.skipTest("Bahnschrift is not installed")
        regular = textfont.render_text("H", "Bahnschrift", 32)
        light = textfont.render_text("H", "Bahnschrift Light", 32)
        self.assertIsNotNone(regular)
        self.assertIsNotNone(light)
        self.assertNotEqual(regular.points, light.points)

    def test_legacy_windows_aliases_use_the_gdi_fallback(self):
        if os.name != "nt" or textfont.face_for("Modern") is not None:
            self.skipTest("Windows legacy GDI aliases are not available")
        rendered = textfont.render_text("A", "Modern", 20)
        self.assertIsNotNone(rendered)
        self.assertTrue(rendered.points)

    def test_fixed_size_windows_fonts_render_too(self):
        face = next((item for item in self.faces
                     if item.path.lower().endswith(".fon")), None)
        if face is None:
            self.skipTest("no legacy Windows font available")
        rendered = textfont.render_text("AB", face.family, 20,
                                        file_path=face.path)
        self.assertIsNotNone(rendered)
        self.assertTrue(rendered.points)

    def test_symbol_fonts_use_their_own_glyphs(self):
        if textfont.face_for("Wingdings") is None:
            self.skipTest("Wingdings is not installed")
        first = textfont.render_text("A", "Wingdings", 20)
        second = textfont.render_text("B", "Wingdings", 20)
        self.assertTrue(first.points)
        self.assertTrue(second.points)
        self.assertNotEqual((first.width, first.height, first.points),
                            (second.width, second.height, second.points))

    def test_notdef_rectangle_detection(self):
        from PIL import Image, ImageDraw
        solid = Image.new("L", (10, 12), 0)
        ImageDraw.Draw(solid).rectangle([1, 1, 8, 10], fill=255)
        self.assertTrue(textfont._mask_is_notdef(solid))
        hollow = Image.new("L", (10, 12), 0)
        ImageDraw.Draw(hollow).rectangle([1, 1, 8, 10], outline=255)
        self.assertTrue(textfont._mask_is_notdef(hollow))
        letter = Image.new("L", (10, 12), 0)
        ImageDraw.Draw(letter).line([(2, 10), (5, 1), (8, 10)], fill=255,
                                    width=2)
        self.assertFalse(textfont._mask_is_notdef(letter))

    def test_empty_text_has_no_ink(self):
        face = next((item for item in self.faces if item.kind == "truetype"),
                    None)
        if face is None:
            self.skipTest("no TrueType system font available")
        rendered = textfont.render_text("", face.family, 20,
                                        file_path=face.path)
        self.assertEqual((rendered.points, rendered.width, rendered.height),
                         ((), 0, 0))


if __name__ == "__main__":
    unittest.main()
