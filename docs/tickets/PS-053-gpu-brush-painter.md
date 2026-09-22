# PS-053 Brush painter on the GPU

Epic F. Size L. Milestone M3.

## v2 behaviour

`brush_painter` (`operators/image_operators.py:238-400`) configures
`BrushPainterCore` (`operators/image_filters/brush_painter_core.py`,
`apply_brush_painting` at `:1273`): coverage density, min/max brush
scale, start/end opacity, step count, gradient threshold, gaussian sigma,
optional HSV shift, fixed seed, random rotation range, UV-seam
duplication. Brush sources: PRESET (folders under
`image_filters/brush_presets`), FOLDER, SINGLE file, DEFAULT circular.
Per-tile numpy stamping with positions/scales/rotations derived from the
image gradient field; UV-seam index mirrors stamps across seams. Minutes
at 4K.

### v2 UI

All paths are relative to `~/paintsystem`. Line numbers without a file
refer to `operators/image_operators.py`; "core" means
`operators/image_filters/brush_painter_core.py`.

Entry point: "Brush Painter" (BRUSH_DATA), the first item of
`MAT_MT_ImageFilterMenu` (`panels/layers_panels.py:686-705`). The menu
opens from "Filters" in the header of the layer settings "Image"
section, or from "Apply Image Filters" in the bake box while Use Baked
is on. PS-051's v2 UI section describes both.

Operator `paint_system.brush_painter` (`:238-455`), bl_label "Brush
Painter", REGISTER and UNDO. `invoke` (`:402-404`) calls
`invoke_get_image` (`operators/common.py:293-301`), which stores the
name of the channel's bake image when Use Baked is on, else of the
active layer's image. It then opens `invoke_props_dialog` at the
default width.

Dialog (`draw`, `:406-455`), property split on and decorate off, three
boxes in order:

1. Brush source, one column:
   - `brush_mode` "Brush Mode", a dropdown (`:255-265`): "Brush Preset"
     (PRESET, BRUSH_DATA, the default), "Brush Folder" (FOLDER, custom
     `folder`), "Single Brush" (SINGLE, GREASEPENCIL), "Circular"
     (DEFAULT, custom `channel`).
   - PRESET: `presets` as "Preset", a dropdown of the folder names in
     `operators/image_filters/brush_presets` ("Gouache Short 1",
     "Gouache Short 2"). The list is read once, when the class is
     defined, in unsorted `os.listdir` order, and the enum has no
     explicit default, so the first entry the file system returns is
     the default (`:267-271`,
     `operators/image_filters/__init__.py:6-13`).
   - FOLDER: `brush_folder_path` "Brush Folder" (DIR_PATH).
   - SINGLE: `brush_texture_path` "Single Brush Texture" (FILE_PATH).
   - DEFAULT adds nothing.
2. A centred row with the label "Brush Parameters" (BRUSH_DATA), then
   one column:
   - `brush_coverage_density` as "Coverage Density", slider, default
     0.7, range 0.1-1.0.
   - `steps` "Steps", default 4, range 1-20.
   - `use_hsv_shift` "Use HSV Shift", default off. When on, "Hue
     Shift", "Saturation Shift" and "Value Shift" follow as sliders,
     0-1, default 0. They are SKIP_SAVE, so they reset on every call
     (`:288-291`). They are jitter, not an offset: each stamp's
     sampled colour gets a random hue shift within plus or minus half
     of Hue Shift times 360 degrees, and random saturation and value
     shifts within plus or minus half of their values (core
     `:411-486`, the draws at `:442-457`).
   - `use_random_seed` "Use Random Seed", default off. When on,
     `random_seed` "Random Seed" follows, default 42, range 0-1000000.
   - `custom_image_gradient` "Use Custom Image Gradient", default off.
     When on, a `prop_search` of `custom_image_name` over
     `bpy.data.images` follows, text "Image" (`:293-294, 434-436`).
   - `use_uv_seam_duplication` "Use UV Seam Duplication", default off.
   - `use_random_rotation` "Random Rotation", default off. When on,
     `random_rotation_range` "Rotation Range" follows as a slider,
     default 360, range 0-360; a stamp deviates by up to half of it
     either way (`:300-311`).
