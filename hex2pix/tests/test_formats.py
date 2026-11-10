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

    def test_named_bit_layouts_spell_out_the_bytes(self):
        padded = bitdepth.layout_label((4, 4, 4, 0), "aligned")
        packed = bitdepth.layout_label((4, 4, 4, 0), "packed")
        self.assertIn("xxxxrrrr ggggbbbb", padded)
        self.assertIn("rrrrgggg bbbbrrrr ggggbbbb", packed)
        self.assertEqual(bitdepth.layout_for(padded),
                         ((4, 4, 4, 0), "aligned"))
        self.assertEqual(bitdepth.layout_for(packed),
                         ((4, 4, 4, 0), "packed"))
        self.assertEqual(bitdepth.layout_label((5, 5, 5, 5), "aligned"),
                         "Custom")

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

    def test_bgr_order_reverses_the_packed_sample(self):
        bitmap = self.bitmap((0x12, 0x34, 0x56, 255),
                             (0xAB, 0xCD, 0xEF, 255))
        data = self.save_bytes(bitmap, raw_channels=(4, 4, 4, 0),
                               raw_packing="packed", raw_order="BGR")
        self.assertEqual(data, b"\x53\x1e\xca")

    def test_bgr_order_aligned_puts_blue_up_front(self):
        bitmap = self.bitmap((0x12, 0x34, 0x56, 255),
                             (0xAB, 0xCD, 0xEF, 255))
        data = self.save_bytes(bitmap, raw_channels=(4, 4, 4, 0),
                               raw_packing="aligned", raw_order="BGR")
        self.assertEqual(data, b"\x05\x31\x0e\xca")

    def test_6_bpp_bgr_order_layout(self):
        bitmap = self.bitmap((0, 0, 0xC0, 255), (0x40, 0x40, 0x40, 255),
                             (0x80, 0x80, 0x80, 255),
                             (0xFF, 0xFF, 0xFF, 255))
        data = self.save_bytes(bitmap, raw_channels=(2, 2, 2, 0),
                               raw_packing="packed", raw_order="BGR")
        self.assertEqual(data, b"\xc1\x5a\xbf")

    def test_alpha_follows_the_chosen_order(self):
        rgba = bytes((0xC0, 0x80, 0x40, 0xFF))
        self.assertEqual(
            bitdepth.sample_rgba(rgba, (2, 2, 2, 2), "BGR"),
            [0b01101111])

    def test_channel_order_is_validated(self):
        self.assertEqual(bitdepth.check_order("bgr"), "BGR")
        self.assertEqual(bitdepth.check_order("rgba"), "RGBA")
        self.assertEqual(bitdepth.order_label("RB"), "RGB")
        for name in ("RB", "RRG", "RGBAX"):
            with self.assertRaises(bitdepth.HexError):
                bitdepth.check_order(name)

    def test_quantize_rgba_keeps_the_top_bits(self):
        rgba = bytes((0x12, 0x34, 0x56, 0xFF))
        self.assertEqual(bitdepth.quantize_rgba(rgba, (4, 4, 4, 0)),
                         b"\x10\x30\x50\xff")
        self.assertEqual(bitdepth.quantize_rgba(rgba, (2, 2, 2, 0)),
                         b"\x00\x00\x40\xff")

    def test_limit_rgba_preserves_alpha_without_alpha_bits(self):
        rgba = bytes((0x12, 0x34, 0x56, 0x78))
        self.assertEqual(bitdepth.limit_rgba(rgba, (4, 4, 4, 0)),
                         b"\x10\x30\x50\x78")
        self.assertEqual(bitdepth.limit_rgba(rgba, (4, 4, 4, 4)),
                         b"\x10\x30\x50\x70")
        self.assertEqual(bitdepth.limit_rgba(rgba, (0, 4, 4, 0)),
                         b"\x00\x30\x50\x78")

    def test_limit_color_matches_the_byte_limit(self):
        color = (0x12, 0x34, 0x56, 0x78)
        self.assertEqual(bitdepth.limit_color(color, (4, 4, 4, 0)),
                         (0x10, 0x30, 0x50, 0x78))
        self.assertEqual(bitdepth.limit_color(color, (8, 8, 8, 8)), color)

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

    def test_bmp_header_wraps_custom_bits(self):
        bitmap = self.bitmap((0x12, 0x34, 0x56, 255),
                             (0xAB, 0xCD, 0xEF, 255))
        target = self.path("out.bmp")
        size = formats.save(bitmap, target, "raw", raw_mode="rgb",
                            raw_channels=(4, 4, 4, 0),
                            raw_packing="packed", raw_header="bmp")
        with open(target, "rb") as handle:
            data = handle.read()
        self.assertEqual(size, len(data))
        self.assertEqual(data[:2], b"BM")
        self.assertEqual(struct.unpack_from("<I", data, 14)[0], 40)
        # 4:4:4 has no BMP layout of its own, so it is stored as 5:6:5.
        self.assertEqual(struct.unpack_from("<H", data, 28)[0], 16)
        self.assertEqual(struct.unpack_from("<I", data, 30)[0], 3)
        self.assertEqual(struct.unpack_from("<III", data, 54),
                         (0xF800, 0x07E0, 0x001F))
        self.assertEqual(struct.unpack_from("<ii", data, 18), (2, 1))
        offset = struct.unpack_from("<I", data, 10)[0]
        self.assertEqual(offset, 66)
        self.assertEqual(data[offset:], b"\xaa\x11\x7d\xae")

    def test_bmp_16_bit_masks_follow_the_order(self):
        bitmap = self.bitmap((0x12, 0x34, 0x56, 255))
        target = self.path("bgr.bmp")
        formats.save(bitmap, target, "raw", raw_mode="rgb",
                     raw_channels=(5, 6, 5, 0), raw_packing="packed",
                     raw_header="bmp", raw_order="BGR")
        with open(target, "rb") as handle:
            data = handle.read()
        self.assertEqual(struct.unpack_from("<III", data, 54),
                         (0x001F, 0x07E0, 0xF800))
        offset = struct.unpack_from("<I", data, 10)[0]
        self.assertEqual(data[offset:offset + 2], b"\xa2\x51")

    def test_bmp_24_bit_and_tga_ignore_the_order(self):
        bitmap = self.bitmap((0x12, 0x34, 0x56, 255))
        for header, suffix in (("bmp", ".bmp"), ("tga", ".tga")):
            payloads = []
            for order in ("RGB", "BGR"):
                target = self.path("%s-%s%s" % (header, order, suffix))
                formats.save(bitmap, target, "raw", raw_mode="rgb",
                             raw_channels=(8, 8, 8, 0),
                             raw_packing="packed", raw_header=header,
                             raw_order=order)
                with open(target, "rb") as handle:
                    data = handle.read()
                if header == "bmp":
                    offset = struct.unpack_from("<I", data, 10)[0]
                    payloads.append(data[offset:])
                else:
                    payloads.append(data[18:])
            self.assertEqual(payloads[0], payloads[1], header)
            self.assertEqual(payloads[0][:3], b"\x56\x34\x12", header)

    def test_bmp_v4_and_v5_headers_are_selectable(self):
        bitmap = self.bitmap((0x12, 0x34, 0x56, 255))
        for header, header_size in (("bmpv4", 108), ("bmpv5", 124)):
            target = self.path(header + ".bmp")
            formats.save(bitmap, target, "raw", raw_mode="rgb",
                         raw_channels=(4, 4, 4, 0), raw_packing="packed",
                         raw_header=header)
            with open(target, "rb") as handle:
                data = handle.read()
            self.assertEqual(struct.unpack_from("<I", data, 14)[0],
                             header_size, header)
            self.assertEqual(struct.unpack_from("<H", data, 28)[0], 16,
                             header)

    def test_tga_header_wraps_custom_bits(self):
        bitmap = self.bitmap((0x12, 0x34, 0x56, 255),
                             (0xAB, 0xCD, 0xEF, 255))
        target = self.path("out.tga")
        formats.save(bitmap, target, "raw", raw_mode="rgb",
                     raw_channels=(4, 4, 4, 0), raw_packing="packed",
                     raw_header="tga")
        with open(target, "rb") as handle:
            data = handle.read()
        self.assertEqual(data[:3], b"\x00\x00\x02")
        self.assertEqual(struct.unpack_from("<HH", data, 12), (2, 1))
        self.assertEqual(data[16], 24)
        self.assertEqual(data[18:], b"\x56\x34\x12\xef\xcd\xab")

    def test_indexed_bmp_has_a_colour_table(self):
        bitmap = self.bitmap(*([(255, 255, 255, 255)] * 4
                               + [(0, 0, 0, 255)] * 4))
        target = self.path("indexed.bmp")
        formats.save(bitmap, target, "raw", raw_mode="indexed",
                     raw_depth=1, raw_packing="packed", raw_header="bmp")
        with open(target, "rb") as handle:
            data = handle.read()
        self.assertEqual(struct.unpack_from("<H", data, 28)[0], 1)
        offset = struct.unpack_from("<I", data, 10)[0]
        self.assertEqual(offset, 54 + 8)
        self.assertEqual(len(data) - offset, 4)
        with Image.open(target) as image:
            image.load()
            self.assertEqual(image.size, (8, 1))

    def test_indexed_tga_has_a_colour_map(self):
        bitmap = self.bitmap(*([(255, 255, 255, 255)] * 4
                               + [(0, 0, 0, 255)] * 4))
        target = self.path("indexed.tga")
        formats.save(bitmap, target, "raw", raw_mode="indexed",
                     raw_depth=1, raw_packing="packed", raw_header="tga")
        with open(target, "rb") as handle:
            data = handle.read()
        self.assertEqual(data[1:3], b"\x01\x01")
        self.assertEqual(struct.unpack_from("<H", data, 5)[0], 2)
        self.assertEqual(data[7], 24)
        # TGA colour-mapped images index one byte per pixel.
        self.assertEqual(data[16], 8)
        self.assertEqual(len(data), 18 + 6 + 8)

    def test_header_exports_open_in_a_reader(self):
        bitmap = self.bitmap((0x12, 0x34, 0x56, 255), (0xAB, 0xCD, 0xEF, 255),
                             (0x10, 0x20, 0x30, 255), (0x40, 0x50, 0x60, 255))
        layouts = [(1, 1, 1, 0), (2, 2, 2, 0), (3, 3, 2, 0), (4, 4, 4, 0),
                   (5, 5, 5, 0), (5, 6, 5, 0), (6, 6, 6, 0), (8, 8, 8, 0),
                   (10, 10, 10, 0), (8, 8, 8, 8)]
        # A unique file per case: Pillow keeps a Windows handle on palette
        # BMPs past close, so rewriting the same path would fail.
        for header in ("bmp", "bmpv4", "bmpv5", "tga"):
            for channels in layouts:
                name = "readable_%s_%s%s" % (
                    header, "".join(str(value) for value in channels),
                    bitdepth.header_ext(header))
                target = self.path(name)
                formats.save(bitmap, target, "raw", raw_mode="rgb",
                             raw_channels=channels, raw_header=header)
                with Image.open(target) as image:
                    image.load()
                    self.assertEqual(image.size, (4, 1), (header, channels))
        for depth in (1, 2, 4, 8):
            for header in ("bmp", "bmpv4", "bmpv5", "tga"):
                target = self.path("indexed_%s_%d%s"
                                   % (header, depth,
                                      bitdepth.header_ext(header)))
                formats.save(bitmap, target, "raw", raw_mode="indexed",
                             raw_depth=depth, raw_header=header)
                with Image.open(target) as image:
                    image.load()
                    self.assertEqual(image.size, (4, 1), (header, depth))

    def test_raw_size_accounts_for_headers(self):
        self.assertEqual(formats.raw_size(2, 1, 12, "packed", "raw"), 3)
        # BMP stores 12 bpp as 16 bpp 5:6:5 with its masks, TGA as 24 bpp.
        self.assertEqual(formats.raw_size(2, 1, 12, "packed", "bmp"), 70)
        self.assertEqual(formats.raw_size(2, 1, 12, "packed", "tga"), 24)
        self.assertEqual(formats.raw_size(2, 1, 24, "packed", "bmp",
                                          channels=(8, 8, 8, 0)), 62)
        self.assertEqual(formats.raw_size(2, 1, 8, "packed", "bmp",
                                          mode="indexed", depth=8),
                         14 + 40 + 1024 + 4)

    def test_header_names_and_extensions(self):
        self.assertEqual(bitdepth.header_key("BMP V4"), "bmpv4")
        self.assertEqual(bitdepth.header_label("bmpv5"), "BMP V5")
        self.assertEqual(bitdepth.header_ext("tga"), ".tga")
        with self.assertRaises(HexError):
            bitdepth.check_header("png")

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


