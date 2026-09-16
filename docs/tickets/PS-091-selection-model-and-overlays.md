# PS-091 Selection model and overlays

Epic J. Size L. Milestone M3b.

## Status

Built in slices, since the four halves of this ticket are independent.

1. **The document model. Done.** `props/selection.py` and
   `tests/test_selection_model.py`; `PaintSystemNodeTree.selection` holds
   it. 33 checks on 5.2.1 and 4.2.23 (56 after slice 2). Outlines turned
   out to want an ID property rather than a collection of typed point
   groups: a lasso carries hundreds of points and memfile undo copies
   every step, so a PropertyGroup per point would be copied on every
   push. Slice 2 replaced `ops_hash`'s rounded JSON with exact binary
   digests and removed `mask_hash`, which went stale after undo and
   reload.
2. **Rasterisation. Done.** `selection/outline.py`, `selection/raster.py`,
   `tests/test_selection_outline.py` and `tests/test_selection_raster.py`.
   One `R32F` pass per op evaluates coverage at each texel centre in
   closed form: box and ellipse analytically (Eberly's distance for the
   ellipse), lasso by even-odd parity and per-cell nearest-segment lists
   that numpy builds per outline. Neither the winding-number fan nor the
   jump flood was needed. Masks are cached under a digest of the ops, so
   undo and redo run no pass while the earlier mask is still cached. 148
   checks on 5.2.1 (Intel GL, headless and windowed), 4.2.23 windowed and
   5.3 alpha (NVIDIA Vulkan), and 149 on Vulkan through lavapipe on 5.2.1
   and 5.3 alpha, where one more check fails the run if Blender fell back
   to OpenGL. CI runs that lavapipe step from 5.2, because the OpenGL
   jobs miss Vulkan-only faults such as an `R32F` texture read back as
   bytes. `FACES`, `RASTER`, `TRANSFORM` and outlined `VIEW` ops raise
   `UNSUPPORTED` until PS-093 and PS-094 add them. `gpu.init()` turned
   out to arrive in 5.2, not 5.0, and to crash Blender instead of raising
   when EGL cannot start.
3. **Clipping native strokes** through the brush stencil.
4. **Overlays**: the wash and the marching ants, in both editors.

## Goal

A soft selection on the active layer's image that limits scripted image
tools (transform, fill, filters) and clips native brush strokes, shown
in the 3D view and the image editor.

## Model: the selection is compiled, like the material

The selection is document data; its mask is derived from it, the same
way the artifact is derived from the tree.

- `PaintSystemSelection` on the tree: `image` (the layer image it
  applies to), `feather` and `antialias` defaults, and `ops`, an
  ordered collection of operations:
  - `kind`: `BOX`, `ELLIPSE`, `LASSO`, `FACES`, `RASTER`, `TRANSFORM`,
    `INVERT`, `ALL`.
  - `mode`: `REPLACE`, `ADD`, `SUBTRACT`, `INTERSECT`.
  - `space`: `UV` (image editor) or `VIEW` (3D view).
  - Shape data: `points` (region or UV coordinates), and for `VIEW` the
    region size and the view and projection matrices at the time, so the
    op can be rasterised again after undo or reload.
  - `feather` in pixels, `antialias`, `through` (ignore occlusion, like
    X-ray in mesh selection).
  - `feather` is limited to 1024 pixels. `INVERT` and `TRANSFORM` act on
    the mask before them, so their mode means nothing and `add_op` stores
    `ADD`.
  - `RASTER` points at a write-once greyscale `Image` (magic wand and
    other pixel-derived results). It is never modified, so undo only
    needs the pointer. It is packed right after the write: PS-096 found
    that an unpacked generated image comes back black after an undo past
    its creation and a redo.
  - `TRANSFORM` carries the matrix of a committed move (PS-094) so the
    selection follows the content without copying pixels.