3. A centred row with the label "Advanced Settings" (TOOL_SETTINGS),
   then `split(factor=0.5)` with property split off:
   - Left column: `min_brush_scale` "Min Scale" (default 0.03) and
     `max_brush_scale` "Max Scale" (default 0.1), sliders over
     0.001-1.0, then `gradient_threshold` "Gradient Threshold", slider,
     default 0, range 0-1.
   - Right column: `start_opacity` "Start Opacity" (default 0.4) and
     `end_opacity` "End Opacity" (default 1.0), sliders over 0-1, then
     `gaussian_sigma` "Gaussian Sigma", an integer field, default 3,
     range 0-10.

No field sets a fixed rotation: `brush_rotation_offset` stays 0.0
(core `:87`).

What OK does (`execute`, `:334-400`):

- Brush source (`:361-375`, core `:1290-1295`). PRESET loads every
  png, jpg, jpeg, bmp and tiff in the preset folder (core `:286-315`).
  FOLDER and SINGLE use their path when it exists; otherwise they
  report "Brush folder not found: <path>" or "Brush texture not found:
  <path>" (WARNING) and fall back; an empty path falls back without a
  report. DEFAULT, and every fallback, paints with a soft circle with
  a linear falloff, built as a 50-pixel mask and resized per step like
  any brush (core `:247-254, 1249-1250`). A brush image that loads
  with four channels uses its alpha as the mask, otherwise its grey
  value, and a non-square one is padded to a square (core
  `:256-284`).
- Steps (core `:1237-1271`, `:375-392`). Step k of n paints at a scale
  interpolated from Max Scale down to Min Scale and an opacity from
  Start to End Opacity; with one step, Min Scale at End Opacity. The
  brush size is that scale times the tile's shorter side. The stamp
  count is the tile area times Coverage Density over 0.7 times the
  brushes' average covered area, clamped to at least 50 and at most
  an eighth of the tile area. A stamp is skipped when the blurred
  colour at its centre has alpha at most 1e-6, or when the gradient
  magnitude there, normalised by the tile's strongest, is below
  Gradient Threshold (core `:991-1005, 365-371`).
- Target. A copy of the image `get_image` returns is painted. The copy
  replaces the channel's bake image when Use Baked is on, else the
  active layer's image (`:383, 396-399`). Unlike Blur and Sharpen,
  this handles the bake case.
- Custom image gradient (`:337-339`, core `:1302-1306`). The image
  named in the field is read; an empty or unknown name silently falls
  back to the painted image's own gradient. For each UDIM tile whose
  number also exists in the custom image, that tile is blurred,
  premultiplied, at Gaussian Sigma. Its luma is then blurred again and
  passed through Sobel (core `:942-947, 359-373`). That magnitude and
  angle drive the Gradient Threshold skip and the stroke angle (core
  `:1000-1007`). The stamp colour is still sampled from the painted
  image's own blur (core `:991`). The custom image is not resized: it
  is indexed with the painted image's texel coordinates, so a smaller
  image raises an index error and a larger one contributes only its
  top-left region.
- Seam duplication (`:313-332, 384-393`, core `:1326-1327`). The UV map
  comes from the active layer: PS_UVMap for AUTO when it exists, the
  layer's `uv_map_name` for UV, and none for any other coordinate type,
  in which case no seam index is built and nothing says so. The index
  is built from `ps_object.data`, the original mesh rather than the
  evaluated one (core `:699-722`). In bake mode it still uses the
  active layer's UV map, not the channel's `bake_uv_map`.
- Progress. The callback calls `wm.progress_begin(0, total)` at the
  first stamp and `progress_update` after every stamp, skipped ones
  included, which drives the cursor counter; `progress_end` runs after
  (`:377-395`, core `:1359-1361`).
- Seed. With Use Random Seed on, NumPy's global generator is seeded
  with `random_seed + step` before each step's positions are drawn
  (core `:1254-1255`). Every step of every tile is planned this way
  before any stamp is drawn (core `:1313-1324`), so tiles of the same
  size get the same positions. Brush choice, colour jitter and
  rotation jitter are then drawn from the same global stream while
  stamping (core `:1340, 442-457, 1009-1011`). The colour jitter is
  drawn before the skip checks and the rotation after them (core
  `:991-1011`), so a stamp skipped for transparency or the threshold
  draws no rotation. With Random Rotation on, a change to the picture
  therefore changes the brush and jitter of every later stamp, but not
  where any stamp lands. With the toggle off, every run differs.

## v3 design

Decided on 2026-09-21. The painter is a filter layer kind, Painterly,
rather than a destructive operator: it repaints the stack below and
stays editable, and PS-057 already provides the derived image, the
freshness stamps, the auto refresh and the Update button. A destructive
action can follow on the same core, as Blur and Sharpen did.

