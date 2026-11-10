"""Tests for hex2pix's file formats, including the hand-written BMP writer."""

import os
import struct
import tempfile
import unittest

from PIL import Image

from hex2pix import bitdepth, formats
from hex2pix.model import Bitmap, HexError, parse_block


def sample():
    bitmap = Bitmap(4, 3)
    bitmap.set_rgba(0, 0, (255, 0, 0, 255))
    bitmap.set_rgba(1, 0, (0, 255, 0, 255))
    bitmap.set_rgba(2, 0, (0, 0, 255, 255))
    bitmap.set_rgba(3, 0, (255, 255, 255, 255))
    bitmap.set_rgba(0, 1, (0, 0, 0, 255))
    bitmap.set_rgba(1, 1, (10, 20, 30, 128))
    bitmap.set_rgba(2, 1, (40, 50, 60, 0))
    return bitmap


class ExportOptionTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def path(self, name):
        return os.path.join(self.dir, name)

    def test_trim_crops_transparent_edges(self):
        bitmap = Bitmap(5, 4)
        bitmap.set_rgba(1, 1, (255, 0, 0, 255))
        bitmap.set_rgba(2, 2, (255, 0, 0, 255))
        cropped = formats.trim_alpha(bitmap)
        self.assertEqual((cropped.width, cropped.height), (2, 2))
        self.assertEqual(cropped.rgba(0, 0), (255, 0, 0, 255))

    def test_trim_keeps_an_empty_canvas(self):
        cropped = formats.trim_alpha(Bitmap(3, 3))
        self.assertEqual((cropped.width, cropped.height), (3, 3))

    def test_scale_nearest_doubles_pixels(self):
        bitmap = Bitmap(2, 1)
        bitmap.set_rgba(0, 0, (255, 0, 0, 255))
        bitmap.set_rgba(1, 0, (0, 0, 255, 255))
        big = formats.scale_bitmap(bitmap, 2)
        self.assertEqual((big.width, big.height), (4, 2))
        self.assertEqual(big.rgba(0, 0), (255, 0, 0, 255))
        self.assertEqual(big.rgba(3, 1), (0, 0, 255, 255))

    def test_scale_guards_absurd_dimensions(self):
        with self.assertRaises(HexError):
            formats.scale_bitmap(sample(), 5000)

    def test_prepare_export_flattens_when_alpha_is_dropped(self):
        prepared = formats.prepare_export(sample(), keep_alpha=False,
                                          matte=(255, 255, 255))
        self.assertEqual(prepared.rgba(1, 1), (132, 137, 142, 255))
        self.assertEqual(prepared.rgba(2, 1), (255, 255, 255, 255))

    def test_prepare_export_trims_then_scales(self):
        bitmap = Bitmap(4, 4)
        bitmap.set_rgba(1, 1, (255, 0, 0, 255))
        prepared = formats.prepare_export(bitmap, trim=True, scale=2)
        self.assertEqual((prepared.width, prepared.height), (2, 2))

    def test_png_without_alpha_writes_rgb(self):
        target = self.path("flat.png")
        formats.save(sample(), target, "png", keep_alpha=False)
        with Image.open(target) as image:
            self.assertEqual(image.mode, "RGB")

    def test_gif_palette_size_is_capped(self):
        target = self.path("small.gif")
        formats.save(sample(), target, "gif", gif_colors=2)
        with Image.open(target) as image:
            self.assertLessEqual(len(image.getcolors(256) or []), 2)

    def test_png_compression_accepts_zero(self):
        target = self.path("store.png")
        self.assertGreater(formats.save(sample(), target, "png",
                                       png_compress=0), 0)

    def test_save_dialog_offers_projects_and_every_format(self):
        patterns = [pattern for _label, pattern in formats.SAVE_FILETYPES]
        self.assertIn("*.h2p", patterns)
        for item in formats.FORMATS:
            self.assertIn("*" + item["ext"], patterns)


class FormatTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def path(self, name):
        return os.path.join(self.dir, name)

    def test_all_formats_write(self):
        bitmap = sample()
        for item in formats.FORMATS:
            target = self.path("out" + item["ext"])
            size = formats.save(bitmap, target, item["key"])
            self.assertGreater(size, 0, item["key"])

    def test_png_roundtrip_exact(self):
        bitmap = sample()
        forms = self.path("a.png")
        formats.save(bitmap, forms, "png")
        back = formats.load(forms)
        self.assertEqual((back.width, back.height), (4, 3))
        self.assertEqual(back.rgba(1, 1), (10, 20, 30, 128))

    def test_bmp24_header_and_pixels(self):
        bitmap = sample()
        target = self.path("a24.bmp")
        formats.save(bitmap, target, "bmp24", matte=(255, 255, 255))
        with open(target, "rb") as handle:
            header = handle.read(54)
        self.assertEqual(header[:2], b"BM")
        self.assertEqual(struct.unpack_from("<H", header, 28)[0], 24)
        self.assertEqual(struct.unpack_from("<I", header, 2)[0], os.path.getsize(target))
        back = formats.load(target)
        self.assertEqual(back.rgba(0, 0), (255, 0, 0, 255))
        # 128-alpha over white: (10*128 + 255*127)//255 = 132
        self.assertEqual(back.rgba(1, 1), (132, 137, 142, 255))
        self.assertEqual(back.rgba(2, 1), (255, 255, 255, 255))

    def test_bmp32_keeps_alpha(self):
        bitmap = sample()
        target = self.path("a32.bmp")
        formats.save(bitmap, target, "bmp32")
        with open(target, "rb") as handle:
            header = handle.read(54)
        self.assertEqual(struct.unpack_from("<H", header, 28)[0], 32)
        back = formats.load(target)
        self.assertEqual(back.rgba(1, 1), (10, 20, 30, 128))
        self.assertEqual(back.rgba(2, 1), (40, 50, 60, 0))

    def test_ppm_roundtrip(self):
        bitmap = sample()
        target = self.path("a.ppm")
        formats.save(bitmap, target, "ppm", matte=(0, 0, 0))
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(2), b"P6")
        back = formats.load(target)
        self.assertEqual(back.rgba(0, 0), (255, 0, 0, 255))

    def test_gif_and_jpeg_are_readable(self):
        bitmap = sample()
        gif = self.path("a.gif")
        formats.save(bitmap, gif, "gif")
        back = formats.load(gif)
        self.assertEqual(back.rgba(0, 0)[:3], (255, 0, 0))

        jpg = self.path("a.jpg")
        formats.save(bitmap, jpg, "jpg", quality=95)
        back = formats.load(jpg)
        self.assertEqual(back.width, 4)

    def test_ico_and_tga_and_tiff(self):
        bitmap = sample()
        for key, ext in (("tga", ".tga"), ("tif", ".tif"), ("webp", ".webp")):
            target = self.path("a" + ext)
            formats.save(bitmap, target, key)
            back = formats.load(target)
            self.assertEqual(back.rgba(0, 0), (255, 0, 0, 255), key)

        # ICO entries are square, so the artwork is padded, not resampled
        target = self.path("a.ico")
        formats.save(bitmap, target, "ico")
        back = formats.load(target)
        self.assertEqual(back.rgba(0, 0), (255, 0, 0, 255))
        self.assertEqual((back.width, back.height), (4, 4))
        self.assertEqual(back.rgba(1, 1), (10, 20, 30, 128))

    def test_hex_text_roundtrip(self):
        bitmap = sample()
        target = self.path("a.txt")
        formats.save(bitmap, target, "hex")
        with open(target, "r", encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("FF0000", text)
        self.assertIn(".", text)
        back = formats.load(target)
        self.assertEqual(back.rgba(0, 0), (255, 0, 0, 255))
        self.assertEqual(back.rgba(2, 1), (0, 0, 0, 0))

    def test_hex_block_image(self):
        bitmap = formats.bitmap_from_block(parse_block("FF0000 00FF00\n0000FF ."))
        self.assertEqual((bitmap.width, bitmap.height), (2, 2))
        self.assertEqual(bitmap.rgba(1, 0), (0, 255, 0, 255))
        self.assertEqual(bitmap.rgba(1, 1), (0, 0, 0, 0))

    def test_bad_bmp(self):
        target = self.path("bad.bmp")
        with open(target, "wb") as handle:
            handle.write(b"BM" + b"\x00" * 100)
        with self.assertRaises(HexError):
            formats.read_bmp(target)

    def test_bmp_writes_resolution(self):
        target = self.path("ppi.bmp")
        formats.save(sample(), target, "bmp24", ppi=300)
        with open(target, "rb") as handle:
            header = handle.read(54)
        ppm = struct.unpack_from("<i", header, 38)[0]
        self.assertEqual(ppm, int(round(300 / 0.0254)))

    def test_png_keeps_resolution(self):
        target = self.path("dpi.png")
        formats.save(sample(), target, "png", ppi=144)
        with Image.open(target) as image:
            self.assertAlmostEqual(image.info["dpi"][0], 144, places=0)

    def test_load_document_reports_ppi(self):
        target = self.path("meta.bmp")
        formats.save(sample(), target, "bmp24", ppi=96)
        bitmap, ppi = formats.load_document(target)
        self.assertEqual((bitmap.width, bitmap.height), (4, 3))
        self.assertAlmostEqual(ppi, 96, places=1)

    def test_format_for_path(self):
        self.assertEqual(formats.format_for_path("x.PNG"), "png")
        self.assertEqual(formats.format_for_path("x.bmp"), "bmp24")
        self.assertEqual(formats.format_for_path("x.unknown"), "png")

    def test_pillow_can_open_our_bmp(self):
        bitmap = sample()
        for key in ("bmp24", "bmp32"):
            target = self.path(key + ".bmp")
            formats.save(bitmap, target, key)
            with Image.open(target) as image:
                image.load()
                self.assertEqual(image.size, (4, 3))


class RawBitDepthTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def path(self, name):
        return os.path.join(self.dir, name)

    @staticmethod
    def bitmap(*colors):
        bitmap = Bitmap(len(colors), 1)
        for x, color in enumerate(colors):
            bitmap.set_rgba(x, 0, color)
        return bitmap

    def save_bytes(self, bitmap, **options):
        target = self.path("out.raw")
        size = formats.save(bitmap, target, "raw", **options)
        with open(target, "rb") as handle:
            data = handle.read()
        self.assertEqual(size, len(data))
        return data

    def test_packed_streams_round_trip(self):
        for bits in (1, 2, 3, 5, 6, 7, 9, 12, 17, 32):
            values = [(index * 7 + 1) % (1 << bits) for index in range(11)]
            data = bitdepth.pack_samples(values, bits, "packed")
            self.assertEqual(len(data), (len(values) * bits + 7) // 8, bits)
            self.assertEqual(
                bitdepth.unpack_samples(data, bits, len(values), "packed"),
                values, bits)

    def test_aligned_stream_pads_the_high_bits(self):
        data = bitdepth.pack_samples([0x135, 0xACE], 12, "aligned")
        self.assertEqual(data, b"\x01\x35\x0a\xce")
        self.assertEqual(
            bitdepth.unpack_samples(data, 12, 2, "aligned"), [0x135, 0xACE])

    def test_packed_tail_is_zero_padded(self):
        self.assertEqual(bitdepth.pack_samples([1], 3, "packed"), b"\x20")

    def test_12_bpp_packed_layout(self):
        bitmap = self.bitmap((0x12, 0x34, 0x56, 255),
                             (0xAB, 0xCD, 0xEF, 255))
        data = self.save_bytes(bitmap, raw_mode="rgb",
                               raw_channels=(4, 4, 4, 0),
                               raw_packing="packed")
        self.assertEqual(data, b"\x13\x5a\xce")

    def test_12_bpp_aligned_layout(self):
        bitmap = self.bitmap((0x12, 0x34, 0x56, 255),
                             (0xAB, 0xCD, 0xEF, 255))
        data = self.save_bytes(bitmap, raw_mode="rgb",
                               raw_channels=(4, 4, 4, 0),
                               raw_packing="aligned")
        self.assertEqual(data, b"\x01\x35\x0a\xce")

    def test_6_bpp_packed_layout(self):
        bitmap = self.bitmap((0, 0, 0xC0, 255), (0x40, 0x40, 0x40, 255),
                             (0x80, 0x80, 0x80, 255),
                             (0xFF, 0xFF, 0xFF, 255))
        data = self.save_bytes(bitmap, raw_channels=(2, 2, 2, 0),
                               raw_packing="packed")
        self.assertEqual(data, b"\x0d\x5a\xbf")

    def test_6_bpp_aligned_layout(self):
        bitmap = self.bitmap((0, 0, 0xC0, 255), (0x40, 0x40, 0x40, 255),
                             (0x80, 0x80, 0x80, 255),
                             (0xFF, 0xFF, 0xFF, 255))
        data = self.save_bytes(bitmap, raw_channels=(2, 2, 2, 0),
                               raw_packing="aligned")
        self.assertEqual(data, b"\x03\x15\x2a\x3f")

    def test_32_bpp_rgba_keeps_every_byte(self):
        data = self.save_bytes(self.bitmap((0x12, 0x34, 0x56, 0x78)),
                               raw_channels=(8, 8, 8, 8),
                               raw_packing="packed")
        self.assertEqual(data, b"\x12\x34\x56\x78")

    def test_10_bit_channels_shift_up(self):
        data = self.save_bytes(self.bitmap((0x12, 0x34, 0x56, 255)),
                               raw_channels=(10, 10, 10, 0),
                               raw_packing="packed")
        expected = ((0x12 << 2) << 20) | ((0x34 << 2) << 10) | (0x56 << 2)
        self.assertEqual(int.from_bytes(data, "big"), expected << 2)

    def test_indexed_stream_uses_one_entry_per_bit(self):
        indices, palette = bitdepth.quantize_rgb(
            bytes([255, 255, 255] * 4 + [0, 0, 0] * 4), 8, 1, 1)
        self.assertEqual(len(indices), 8)
        self.assertEqual(set(indices), {0, 1})
        self.assertEqual(sorted(palette), [(0, 0, 0), (255, 255, 255)])

    def test_indexed_save_packs_bits(self):
        bitmap = self.bitmap(*([(255, 255, 255, 255)] * 4
                               + [(0, 0, 0, 255)] * 4))
        data = self.save_bytes(bitmap, raw_mode="indexed", raw_depth=1,
                               raw_packing="packed")
        self.assertEqual(len(data), 1)
        self.assertIn(data[0], (0x0F, 0xF0))

    def test_raw_rejects_bad_channel_totals(self):
        for channels in ((0, 0, 0, 0), (16, 16, 16, 16), (8, 8, 8, 9)):
            with self.assertRaises(HexError):
                formats.save(self.bitmap((0, 0, 0, 255)),
                             self.path("bad.raw"), "raw",
                             raw_channels=channels)

    def test_indexed_depth_is_capped_at_eight(self):
        with self.assertRaises(HexError):
            formats.save(self.bitmap((0, 0, 0, 255)),
                         self.path("bad.raw"), "raw",
                         raw_mode="indexed", raw_depth=9)

    def test_raw_is_offered_and_detected(self):
        patterns = [pattern for _label, pattern in formats.SAVE_FILETYPES]
        self.assertIn("*.raw", patterns)
        self.assertEqual(formats.format_for_path("x.RAW"), "raw")


if __name__ == "__main__":
    unittest.main()
