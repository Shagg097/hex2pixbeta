# hex2pix

A standalone pixel-art editor built around hex: type hex to paint, place hex
blocks, and select *bytes of pixel data* with art-program selection tools
(rectangle, ellipse, polygon, lasso, wand, similar). Mouse input is there for
drawing and selecting; the keyboard is the primary instrument.

Ships as a single Windows executable, `hex2pix.exe`, built from the Python
package in this folder.

## Run

```
hex2pix.exe                  # home page: presets, recent files
hex2pix.exe art.bmp          # open a file straight in the editor
```

From source, without building:

```
py -3 run_hex2pix.py
```

Requires Python 3.10+ with tkinter (standard on Windows) and Pillow.

## Home

Opening hex2pix without a file argument shows a start page in the spirit of
Krita's: the hex2pix wordmark, a grid of preset canvas tiles (16x16 up to
1920x1080), **Custom canvas...** and **Open image...** buttons, and a **Recent
documents** list on the right. Double-click an entry or press Enter to reopen
it; **Clear list** empties it; entries whose file has vanished drop out on
load. The list lives in `%APPDATA%\hex2pix\recent.json`.

A footer carries the version plus links to the keyboard reference, the byte
selection help and this manual. **File > Home** returns to the page at any
time, and **Continue** goes straight back to the editor. Passing a file on the
command line skips the page and opens that image.

## Canvas setup

**File > New canvas...** opens a dialog in the spirit of GIMP's: a preset list
(icon sizes up to Full HD), width and height with a **px / mm / in** unit
switch, a **keep ratio** lock, resolution in **ppi**, a live physical-size
readout, the color model (RGB or Grayscale at 8-bit integer precision) and an
**alpha channel** toggle. New canvases can be filled with transparency, white,
black, or the current foreground or background color.

**Image > Canvas properties...** reopens the same dialog on the open image, so
size, resolution, color model and alpha stay editable at any point. When the
pixel size changes you choose how:

* **pad/crop** leaves every pixel untouched and adds or removes canvas around
  it, positioned by a nine-way anchor;
* **resample** scales the artwork with nearest-neighbour or premultiplied
  bilinear filtering, so transparent pixels do not bleed dark fringes.

Resolution is metadata, not pixels: it drives the mm/in readout, is written
into PNG, TIFF, JPEG, WEBP and BMP files, and is restored when those files are
reopened. Every canvas change is one undo step, and undo restores the previous
size, resolution, color model and alpha along with the pixels. The status bar
always shows the current canvas as `WxH @ ppi ppi MODE`.

## Views

The canvas opens in **hex code view**: every pixel is drawn as its own hex
code, in its own color. Opaque pixels read `RRGGBB`; partly transparent pixels
read `RRGGBBAA`; fully transparent pixels show `......`. Selected pixels get a
tinted cell so byte selections stay visible in this view too. Codes too dark to
read are lightened just enough to be legible.

Only the visible cells are drawn, so this grid stays responsive on large
canvases instead of being flattened into one enormous picture.

Toggle to the rendered artwork with **View > Result image**, the **Hex / Image**
radios in the left panel, or the **H** key. In hex view `[` `]` and the wheel
change the code font size (6–22 pt); in image view they zoom the artwork, and
the pixel grid and checkerboard apply there. Tools, the cursor and every
selection work identically in both views.

**F11** toggles borderless fullscreen and **Escape** leaves it, restoring the
previous window geometry. The tool column scrolls on its own when the window
is too short for every panel, so no control is ever cut off at any window
size.

## Keyboard selection

The pixel cursor is a keyboard tool in the Dwarf Fortress tradition: arrows
move it, Ctrl+arrows move it eight pixels, and a rectangle is drawn with two
corners instead of a mouse drag.

With **Rect select** (Shift+M) or **Ellipse select** (Shift+O) active:

- **Enter** drops the first corner at the cursor.
- **Arrows** stretch the rubber band from it; the byte mask updates live and
the status bar shows the anchor.
- **Enter** again closes the selection and leaves it in place.
- **Escape** throws the rubber band away and restores the selection you had.

Shift+arrow starts a band from the current pixel without pressing Enter
first. The anchor is drawn dashed in both views: a cell outline in hex view,
a rectangle over the artwork in image view.

## Macros