### The build

A filter layer kind has so far been a list of full-target fragment
passes. The painter is not one: part of it is per-stamp work that
belongs on the CPU, and the stamps are geometry rather than a pass. So
`LayerFilterSpec` gains an optional `build` hook, a generator that
takes the composited stack below and returns the painted texture,
yielding between units so the build stays cancellable. A kind with no
hook runs its `passes_of` as before.

Inside the hook, in order:

1. **Encode and premultiply.** The composite arrives scene linear and
   straight. v2 painted into stored byte values, which for a painted
   layer are sRGB, and its look depends on that -- the blur, the luma
   the gradient is taken on, and the edge of every "over" blend. So the
   stack is encoded to sRGB first and decoded again at the end, as
   Invert and Sharpen already do with `encode`.
2. **Analysis.** The existing gaussian passes blur the colour the
   stamps sample. A Sobel pass over a blur of the picture's luma, as in
   v2, writes the gradient field (`gx`, `gy`, magnitude), and a short
   chain of max-reduction passes finds the field's peak, which v2
   normalises the magnitude by and the Edge Threshold is relative to.
3. **Gather.** The stamp centres are uploaded as a small data texture,
   and one pass reads the blurred colour and the gradient at each of
   them into a target of the same small size. That readback is the only
   one before the result: a few hundred kilobytes for thousands of
   stamps, where reading the field back whole would be 256 MB at 4K.
   The ticket first planned to read back a downsampled field and plan
   from that; gathering at the exact centres is both cheaper and
   faithful to v2, which samples one texel per stamp.
4. **Planning, on the CPU.** Numpy over the gathered arrays: the
   transparent-centre and threshold skips, the stroke angle, the HSV
   jitter and, in the seam slice, the mirrored duplicates. Everything
   here is per stamp rather than per texel, so it is small, and it is
   testable without a GPU.
5. **Stamping.** Each step's stamps are drawn in the order they were
   planned, as rotated quads with premultiplied "over" blending
   (`gpu.state.blend_set('ALPHA_PREMULT')`), up to 32768 to a draw so
   that no single draw stalls the driver. The brushes are packed into
   one atlas per step, resized on the CPU to that step's stamp size as
   v2 did: sampling a 1024-texel brush for a 60-texel stamp without
   mipmaps aliases, and the Python `gpu` module cannot build a mip
   chain. A stamp larger than its brush keeps the brush at its own size
   and magnifies it, as does one whose atlas would not fit the GPU's
   largest texture. One atlas rather than one
   texture per brush, so the draw keeps the planned order -- grouping
   stamps by brush would stack every stamp of one brush under every
   stamp of the next.

The texture `build` returns goes through the build's usual sRGB encode,
readback and commit, so everything downstream of the kind is unchanged.

### Determinism

A filter layer rebuilds whenever the stack below changes, so a painter
that reshuffled its strokes on every build would boil the whole picture
on every stroke painted under it. The seed is therefore a node property
and always used, and every random number a build needs -- positions,
brush choices, rotation jitter, colour jitter -- is drawn up front, per
step, before anything looks at the picture. A stamp that is skipped
still consumes its draws, so painting below changes the colour and the
angle of the stamps it reaches and never where any stamp lands.

### Where v3 differs from v2 on purpose

- **Stroke angle.** v2 holds its arrays top-down and rotates the brush
  counter-clockwise by the gradient angle measured in that y-down frame,
  which mirrors the brush against the gradient on diagonal edges: a 45°
  edge gets its stroke at 135°, while horizontal and vertical edges come
  out right. v3 aligns the brush with the gradient on every edge. The
  Rotation setting still turns every stroke by a fixed amount.
- **Colour jitter** is drawn per stamp from the seeded stream rather
  than from NumPy's global generator, so it repeats with the seed too.
- **Brush resize** box-filters before the bilinear step when a brush
  shrinks by a factor of two or more, where v2's bilinear alone skips texels.
  The stamp count is still computed from the resized brushes' covered
  area, v2's formula.
- **Smoothing** is in texels of a 2048 image and scales with the layer's
  resolution, where v2's sigma was in texels of whatever image it
  painted. Every other size here is already a fraction of the image, and
  a texel blur made raising the resolution repaint the picture: measured
  on a 4K test picture, stroke directions at 4096 were a median 25.6°
  from those at 2048, and 2.8° once the blur scales. At 2048, v2's usual
  size and a new layer's, the two are the same.

### Settings