class BitsPerPixelTests(unittest.TestCase):
    def test_common_totals_use_the_usual_layouts(self):
        self.assertEqual(bitdepth.widths_for_total(3), (1, 1, 1, 0))
        self.assertEqual(bitdepth.widths_for_total(6), (2, 2, 2, 0))
        self.assertEqual(bitdepth.widths_for_total(8), (3, 3, 2, 0))
        self.assertEqual(bitdepth.widths_for_total(12), (4, 4, 4, 0))
        self.assertEqual(bitdepth.widths_for_total(15), (5, 5, 5, 0))
        self.assertEqual(bitdepth.widths_for_total(16), (5, 6, 5, 0))
        self.assertEqual(bitdepth.widths_for_total(24), (8, 8, 8, 0))
        self.assertEqual(bitdepth.widths_for_total(32), (8, 8, 8, 8))

    def test_alpha_stays_in_use_when_the_layout_had_it(self):
        self.assertEqual(bitdepth.widths_for_total(16, (8, 8, 8, 8)),
                         (4, 4, 4, 4))
        self.assertEqual(bitdepth.widths_for_total(10, (8, 8, 8, 8)),
                         (3, 3, 2, 2))

    def test_every_total_from_one_to_thirty_two_adds_up(self):
        for total in range(1, bitdepth.MAX_BITS + 1):
            widths = bitdepth.widths_for_total(total)
            self.assertEqual(sum(widths), total, total)
            self.assertTrue(any(widths[:3]), total)
            self.assertLessEqual(max(widths), bitdepth.MAX_CHANNEL_BITS)

    def test_totals_outside_the_range_are_rejected(self):
        for bad in (0, -1, bitdepth.MAX_BITS + 1):
            with self.assertRaises(HexError):
                bitdepth.widths_for_total(bad)


if __name__ == "__main__":
    unittest.main()