**F9** (or Macro > Record / stop recording) records the keys the editor acts
on: cursor moves, hex digits, Enter, Delete, brush and zoom keys, tool
shortcuts, Escape and Ctrl+Z/Y/A/D. Keys that open dialogs are never
recorded, so playback can never pop a file dialog mid-sequence.

**F5** plays the recording back at about 20 keys per second through the same
key handler a person uses. Macro > Play repeatedly... asks for a repeat count,
Escape stops playback, and real keystrokes are ignored while it runs so the
sequence stays deterministic. Macro > Save/Load macro... stores the key list
as JSON (`{"format": "hex2pix-macro", ...}`); Clear recording empties it.

## Pattern fills

Two fill tools work from the mouse and from the keyboard cursor:

- **Line pattern** (Shift+L) fills the selected lines one after another,
giving each line the next row of the pattern. A two-row pattern becomes
alternating stripes, and Shift staggers every line by that many pattern
columns.
- **Structure** (Shift+K) builds a super tile from super-width x super-height
copies of the pattern, mirroring odd columns and rows into a kaleidoscope,
then repeats that tile over the selection. Shift staggers each tile row.

The pattern itself is a micro pixel canvas in the Pattern tab: click a cell to
paint it in the foreground colour, right-click or Ctrl+click to skip it. The
grid stays in sync with hex rows, so a pattern can be typed as
`FF0000 0000FF` / `. 00FF00` and sent to the grid, or painted cell by cell and
written back out. `.` cells skip the pixel instead of writing it, and the
pattern can also be lifted straight from the selection.

Scale repeats each pattern pixel N times. Enter at the keyboard cursor runs
whichever fill tool is active, so patterns and flood fills can be placed
without touching the mouse.

## Shapes, symmetry and palettes

Aseprite-style drawing aids on top of the brush:

- **Line** (Shift+N), **Rectangle** (Shift+R) and **Ellipse** (Shift+U) drag
  between two points with a live preview; the **Filled shapes** checkbox picks
  outline or solid. From the keyboard, Enter sets one end and Enter again
  commits, with arrows stretching the preview.
- **Gradient** (Shift+V) drags a direction and interpolates the foreground to
  the background colour across the selection; Enter applies it corner to
  corner.
- **Mirror** (left panel) mirrors brush strokes, shapes and erasing across the
  X axis, Y axis or both, like Aseprite's symmetry.
- **Tip** switches the brush between a square and a circle.
- **Palette** keeps a swatch grid: PICO-8, DB16 and Grayscale presets, `+ FG`
  to add the current colour, and Save/Load for GIMP `.gpl` palettes, the format
  Aseprite also reads and writes.
- Only the visible part of the canvas is drawn, so painting stays responsive on
  large sprites. The canvas is centred when it is smaller than the workspace;
  use the zoom buttons, **Zoom to fit**, the mouse wheel (which zooms around
  the pointer) or `[` / `]`.

## The hex idea

* **Type a hex color, press Enter.** Digits `0-9` and `A-F` fill a buffer that
  the status bar shows; `Enter` sets the foreground color and paints the pixel
  under the cursor, then advances the cursor one step right. Three, four, six
  and eight digit colors are all accepted (`F00`, `F008`, `FF0000`,
  `FF000080`).
* **Arrow keys move the pixel cursor**, so a full mini-icon can be drawn without
  touching the mouse.
* **Hex blocks** are rows of pixels as text. `FF0000 00FF00` is a two-pixel row;
  `;` or `|` starts a new row; `.` skips a pixel (leaves the artwork alone);
  `//` starts a comment. Runs of more than eight digits split into pixels.
  Blocks can be stamped, tiled or stretched, then scaled, flipped and rotated.
* **Recolor and byte operations** act on the selection: an `OLD=NEW` hex map, a
  hue shift, a palette ramp rotation, or `add subtract multiply xor nand` per
  channel with strength and clamp/wrap.

## Byte selections

A selection is a byte mask: one byte per pixel deciding whether that pixel's
four RGBA bytes take part in the next edit. Six tools build those masks, and
the **Move / Scale** tool moves or resizes what they selected.