Renamed on 2026-09-21, after the first slice was tried: "Largest Brush"
read as a choice of brush beside the Brush menu, and fractions of the
image on a 0-1 slider left the useful range in its first quarter.

| Setting | v2 | Stored as |
|---|---|---|
| Largest / Smallest Stroke | max / min brush scale | percent of the image |
| Passes | steps | count |
| First / Last Pass Opacity | start / end opacity | factor |
| Coverage | density | percent |
| Edge Threshold | gradient threshold | percent of the strongest edge |
| Smoothing | sigma | texels of a 2048 image |
| Rotation | (fixed at 0) | angle |
| Random Rotation | random rotation and its range | angle, 0 is off |

`plan.Settings` keeps v2's quantities and `Settings.of` converts. A
percentage becomes a single-precision fraction, as v2's properties were,
so that the stamp counts stay exact. Random Rotation is one angle rather
than v2's switch and range, since the layer's settings panel cannot hide
a range that does nothing while its switch is off; an angle of 0 paints
exactly what the switch off did. Coverage runs from 1 % to 200 %, past
v2's 10-100 %: a few loose strokes and denser paint are both looks worth
having, and stamps cost little. At 2048
the default settings paint the same pixels as before the rename.

### Brushes

The two v2 presets ship in the package with the default circle. Custom
brushes, in a later slice, are Blender images chosen on the layer
rather than paths on disk: a filter layer rebuilds on its own, and a
path that does not exist on the machine that opened the file would
lose the brush without saying so, where a packed image travels with
the .blend. Decided with the user on 2026-09-21.

### Seams

Seam pairs come from the evaluated mesh of an object stored on the
layer, filled in from the active object on first build. Auto refresh
then works whatever is selected, and the seam index can be keyed by the
surface content key `gpu_passes/surface.py` already computes, which is
also what lets a UV edit mark the layer out of date. Decided with the
user on 2026-09-21; see the next section for why this is load-bearing.

The stored object now exists on every filter layer (PS-057,
`surface_name`, shown as "Object", 2026-09-23). It is stored by name,
so a tree does not carry the mesh along when its material is appended
elsewhere, and the seam index should key on the resolved object rather
than on the name, which a rename changes. Add Layer and the
first build that needs a mesh fill it, and `filters.layer_plan.
surface_of` picks the mesh by the same rules the painter will need
(the stored Object if it is a mesh showing the tree, else the active
one, never a search of the file). The seams slice reuses it: a
Painterly layer always needs a mesh, so it resolves through
`surface_of` even when the UV names alone would not ask for one, and
refuses by name without one. Whether several meshes sharing one
material need their seams merged is left for that slice.

### Slices

1. Painterly as a filter layer kind, without seams: the hook, the
   passes, the planning, the presets.
2. Seams: the seam index from the surface arrays of the stored object,
   the duplicates, a refusal by name when the layer has no mesh to read.
3. Custom brushes as Blender images.
4. A UV or mesh edit on the seam object marks the layer out of date.

## Working across seams is a requirement, not a refinement

Seam duplication is the part to design around rather than the part to
add last.
A stamp is the size of many texels, so every stamp near a seam lands
half on a shell that continues somewhere else in the map. Without the
mirrored instance the stroke stops dead at the seam and the model shows
a hairline of unpainted texels along every one of them, which is the
failure the whole feature is judged on.

It is also the one part of the painter that no other GPU path here can
lend anything to. Every pass built so far samples by normalised
coordinate with clamp-to-edge and knows nothing about the mesh: the
filter layers, the composite and the texel map all treat the image as a
flat rectangle. Seam pairs have to come from the mesh, which means the
painter needs a resolved object and UV map before it can plan a single
stamp -- unlike a filter layer, which works on the image alone as long
as the stack below shares one map (`filters/layer_plan.py`). Now that
the painter is a filter layer kind, that precondition is the first
thing its build checks, and a layer with no mesh to read refuses by
name rather than painting without seams.

## Acceptance

- Same stamp count as v2 for the same image, brushes and settings: the
  count formula is v2's, so this is exact, not approximate. The
  distribution is similar; an exact match is not expected, because the
  random stream and the stroke angle differ on purpose.
- The same settings and the same stack build the same pixels, and
  painting below moves no stamp, only its colour and angle.
- 4K image with default settings completes in under 10 seconds.
- Seam duplication produces continuous strokes across a UV seam on the
  factory monkey. This one is load-bearing: a painter that stops at a
  seam is not shippable, however good it looks inside a shell.