- Because the ops are document data, selection undo is Blender's undo,
  and the selection is saved with the file like mesh selection. A
  `REPLACE` of a box, ellipse, lasso, faces, raster or all op drops the
  ops before it. The mask is cached under a digest of the ops, the size
  and the UDIM tile, so after undo, redo or a reload the restored ops
  find their mask or build it, and no stored hash can go stale. Memfile
  undo never restores pixels (PS-096), so the stencil image written from
  the mask (slice 3) is tracked by `session_uid` outside the file and
  rewritten when its recorded digest no longer matches the ops. On 4.2
  in texture paint mode document changes are not undoable (PS-090), so
  selection undo does nothing there; the docs say so.

## Rasterisation (`selection/raster.py`)

GPU passes build the mask at the image's size, or at a UDIM tile's.

- One full-screen pass per op evaluates the op's coverage `c` at every
  texel centre and combines it with the previous mask `m` in the same
  draw: replace writes `c`, add `max(m, c)`, subtract `min(m, 1 - c)`,
  intersect `min(m, c)`, invert `1 - m`. Masks are `R32F` textures cached
  by content under a 512 MiB budget, so appending an op runs one pass, and
  undo, redo or removing the last op run none while the earlier mask is
  still cached.
- Coverage comes from a signed distance `s` in texels: `s > 0` for a hard
  edge, else the smoothstep of `(s + hw) / (2 hw)` with
  `hw = max(feather, antialias ? 1 : 0) / 2`, so a feather rises from 0
  to 1 over its width, centred on the outline. Box and ellipse distances
  are analytic. Lasso and polygon are filled by even-odd parity from
  per-row crossing tables and measure distance through per-cell segment
  lists. With a hard edge a box or ellipse leaves out texel centres that
  lie exactly on its outline, while the lasso's half-open even-odd rule
  counts a centre on a right or bottom edge but not on a left or top one.
  The same rectangle as a box and as a lasso can therefore differ by a
  column and a row when its edges pass through texel centres.
- Masks match the same formulas evaluated in float64 on the CPU
  (`tests/selection_reference.py`). Hard edges agree exactly on every
  texel further than 1e-3 from the outline. Soft edges agree within 1e-5
  for boxes and lassos, and for an ellipse within `max(1e-4, 6e-7 r)`,
  with `r` its longer radius in texels: the float32 root is off by about
  `3e-7 r` in distance, and which texels come closest to the bound
  differs between backends. The soft bounds hold from a half width of
  0.25 texels (anti-alias on, or a feather of 0.5 or more); below that
  they grow as `0.25 / hw`, because the slope `1.5 / (2 hw)` amplifies
  float32 distance error.
- `UV` ops rasterise in texel space. `VIEW` ops rasterise the shape in
  screen space at region size (feather measured in screen pixels, as the
  user drew it), then reach texels through the texel map (PS-092): each
  texel is projected with the stored matrices, sampled from the screen
  shape, and kept only when it faces the view and passes the depth test
  unless `through` is set.
- `FACES` draws the UV triangles of the mesh's selected faces
  (`use_paint_mask` face selection) in texel space.
- Until PS-093 and PS-094 add them, `FACES`, `RASTER`, `TRANSFORM` and
  outlined `VIEW` ops make `get_mask` raise `MaskUnavailable` with reason
  `UNSUPPORTED`; a limited edit never runs as if there were no selection.
  The digests already include the `RASTER` image's `session_uid` and the
  `TRANSFORM` matrix; the per-object epoch `FACES` and `VIEW` need comes
  with PS-093.
- Any other mask that cannot be built raises `MaskUnavailable` too, with
  a reason and a message fit for the UI. An op whose `points` ID property
  is not a flat array of even length, such as a list of pairs written to
  it directly, is `UNSUPPORTED` at that op with "A selection operation
  has malformed points", and `peek_mask` returns None for it rather than
  raising. `GPU_ERROR` means the GPU raised during the build, as it does
  with no active context in the tick of a file load on 5.3 alpha. It is
  transient and not remembered, so the next call tries again.
- `availability()` tells a `poll` or a draw callback whether `get_mask`
  can build, without building, running the self-test or starting a
  background GPU context. On 5.2.1 and 5.3 alpha `gpu.init()` crashes
  Blender instead of raising when EGL has no usable driver, so only a
  path about to draw may call it. An empty answer is therefore not a
  promise: an untested GPU or an unstarted background context reports
  available.
