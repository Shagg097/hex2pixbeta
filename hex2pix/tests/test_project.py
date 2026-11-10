"""Tests for the .h2p project container."""

import json
import os
import shutil
import tempfile
import unittest
import zipfile

from hex2pix import formats
from hex2pix.model import Document, HexError


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="h2p-project-")
        self.path = os.path.join(self.folder, "art.h2p")

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def document(self):
        document = Document(3, 2, ppi=144.0, color_mode="Grayscale",
                            alpha=False)
        document.bitmap.set_rgba(0, 0, (255, 0, 0, 255))
        document.bitmap.set_rgba(2, 1, (0, 255, 0, 128))
        layer = document.add_layer("Ink", op="multiply", gate="nand",
                                   blend="screen")
        layer.alpha = 128
        layer.group = "Ink"
        document.ensure_group("Ink").alpha = 200
        layer.bitmap.set_rgba(1, 0, (0, 0, 255, 255))
        document.layers[0].visible = False
        document.selection.restore(b"\x00\xff\x00\x00\x00\xff")
        return document

    def test_round_trip_keeps_every_layer(self):
        document = self.document()
        size = formats.save_project(self.path, document,
                                    {"foo": "bar",
                                     "palette": [[1, 2, 3, 255]]})
        self.assertGreater(size, 0)
        project = formats.load_project(self.path)
        loaded = project.document
        self.assertEqual(loaded.width, 3)
        self.assertEqual(loaded.height, 2)
        self.assertEqual(loaded.ppi, 144.0)
        self.assertEqual(loaded.color_mode, "Grayscale")
        self.assertFalse(loaded.alpha)
        self.assertEqual(loaded.layer_count, 2)
        self.assertEqual(loaded.layers[0].name, "Layer 1")
        self.assertEqual(loaded.layers[1].name, "Ink")
        self.assertEqual(loaded.layers[1].op, "multiply")
        self.assertEqual(loaded.layers[1].gate, "nand")
        self.assertEqual(loaded.layers[1].blend, "screen")
        self.assertEqual(loaded.layers[1].alpha, 128)
        self.assertEqual(loaded.layers[1].group, "Ink")
        self.assertEqual(loaded.layers[0].group, "")
        group = loaded.group("Ink")
        self.assertIsNotNone(group)
        self.assertEqual(group.alpha, 200)
        self.assertTrue(group.visible)
        self.assertFalse(loaded.layers[0].visible)
        self.assertEqual(loaded.layers[0].bitmap.rgba(0, 0), (255, 0, 0, 255))
        self.assertEqual(loaded.layers[1].bitmap.rgba(1, 0),
                         (0, 0, 255, 255))
        self.assertEqual(project.extras["foo"], "bar")
        self.assertEqual(project.extras["palette"], [[1, 2, 3, 255]])
        self.assertEqual(loaded.path, self.path)

    def test_layer_data_is_separate_entries(self):
        formats.save_project(self.path, self.document())
        with zipfile.ZipFile(self.path) as archive:
            names = set(archive.namelist())
            header = json.loads(archive.read("project.json").decode("utf-8"))
        self.assertIn("layers/000.rgba", names)
        self.assertIn("layers/001.rgba", names)
        self.assertEqual(header["format"], formats.PROJECT_FORMAT)
        self.assertEqual(header["version"], formats.PROJECT_VERSION)
        self.assertEqual(len(header["layers"]), 2)
        self.assertEqual(header["groups"][0]["name"], "Ink")
        self.assertEqual(header["layers"][1]["group"], "Ink")

    def test_selection_is_saved(self):
        formats.save_project(self.path, self.document())
        loaded = formats.load_project(self.path).document
        self.assertTrue(loaded.selection.active)
        self.assertEqual(list(loaded.selection.mask), [0, 255, 0, 0, 0, 255])

    def test_active_layer_is_saved(self):
        formats.save_project(self.path, self.document())
        loaded = formats.load_project(self.path).document
        self.assertEqual(loaded.layer_index, 1)

    def test_empty_selection_stays_empty(self):
        document = self.document()
        document.selection.clear()
        formats.save_project(self.path, document)
        loaded = formats.load_project(self.path).document
        self.assertFalse(loaded.selection.active)

    def test_extension_detection(self):
        self.assertTrue(formats.is_project_path("C:/art/thing.H2P"))
        self.assertFalse(formats.is_project_path("thing.png"))

    def test_bad_archive_is_reported(self):
        with open(self.path, "wb") as handle:
            handle.write(b"not a zip")
        with self.assertRaises(HexError):
            formats.load_project(self.path)

    def test_foreign_zip_is_reported(self):
        with zipfile.ZipFile(self.path, "w") as archive:
            archive.writestr("readme.txt", "hello")
        with self.assertRaises(HexError):
            formats.load_project(self.path)

    def test_truncated_layer_is_reported(self):
        formats.save_project(self.path, self.document())
        with zipfile.ZipFile(self.path) as archive:
            entries = {name: archive.read(name)
                       for name in archive.namelist()}
        entries["layers/001.rgba"] = b"\x00\x00"
        with zipfile.ZipFile(self.path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        with self.assertRaises(HexError):
            formats.load_project(self.path)


if __name__ == "__main__":
    unittest.main()
