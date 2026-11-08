# Hex Kit for Aseprite

Keyboard-first hex pixel editing for pixel art, built around the data model of a
**24-bit `.bmp`**: 8 bits per channel, RGB, no alpha channel.

Type colors as hex, stamp blocks of pixel data, flood a selection with a pattern,
recolor a region, and combine hex values into existing pixels with
`add`, `subtract`, `multiply`, `xor` and `nand`.

## Install

Either:

* Double-click `hexkit-1.0.0.aseprite-extension` (or use
  *Edit > Preferences > Extensions > Add Extension*), then restart Aseprite; or
* Copy this folder into the Aseprite extensions folder:
  * Windows: `%APPDATA%\Aseprite\extensions\hex-kit\`
  * macOS: `~/Library/Application Support/Aseprite/extensions/hex-kit/`
  * Linux: `~/.config/aseprite/extensions/hex-kit/`

Then open **File > Scripts > Hex Kit**. Every tool is a real Aseprite command, so
you can bind it in *Edit > Keyboard Shortcuts* (search for `HexKit`).

## Tools

| Command | What it does |
| --- | --- |
| **Hex Kit Panel...** | Launcher with a button for every tool. |
| **Keyboard Pixel Cursor...** | Paint pixels with the keyboard only: arrows move, hex is typed, Enter paints and steps forward. |
| **Set Foreground Color from Hex...** | Enter `#RRGGBB` (or 3/4/8 digit) as the foreground color, optionally the background too. |
| **Copy Foreground Hex** | Puts `#RRGGBB` on the clipboard. |
| **Place Pattern / Hex Block...** | Stamp, tile or stretch hex pixel data onto the canvas or a selection, with scale, flip, rotation and mirror tiling. |
| **Copy Region as Hex Block** | Copies a selection (or the layer's pixels) as hex rows, ready to paste back or elsewhere. |
| **Apply Hex Operation to Region...** | Applies add / subtract / multiply / xor / nand between the region's pixels and a hex value or hex block. |
| **Recolor Selected Region...** | Remaps region colors by hex map, hue shift, or palette rotation of the region's own ramp; can update the sprite palette too. |
| **Rebuild Palette from Region** | Replaces the sprite palette with the colors actually used in the region, sorted by luminance. |
| **Save as 24-bit BMP...** | Renders the current frame, composites transparency over a matte color, saves an opaque RGB `.bmp`. |
| **Check 24-bit BMP Readiness** | Reports color mode, frames, layers, and how many pixels are not fully opaque. |

All tools work on the selection when there is one, otherwise on the whole layer.

## Hex data formats

A *block* is hex pixel data, one row per line. Rows can also be separated with
`;` or `|`, which is handy if your Aseprite build shows a single-line text field.

```text
FF0000 00FF00 0000FF
1E1E28 . 1E1E28 .
```

* `FF0000`, `#FF0000`, `0xRRGGBB` — one RGB pixel (alpha 255).
* `F80` / `#F80A` — 3 and 4 digit shorthand, like CSS.
* `FF000080` — `RRGGBBAA` (alpha last, not ARGB).
* `.` or `-` or `_` — skip this pixel, leaving what is already there.
* Pixels may be separated by spaces, tabs or commas.
* A long run such as `FF0000FF0000` is split into 3-byte pixels; if the length
  does not divide by 3 it is split into 4-byte `RRGGBBAA` pixels instead.
* `//` starts a comment.

Hex maps (in **Recolor Selected Region**) use `OLD=NEW` per line, and `->`
works as the arrow:

```text
FF0000=FFAA00
#1E1E28 -> #2E2E3A
```

## Keyboard reference (Keyboard Pixel Cursor)

| Key | Action |
| --- | --- |
| Arrow keys | Move the cursor one pixel |
| Shift + arrows | Jump by the brush size (at least 8) |
| `0`-`9`, `a`-`f` | Type the color, shown live as `#RRGGBB` |
| Backspace | Delete the last typed hex digit |
| Enter / Space | Paint and step right by the brush size |
| `g` | Pick the color under the cursor into the hex buffer |
| Delete | Erase the brush area (transparent) |
| `+` / `-` | Brush size (square block, 1-32) |
| `[` / `]` | Zoom the preview |
| Esc | Close the tool |

Typed digits are right-aligned: `F80` is `0x000F80`. Six digits is a normal
`RRGGBB` color, eight digits adds alpha.

## 24-bit BMP notes

* Tools require RGB color mode and offer to convert the sprite if it is not
  already RGB (`Sprite > Color Mode > RGB` does the same thing).
* A 24-bit BMP has no transparency. **Save as 24-bit BMP** renders the current
  frame with all layers composited, blends every non-opaque pixel over a matte
  color you choose, and writes an opaque RGB image. Only the current frame is
  written.
* Multi-layer and animated sprites are exported as one flat frame, so the saved
  file is the same data a 24-bit BMP can hold.
* Aseprite's BMP writer chooses the bit depth from the image; guessing from the
  rendered, fully opaque RGB image, the file is 24-bit. If your build writes a
  32-bit BMP, the alpha channel is uniformly 255, so the 24-bit interpretation is
  identical.

## Files

* `hexkit.lua` — the whole plugin (pure Lua core + Aseprite UI).
* `package.json` — Aseprite extension manifest.
* `test/run.lua` — tests for the pure core (hex parsing, blocks, transforms,
  tiling, bitwise operations, HSV). It runs in any Lua interpreter; no Aseprite
  required.

```sh
lua test/run.lua        # or: npx fengari test/run.lua
```

The pure core is also exposed as the global `HexKit` table, so other scripts can
reuse `HexKit.parseHex`, `HexKit.parseBlock`, `HexKit.transformBlock`,
`HexKit.combine` and `HexKit.blit`.

## Planned limits

* Indexed and grayscale sprites are not supported (the tools convert to RGB).
* `Copy Region as Hex Block` is capped at 128x128 pixels.
* The pattern stored in preferences is capped at 4000 characters.