| Tool | How it selects |
| --- | --- |
| Rectangle | drag a box |
| Ellipse | drag a box, rasterised to the inscribed ellipse |
| Polygon | click vertices, `Enter` or double-click closes; `Shift+Enter` adds a vertex under the cursor |
| Lasso | drag freehand; the path previews live, closes on release and is tidied before it fills |
| Wand | contiguous (4-connected) pixel data matching the clicked pixel |
| Similar | every matching pixel in the image, not just the connected run |

Tolerance (left panel) loosens the color comparison; `0` means exact bytes.
Hold **Shift** while selecting to add, **Ctrl** to subtract, **Alt** to
intersect. Right-drag a selection tool subtracts.

The **Move / Scale** tool turns a selection into a floating piece: drag inside
it to move those pixels (the original area is cleared), hold **Shift** while
dragging to scale them from the corner opposite the one you grabbed, hold
**Ctrl** to rotate them around their centre in 15° steps (nearest-neighbour
sampling, so quarter turns are exact), or press `Enter` to drop the piece with
its corner on the cursor. `Escape` puts the piece back untouched. The
selection follows the pixels, and the whole drag is one undo step.

The **Dump** tab shows the selected bytes as `offset  RRGGBBAA` rows, with the
selection's pixel count and byte count. **Copy dump** puts that text on the
clipboard; **Copy block** copies the selection as hex pixel rows.

## Layers

The **Layers** tab stacks bitmaps that share one selection and canvas size.
Each layer combines with the layers below it using its operation:

| Operation | Result |
| --- | --- |
| Normal | the layer covers what is below (the default) |
| Add | `below + above`, clamped |
| Subtract | `below - above`, clamped |
| Multiply | `below * above / 255` |
| Divide | `below * 255 / above` |

A pixel's alpha is the strength of the operation, so transparent pixels leave
the stack below alone and a partly transparent layer mixes the result. Where
the stack below is transparent the layer shows as it is. New layers start
**Normal**, so drawing covers instead of combining.

The **Opacity** slider under the layer list blends the active layer without
touching its pixels: it scales the strength of that layer's operation, so
`0%` hides the layer's contribution and `100%` uses the pixels as stored. The
percentage also appears in the layer row and the status bar, and dragging the
slider is one undo step.

Both views show the flattened result once there is more than one layer; the
**Dump** tab, **Copy block** and the byte tools still work on the active
layer, which is the one tools edit and type hex into. **New**, **Copy**,
**Delete**, **Raise** and **Lower** manage the stack, `Ctrl+Shift+N` adds a
layer, and double-clicking a layer row shows or hides that layer.

## Tool options

The bar above the canvas follows the active tool; every control edits the same
state the tool uses:

| Tool | Options |
| --- | --- |
| Pencil, Eraser | Size, Tip (square or circle), Mirror |
| Picker | pick the colour into FG or BG |
| Fill | Tolerance |
| Line | Width, Tip |
| Rectangle, Ellipse | Fill, Width, Tip |
| Gradient | reverse the direction (BG to FG) |
| Line pattern | Grid size, Scale, Shift |
| Structure fill | Grid size, Mirror tiles |
| Rect, Ellipse, Polygon select | selection mode: Replace, Add, Subtract, Intersect |
| Lasso | selection mode and Smooth path |
| Wand, Similar | Tolerance, Ignore alpha |
| Move / Scale | Rotate snap in degrees |
| Hand | zoom out, zoom in and Zoom to fit |

Brush size, tip, mirror and tolerance also stay in the left column.

## Camera

**Hand** (`Shift+H`) or the middle mouse button pans the canvas anywhere,
including past its edges — the view keeps a margin of empty space on every
side so the artwork can sit wherever you like. The wheel zooms smoothly in
1.25× steps around the pointer, from 12.5% to 6400%, and **Zoom to fit** picks
the exact ratio for the window (it can go below 100% for big sprites). `[`
and `]` zoom out and in.

## Mouse

* Left drag — draw with the current tool; right drag — erase.
* **Move / Scale**: left drag inside a selection moves it; **Shift**+drag
  scales it; **Ctrl**+drag rotates it.
* Middle drag or **Hand** — pan freely; wheel — zoom around the pointer.
* Click a swatch to change the foreground; click a recent swatch to reuse it.

## Keyboard