- The first build of a session runs a self-test: two chains, at 64 x 64
  and 140 x 64, compared at chosen texels with float64 values from
  `tests/selection_reference.py`. They check the soft and hard edge
  profiles, all four modes, `ALL` and `INVERT`, an add of two fractional
  values, the ellipse root, lasso distance tables in 32-texel cells, and
  lasso parity across three span columns and two rows of the key
  texture. A GPU that fails gets `SELF_TEST` for the rest of the session
  instead of wrong masks. A hard ellipse, cells wider than 32 texels,
  distance tables past one data texture row and tiles other than 1001
  are not checked.
- The mask stays a `GPUTexture` for passes and overlays. Slice 3 writes
  it to the derived float `Image` (`.PS Selection <tree>`, no fake user)
  from a timer, only while the stencil is in use, through
  `SelectionMask.read_bytes`: an `R8` pass and a UBYTE read, 7–24 ms at
  4K. A UBYTE read of the `R32F` texture itself returns zeros on Vulkan.
  PS-096 spike 4 measured the whole write at about 150–200 ms at 4K
  (read back, expansion to RGBA, `foreach_set`), which is why it happens
  only for the stencil and not on every rebuild.

## Clipping native strokes

- 3D view: while a selection exists in texture paint mode, the derived
  mask image is the Stencil Mask (`use_stencil_layer`, `stencil_image`,
  `mesh.uv_layer_stencil` set to the layer's UV map, and
  `invert_stencil=True`, because Blender's stencil protects where the
  mask is white). Every Blender brush is clipped with soft edges: PS-096
  spike 2 measured texels at mask 0 unchanged and the painted strength
  following the mask within 0.01, on 5.2 and 4.2. Clearing the selection
  restores the previous stencil settings. `sync_canvas` leaves them
  alone.
- Image editor: Blender's 2D painting has no stencil. The fallback keeps a
  GPU copy of the layer image; after each stroke (image update seen in
  `depsgraph_update_post`) one pass writes `mix(copy, image, mask)` back
  and refreshes the copy. Paint outside the selection shows until the
  stroke ends. Decided when the image editor tools are built.

## Overlays (`selection/overlay.py`)

Draw handlers only, so showing or hiding a selection never changes the
material and never recompiles a shader.

- One fragment shader draws the selection in both editors: a light wash
  inside, and marching ants along the 0.5 iso-line of the mask, found
  with screen-space derivatives and dashed by screen position and a time
  uniform. Soft masks and any zoom level work without extracting
  outline segments.
- 3D view (`POST_VIEW`): the evaluated mesh of the paint object,
  sampling the mask at the layer's UV map with `texelFetch` and bilinear
  filtering in the shader (linear filtering of `R32F` is an optional
  device feature on Vulkan and Metal), depth-tested `LESS_EQUAL` without
  depth writes. The batch is cached per mesh and dropped in
  `depsgraph_update_post`.
- Image editor (`POST_PIXEL`): a quad over the displayed image, mapped
  with `region.view2d`.
- A timer redraws the ants at 8 Hz only while a selection exists.
- Handlers restore `gpu.state` in a `finally`.
- Handlers draw what `raster.peek_mask` returns and never build. When it
  returns None and `availability()` is empty, a one-shot timer calls
  `get_mask` and tags the region for a redraw.
- Preferences: wash colour and opacity, ant colours, show in the 3D view.

## Acceptance

- A lasso on a 4K image shows the wash and ants in both editors within
  one redraw after release.
- Add, subtract, intersect and invert combine as the soft set operations
  above; undo and redo restore the previous mask exactly, without a pass
  when it is cached.
- A feathered box has mask values rising from 0 to 1 over the feather
  width.
- A native stroke across the selection edge in the 3D view leaves texels
  with mask 0 unchanged.
- With no selection, no timer runs and the draw handlers return at once.
- Reopening the file rebuilds the same mask from the saved ops.
- A mask that cannot be built, whether for an op not supported yet,
  malformed points or a GPU error, makes `get_mask` raise
  `MaskUnavailable` with a message; nothing edits the image as if there
  were no selection.
