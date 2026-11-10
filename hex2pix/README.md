# hex2pix

A standalone pixel-art editor built around hex: type hex to paint, place hex
blocks, and select *bytes of pixel data* with art-program selection tools
(rectangle, ellipse, polygon, lasso, wand, similar). Mouse input is there for
drawing and selecting; the keyboard is the primary instrument.

Ships as a single Windows executable, `hex2pix.exe`, built from the Python
package in this folder. That executable is self-contained — CPython 3.10,
Tcl/Tk and Pillow are bundled inside it — so **the released build runs on
Windows without Python installed**. Python is only needed for the source
checkout below.

## Run

```
hex2pix.exe                  # home page: presets, recent files
hex2pix.exe art.bmp          # open a file straight in the editor
```

The release also includes `hex2pix-portable.zip`: the same program as a folder,
with the runtime in `_internal\`, for machines whose antivirus blocks the
single file's `%TEMP%` unpacking. Both need 64-bit Windows 10 or 11; neither
needs an installer, administrator rights or Python.

From source, without building (this path does need Python):

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

## Automatic updates

The update check is optional, uses only the Python standard library, and every
update is opted into separately.

Packaged Windows builds check GitHub Releases shortly after launch; **Help >
Check for updates...** checks immediately and **Check for updates
automatically** toggles the startup check. When a newer release exists the
prompt shows its version and notes with **Download and install**, **Skip this
version** and **Later**; only the first downloads it, skipping remembers that
release until a newer one appears, and later asks again on the next launch.
The download is checked against the asset size and the SHA-256 digest
published by GitHub, then, after the editor closes, a helper replaces
`hex2pix.exe` and restarts it. Stable installs ignore prereleases; prerelease
installs may receive them. The folder must be writable
(portable installs are fine, `Program Files` is not). Source checkouts do not
self-replace and instead offer the release page. Attach the built
`hex2pix.exe` to a GitHub Release (with a version tag such as `v1.3.0b1`) for
that release to be installable.

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

The canvas opens in **pixel view**, so the rendered artwork is what you see
first: transparent pixels on the checkerboard, the pixel grid, and every
selection overlay drawn over the image. **View > Hex code view**, the
**Pixel / Hex** radios in the left panel, or the **H** key switch to the hex
view, where every pixel is drawn as its own hex code in its own color. Opaque
pixels read `RRGGBB`; partly transparent pixels read `RRGGBBAA`; fully
transparent pixels show `......`. Selected pixels get a tinted cell so byte
selections stay visible there too, and codes too dark to read are lightened
just enough to be legible.

Only the visible cells are drawn, so the hex grid stays responsive on large
canvases instead of being flattened into one enormous picture.

In pixel view `[` `]` and the wheel zoom the artwork; in hex view they change
the code font size (6–22 pt). Tools, the cursor and every selection work
identically in both views.

**F11** toggles borderless fullscreen and **Escape** leaves it, restoring the
previous window geometry. The tool column scrolls on its own when the window
is too short for every panel, so no control is ever cut off at any window
size, and the tool-options bar above the canvas grows a horizontal scrollbar
when the active tool has more controls than fit.

The editor starts in **dark mode**. **View > Dark mode** switches the whole
window -- menus, dockers, tool cards, fields, text panels, the colour wheel
and the start page -- to the light palette and back. The same View menu ends
with one checkbutton per right-side docker (**Block**, **Layers**, **Bytes**,
**Dump**, **Export**, **Pattern**, **Dither**), so any tab can be hidden and
brought back without losing its contents or its place in the tab order.

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

## Blur, shuffle and text

Three pixel-level tools share the brush footprint, the mirror setting and the
selection mask, so each one can be confined to a region:

- **Blur** (Shift+B) replaces every pixel under the brush with the average of
  its neighbourhood, mixed back towards the original by **Strength**.
  **Amount** widens the neighbourhood from 0 to 8 pixels, independently of
  the brush footprint. A drag reads from the snapshot taken when the stroke
  started, so overlapping dabs soften once instead of compounding into mush.
- **Shuffle** (Shift+J) reorders the colour bytes under the brush: its default
  mode permutes them, **Scatter** gives each pixel a random neighbour's colour (so
  colours repeat) and **Noise** jitters each channel by **Amount** percent
  while keeping the pixel's alpha.
- **Text** (Shift+X) stamps the **Text** option in the foreground colour with
  its top-left corner at the click point. The default is the built-in 5x7 font
  with whole-pixel **Scale** and blank **Spacing** columns; unknown characters
  fall back to `?`. The **Font** menu lists every usable family installed on
  the operating system, with **Size**, **Bold** and **Italic** for those faces.
  **Load...** adds a specific TTF, OTF, TTC, FON, Type 1, BDF or PCF file. The
  result is ordinary pixels that the hex panels and byte tools can edit.

## Spray can, curves and contours

- **Spray can** (Shift+A) sprays foreground dots inside the brush footprint:
  every pixel of the tip is painted with **Flow**% probability, and holding the
  button keeps spraying at the cursor, so passes build up like a real
  spray can. Dragging sprays along the stroke; the whole press is one undo step.
  **Blur** is a 0-100 slider that softens the spray itself: at `0` every dot is
  flat colour, and higher values fade the pixels at the tip's edge into whatever
  is already underneath, so the spray gets a soft feathered rim. **Multicolor**
  draws each sprayed pixel from the current palette instead of the foreground
  colour, and works together with Blur.
- **Curve** (Shift+C) drags a curve between two points. **Bend** pushes the
  middle sideways by a percentage of half the chord: `0` is a straight line,
  `100` arcs half the chord out, negative bends the other way. **Vectors**
  splits the drag into 1-8 arcs, so `2` with **Side** set to *Alternate* draws
  an S-wave, `3` or more a ripple, and *Left* / *Right* keeps every arc on the
  same side of the drag. Width, tip and mirror work as they do for the other
  shape tools, and two `Enter` presses draw one from the keyboard.
- **Contour** (Shift+G) outlines a region with the foreground colour: a ring
  **Width** pixels just outside or just inside it, following 4- or
  8-connected edges. With a selection it outlines the selection; with nothing
  selected it outlines everything opaque in the layer. The ring is painted
  as-is, including the part that falls outside the selection, because the
  selection is what is being outlined. One click is one undo step.

## Dithering and art filters

The **Dither** tab reduces the selection (or the whole canvas) to the two
current colours, which is how a small palette gets its shading, and it also
holds the bare-necessity filters. Every control works on the selection when
there is one, and the whole active layer when there is not.

| Dither mode | What it does |
| --- | --- |
| Bayer 2x2 / 4x4 / 8x8 | ordered dot patterns from the recursive Bayer matrix, stable and tileable |
| Floyd-Steinberg | error diffusion, organic mixes with no visible grid |
| Pattern | the block painted in the **Pattern** tab becomes the matrix: bright cells turn into FG pixels, so a checkerboard, hatch or logo gives a custom dither, and `.` cells are left untouched |
| Noise | white-noise threshold, grainy shading with no visible grid |
| Lines | horizontal scanline bands, **Scale** rows tall |

Four settings tune every mode: **Bias** shifts the light/dark cut, so negative
keeps more FG; **Strength** blends the dithered colour back towards the pixel
it replaced, so a low value fades the dither out over the artwork; **Scale**
spaces out the Pattern block and the Lines bands; **Serpentine scan** makes
Floyd-Steinberg reverse direction on alternate rows, which removes the
worm-like artefacts of a single-direction scan.

The filters are **Grayscale** (perceived luminance), **Posterize** (2-32
levels), **Threshold** (black-and-white cut-off), **Brightness** and
**Contrast** (tone), **Saturation** (drain to grey or push the channels
apart), **Hue shift** (rotate every hue), **Noise** (stable per-pixel grain),
**Sepia** (warm brown ramp) and **Find edges** (Sobel edge strength as grey).
Each is one undo step.

The Gradient tool's **Dither** option runs the same modes over a gradient
fill, so an FG-to-BG ramp becomes a dithered band instead of a smooth blend.
It reads Bias, Strength, Scale and Serpentine from the Dither tab, and Pattern
reads the Pattern tab block. Dithering is defined over one axis, so it applies
to two-colour linear gradients; **Radial**, **Whole palette** and non-clamped
spreads stay smooth.

## Shapes, symmetry and palettes

Aseprite-style drawing aids on top of the brush:

- **Line** (Shift+N), **Rectangle** (Shift+R), **Ellipse** (Shift+U),
  **Triangle**, **Diamond**, **Star** and **Arrow** drag between two points
  with a live preview; the **Filled shapes** checkbox picks outline or solid,
  and **Background** paints the shape in the background colour so it cuts into
  the artwork. The four preset polygons have no Shift shortcut; open the
  Shapes folder in the tool palette for them. From the keyboard, Enter sets
  one end and Enter again commits, with arrows stretching the preview.
- **Gradient** (Shift+V) drags a direction and interpolates the foreground to
  the background colour across the selection; **Radial** uses the distance
  from the drag start, **Whole palette** walks the palette instead, and
  **Spread** clamps, repeats or reflects the ramp. Enter applies it corner to
  corner.
- **Mirror** (left panel) mirrors brush strokes, shapes and erasing across the
  X axis, Y axis or both, like Aseprite's symmetry.
- **Tip** switches the brush between a square, a circle and a **Custom**
  footprint you draw on a 9×9 grid (**Edit…**), shared by every brush-like tool
  including Blur and Shuffle. The Pencil also has **Pattern tip**, which stamps
  the Pattern tab's grid as the brush in its own colours.
- **Palette** keeps a scrolling swatch grid: PICO-8, DB16, Grayscale and the
  256-colour **256 Cube** preset. Left-click a swatch to use its colour,
  right-click to edit it, and **New...** builds a palette from scratch (name,
  1-256 slots, black/white/foreground/gray fill). **+ FG**, **Set** and
  **Del** refine it; Save/Load handle `.256` hex lists, GIMP `.gpl` and JASC
  `.pal` files, capped at 256 colours. **Map to palette** (or **Map +
  dither**, also under Image) recolours the layer or selection to the palette,
  diffusing the error Floyd-Steinberg style when dithering.
- **Colour wheel** above the hex field: click the hue ring or the
  saturation/value square for the foreground, right-click either for the
  background; the hex and alpha fields follow along. GIF export already
  writes a 256-colour adaptive palette.
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

Polygon and lasso masks are filled solid: any pocket enclosed by a path that
circles back on itself is selected too, so a wobbly lasso or a polygon whose
outline crosses itself cannot leave holes inside the selection.

Tolerance (left panel) loosens the color comparison; `0` means exact bytes.
Hold **Shift** while selecting to add, **Ctrl** to subtract, **Alt** to
intersect. A right-drag that starts outside the selection subtracts;
`Ctrl`+drag subtracts anywhere. Right-click inside a selection for
**Delete**, **Split to new layer**, **Copy to new layer**, **Copy to
clipboard** and **Copy to pattern**.

The **Move / Scale** tool turns a selection into a floating piece: drag inside
it to move those pixels (the original area is cleared), hold **Shift** while
dragging to scale them from the corner opposite the one you grabbed, hold
**Ctrl** to rotate them around their centre in 15° steps (nearest-neighbour
sampling, so quarter turns are exact), or press `Enter` to drop the piece with
its corner on the cursor. `Escape` puts the piece back untouched. Scaling is
uniform and anchored: one factor drives both axes, the opposite corner stays
put, and the drag starts from whatever size the piece already has, so it
cannot squash the artwork or collapse on the first pixel of movement. A
rotation grab inside the middle three pixels holds the current angle until
the pointer leaves that dead zone, so a click near the centre cannot spin the
piece. The selection follows the pixels, and the whole drag is one undo step.

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
layer, dragging a row restacks it, double-clicking shows or hides it, and
`Alt+Up` / `Alt+Down` restack the active layer, and `Alt+N`, `Alt+C` and
`Alt+Delete` add, copy and remove layers from the keyboard. `Escape`
deselects instantly.

## Tool palette

The left column shows every tool as an icon tile, six rows of four, in the same
order as the menus. Hover a tile for its name, shortcut and a one-line
reminder; click or press `Shift` plus its letter to pick it. The active tile is
blue, and the status bar always names the current tool. The artwork lives in
`hex2pix/assets/` as 16x16 BMPs (`pencilbtn.bmp`, `lassobtn.bmp`, ...; the
Line pattern tile is `linebylinebtn.bmp` and Structure fill is
`paternbtn.bmp`), so a tile can be redrawn without touching code; a tool
without artwork falls back to a text button.

## Tool options

The bar above the canvas follows the active tool; every control edits the same
state the tool uses:

| Tool | Options |
| --- | --- |
| Pencil, Eraser | Size, Tip (square, circle or custom), Mirror, Pattern tip (Pencil) |
| Picker | pick the colour into FG or BG |
| Fill | Tolerance |
| Line | Width, Tip, Background |
| Rectangle, Ellipse, Triangle, Diamond, Star, Arrow | Fill, Background, Width, Tip |
| Gradient | Radial, Whole palette, reverse the direction (BG to FG), Spread (Clamp, Repeat, Reflect), Dither mode (two-colour linear gradients; Bias/Strength/Scale from the Dither tab) |
| Line pattern | Grid size, Scale, Shift |
| Structure fill | Grid size, Mirror tiles |
| Blur | Size, Tip, Strength, Smear, Mirror |
| Shuffle | Size, Tip, Mode (Shuffle, Scatter, Noise), Amount, Blur |
| Text | text, Font (installed OS families or Load...), Size, Bold, Italic; built-in 5x7 uses Scale and Spacing |
| Spray can | Size, Tip, Flow %, Blur, Multicolor |
| Curve | Width, Tip, Bend %, Vectors (1-8), Side (left, right, alternate) |
| Contour | Width, Side (outside or inside), Connect (4 or 8 edges) |
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
1.25× steps around the pointer, from a floor of 1 : (longer side × 50,000) up to
6400%, and **Zoom to fit** picks
the exact ratio for the window (it can go below 100% for big sprites). `[`
and `]` zoom out and in. Double-click the middle button (or double-click with
**Hand**) to fit the artwork, **Shift**+wheel scrolls sideways, and
**Ctrl**+wheel changes the brush size without leaving the canvas.

## Mouse

* Left press/drag paints with the active tool. For paint tools the right
  button erases; with a selection tool it subtracts; on controls it keeps the
  control's own secondary action.
* **Move / Scale**: left drag inside a selection moves it; **Shift**+drag
  scales it while keeping its proportions; **Ctrl**+drag rotates it around
  its centre.
* **Shift** while dragging **Line** snaps to horizontal, vertical or 45°;
  with **Rectangle** and **Ellipse** it draws a square or circle.
* Selection tools: **Shift** adds, **Ctrl** subtracts, **Alt** intersects and
  a right-drag subtracts. **Polygon** closes on a double-click, and **Lasso**
  closes and tidies itself on release.
* Middle drag or **Hand** pans freely; double-click either to zoom to fit.
  Wheel zooms around the pointer, **Shift**+wheel pans sideways, and
  **Ctrl**+wheel resizes the brush (or the hex code font in hex view).
* Click a swatch to change the foreground, right-click it to edit it; click a
  recent swatch to reuse it. The colour wheel follows the same rule: left for
  FG, right for BG.

## Keyboard

| Key | Action |
| --- | --- |
| Arrows | move the pixel cursor |
| `0-9 A-F` | type a hex color |
| Enter | close a polygon once it has three vertices (`Shift+Enter` adds a vertex); set that color and paint at the cursor; place a selection corner; or run the active fill/shape tool |
| Backspace | delete a typed digit |
| `G` | pick the color under the cursor |
| Delete | erase the pixel under the cursor, or the pixels inside the selection |
| `+` / `-` | brush size |
| `[` / `]` | zoom out / in |
| Space | swap foreground and background |
| Shift + letter | pick a tool (Pencil, Eraser, Spray can, Picker, Fill, Line, Curve, Rectangle, Ellipse, Gradient, Contour, Blur, Shuffle, Text, Line pattern, Structure fill, Rect select, Ellipse select, Polygon, Lasso, Wand, Similar, Move / Scale, Hand; Triangle, Diamond, Star and Arrow live in the Shapes folder) |
| Shift+T | move the selection by dragging, scale it with Shift+drag, rotate it with Ctrl+drag, or move its corner to the cursor with `Enter` |
| Ctrl+Z / Ctrl+Y | undo / redo |
| Ctrl+A / Ctrl+D | select all / deselect (the sidebar buttons do the same) |
| Escape | cancel the current step, leave fullscreen and deselect |
| Ctrl+C / Ctrl+V | copy / paste hex block text |
| Ctrl+O / Ctrl+S | open / save |
| Ctrl+E | save and export docker |
| Ctrl+Shift+N | new layer |
| Alt+N / Alt+C / Alt+Delete | new / copy / delete the active layer |
| Alt+Up / Alt+Down | restack the active layer |

Tool letters are on `Shift` so that `A-F` can stay available for hex digits.

## Projects (.h2p)

**File > Save project as...** writes the whole working session to a `.h2p`
file: a zip holding a `project.json` header, one raw RGBA entry per layer, and
the selection mask. Layers stay separate on disk, with their names, order,
visibility, opacity and blend operation, so reopening a project gives back the
same stack instead of a flattened picture.

The header also carries the canvas size, resolution, colour model and alpha
flag, the active layer, and the editor state: palette and its name, the Pattern
tab block, mirror setting, foreground and background colours, and hex/image
view.

**File > Open project...** (or **Open...**, which recognises `.h2p`) restores
all of it in one step. Once the document path is a `.h2p`, **Ctrl+S** saves the
project rather than flattening it to an image, and **File > Save As...** offers
the project type next to every image write format. A malformed or foreign zip
is reported as an error instead of being loaded partially.

The **Export** docker (**Ctrl+E**) holds saving and exporting in one place.
**Save** and **Save as...** write the project file and show its path; the image
half collects the format, source (whole image, current layer, selection, or one
file per visible layer), trim, scale, sampling, PPI, transparency or matte,
JPEG quality, GIF colours and PNG level, then **Export image** writes the file
from the same panel. **Variable bits per pixel** switches the panel to custom
depth output, with a header choice (Raw, BMP, BMP V4, BMP V5 or TGA), indexed or
RGB(A) colour, per-channel widths, and packed or byte-aligned packing.

## File formats

Reading goes through Pillow, so PNG, BMP, GIF, TGA, ICO, TIFF, WEBP, JPEG and
PPM all open; `.txt`/`.hex` files open as hex blocks.

Writing, from the Save dialog or the Export docker. The docker's image options
apply to every format: source, trim transparent edges, 1–32× scale with nearest
or bilinear sampling, the PPI stored in the file, keeping alpha or blending
onto a matte colour, JPEG quality, GIF palette size and PNG compression level.

| Format | Alpha | Notes |
| --- | --- | --- |
| PNG | yes | 8-bit RGBA |
| BMP 24-bit | no | hand-written header, transparency blended onto the matte |
| BMP 32-bit | yes | hand-written, alpha bytes preserved |
| TGA | yes | 32-bit |
| GIF | no | adaptive palette, colour count adjustable |
| ICO | yes | padded to a square entry, never resampled |
| TIFF | yes | RGBA |
| WEBP | yes | lossless |
| JPEG | no | flattened, quality setting |
| PPM | no | 24-bit binary `P6` |
| Raw pixel bits | optional | 1–32 bpp, Raw/BMP/BMP V4/BMP V5/TGA headers, indexed or RGB(A), packed or byte-aligned |
| Hex text | n/a | every pixel as `RRGGBBAA`, `.` for transparent |

BMP and PPM are written by hand rather than by the imaging library, so the
24-bit BMP really is 24 bits per pixel with a `BITMAPINFOHEADER` — Pillow's
own RGBA-to-BMP path drops alpha and still claims 32 bits. The loader has its
own BMP parser too, so a file hex2pix wrote reopens with its alpha intact.

### Raw bits

**Variable bits per pixel** in the Export docker turns on the custom encoder.
The **Header** choice is **Raw (none)** for values only, **BMP**, **BMP V4** or
**BMP V5** for a BMP file header, or **TGA** for a TGA 2.0 header; indexed
output gets a colour table. Colour is **Indexed** (1–8 bpp, adaptive palette)
or **RGB(A)** (per-channel widths). **Packed** runs pixels together MSB first,
so 12-bit 4:4:4 is two pixels in three bytes (`rrrrgggg bbbbrrrr ggggbbbb`)
and 6-bit 2:2:2 is four in three (`rrggbbrr ggbbrrgg bbrrggbb`); **Aligned**
gives each pixel its own bytes with zero high bits (`0000rrrr ggggbbbb` and
`00rrggbb`). Nonstandard RGB depths are written as declared for custom
readers; the standard indexed depths produce normal BMP/TGA files.

## Build the executable

```
packaging\build_exe.cmd
```

or call the specs directly from the repository root:

```
py -3 -m PyInstaller --noconfirm --clean packaging\hex2pix.spec
py -3 -m PyInstaller --noconfirm --clean packaging\hex2pix_onedir.spec
```

`dist\hex2pix.exe` is a single self-contained file; no Python install is
needed on the target machine. `packaging\build_exe.cmd` also writes
`dist\hex2pix-portable.zip`: the same program as a folder that keeps its
runtime DLLs beside the executable, which fixes a start-up failure of the
single file that reads `Failed to load Python DLL`.

On the `retro-compat` branch, `packaging\cxfreeze_setup.py` builds the
XP-compatible portable folder instead:

```
python38-32\python.exe packaging\cxfreeze_setup.py build_exe --build-exe dist\hex2pix-retro --silent
```

## Tests

```
py -3 -m unittest discover -s hex2pix/tests -t .
py -3 run_hex2pix.py --selftest
```

The first command runs 462 tests over the model, the canvas, formats, colours,
palettes, patterns, dithering, macros and update checks. The second runs those
plus a headless GUI pass that builds the window and exercises painting, erasing,
byte and wand
selections, canvas resizing, undo, home/recent navigation, pattern fills,
fullscreen, dark/light theming, all docker toggles and the shape/mouse helpers;
it writes `hex2pix-selftest.txt`. The same command is available in the frozen
executable: `hex2pix.exe --selftest`.

## Limits

* Layers share one selection and canvas size; the stack is flat, with no groups or per-layer offsets.
* Undo keeps whole-image snapshots under a 96 MB budget (roughly 90 steps for
  a 512x512 image, fewer for larger).
* Selections are hard-edged byte masks; there is no feathering or anti-aliasing.
* Brush tips are square or circle; there are no custom brush stamps.
* Automatic updates only replace the single-file Windows executable and need
  write access to its folder; the portable folder build points at the release
  page instead.
* Canvases are capped at 8192 pixels per side.