| Key | Action |
| --- | --- |
| Arrows | move the pixel cursor |
| `0-9 A-F` | type a hex color |
| Enter | close a polygon once it has three vertices (`Shift+Enter` adds a vertex); set that color and paint at the cursor; place a selection corner; or run the active fill/shape tool |
| Backspace | delete a typed digit |
| `G` | pick the color under the cursor |
| Delete | erase the pixel under the cursor |
| `+` / `-` | brush size |
| `[` / `]` | zoom out / in |
| Space | swap foreground and background |
| Shift + letter | pick a tool (Pencil, Eraser, Picker, Fill, Line, Rectangle, Ellipse, Gradient, Rect select, Ellipse select, Polygon, Lasso, Wand, Similar, Move / Scale, Hand) |
| Shift+T | move the selection by dragging, scale it with Shift+drag, rotate it with Ctrl+drag, or move its corner to the cursor with `Enter` |
| Ctrl+Z / Ctrl+Y | undo / redo |
| Ctrl+A / Ctrl+D | select all / deselect |
| Ctrl+C / Ctrl+V | copy / paste hex block text |
| Ctrl+O / Ctrl+S | open / save |
| Ctrl+E | export tab |
| Ctrl+Shift+N | new layer |

Tool letters are on `Shift` so that `A-F` can stay available for hex digits.

## File formats

Reading goes through Pillow, so PNG, BMP, GIF, TGA, ICO, TIFF, WEBP, JPEG and
PPM all open; `.txt`/`.hex` files open as hex blocks.

Writing:

| Format | Alpha | Notes |
| --- | --- | --- |
| PNG | yes | 8-bit RGBA |
| BMP 24-bit | no | hand-written header, transparency blended onto the matte |
| BMP 32-bit | yes | hand-written, alpha bytes preserved |
| TGA | yes | 32-bit |
| GIF | no | 256-color adaptive palette |
| ICO | yes | padded to a square entry, never resampled |
| TIFF | yes | RGBA |
| WEBP | yes | lossless |
| JPEG | no | flattened, quality setting |
| PPM | no | 24-bit binary `P6` |
| Hex text | n/a | every pixel as `RRGGBBAA`, `.` for transparent |

BMP and PPM are written by hand rather than by the imaging library, so the
24-bit BMP really is 24 bits per pixel with a `BITMAPINFOHEADER` — Pillow's
own RGBA-to-BMP path drops alpha and still claims 32 bits. The loader has its
own BMP parser too, so a file hex2pix wrote reopens with its alpha intact.

## Build the executable

```
build_exe.cmd
```

or directly:

```
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed --name hex2pix ^
    --paths . --hidden-import PIL._tkinter_finder ^
    --add-data "hex2pix\README.md;hex2pix" run_hex2pix.py
```

`dist\hex2pix.exe` is a single self-contained file; no Python install is
needed on the target machine.

## Tests

```
py -3 -m unittest discover -s hex2pix/tests -t .
py -3 run_hex2pix.py --selftest
```

The first command runs 113 assertions over the model, the canvas, formats,
patterns and macros. The second runs those plus 34 headless GUI checks —
building the window, selecting 25 pixels with a rectangle, typing a hex color
and painting it, undoing, running a wand selection, placing a block, painting
and erasing with the mouse, exporting a PNG, reading the byte dump, creating a
24x18 canvas at 144 ppi, scaling it to Grayscale without alpha, undoing that
back to the original canvas, building the canvas dialog and reading a spec out
of it, checking that the window opens in hex code view, toggling to the image
view and back, mapping a click in hex view to the right pixel, switching
between the home page and the editor, starting a preset canvas, reloading the
recent list from disk with its limit and missing-file filtering, drawing a
selection with two keyboard corners (and cancelling it), recording seven
keystrokes and replaying them onto an undone canvas, flood filling from the
keyboard, applying line and structural pattern fills, syncing the pattern hex
rows with the micro canvas, toggling fullscreen and checking the scrollable
tool column — then writes `hex2pix-selftest.txt`. The same command is
available in the frozen executable: `hex2pix.exe --selftest`.

## Limits

* Layers share one selection and canvas size; the stack is flat, with no groups or per-layer offsets.
* Undo keeps whole-image snapshots under a 96 MB budget (roughly 90 steps for
  a 512x512 image, fewer for larger).
* Selections are hard-edged byte masks; there is no feathering or anti-aliasing.
* No brush shapes beyond a square of pixels.
* Canvases are capped at 8192 pixels per side.
