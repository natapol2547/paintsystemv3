# PS-091 Selection model and overlays

Epic J. Size L. Milestone M3b.

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
  - `RASTER` points at a write-once greyscale `Image` (magic wand and
    other pixel-derived results). It is never modified, so undo only
    needs the pointer. It is packed right after the write: PS-096 found
    that an unpacked generated image comes back black after an undo past
    its creation and a redo.
  - `TRANSFORM` carries the matrix of a committed move (PS-094) so the
    selection follows the content without copying pixels.
- Because the ops are document data, selection undo is Blender's undo,
  and the selection is saved with the file like mesh selection. An op
  that replaces everything drops the ops before it. Memfile undo never
  restores pixels (PS-096), so the derived mask image carries a hash of
  the ops it was built from in an ID property, and `undo_post` and
  `redo_post` rebuild it when the hash no longer matches the ops. On 4.2
  in texture paint mode document changes are not undoable (PS-090), so
  selection undo does nothing there; the docs say so.

## Rasterisation (`selection/raster.py`)

GPU passes (PS-050 framework) build the mask at the image's size.

- Every op renders to its own coverage texture, combined in order with
  soft set operations: add is `max`, subtract is `min(m, 1 - op)`,
  intersect is `min`. Op textures are cached by a hash of the op, so
  adding an op only runs that op and the combine.
- Box and ellipse are analytic distance fields. Lasso and polygon are
  filled by accumulating winding numbers from a triangle fan, and their
  distance to the outline comes from a jump flooding pass seeded on the
  outline texels. `antialias` smooths the edge over one pixel; `feather`
  maps the distance through a smoothstep of that radius.
- `UV` ops rasterise in texel space. `VIEW` ops rasterise the shape in
  screen space at region size (feather measured in screen pixels, as the
  user drew it), then reach texels through the texel map (PS-092): each
  texel is projected with the stored matrices, sampled from the screen
  shape, and kept only when it faces the view and passes the depth test
  unless `through` is set.
- `FACES` draws the UV triangles of the mesh's selected faces
  (`use_paint_mask` face selection) in texel space.
- The combined mask is kept as a `GPUTexture` for passes and overlays and
  written to a derived float `Image` (`.PS Selection <tree>`, no fake
  user) when a tool finishes and the stencil below is in use. PS-096
  spike 4 measured that write at about 150–200 ms at 4K (read back,
  expansion to RGBA, `foreach_set`), which is why it happens only for the
  stencil and not on every rebuild.

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
- 3D view (`POST_VIEW`): the evaluated mesh of the paint object, sampling
  the mask at the layer's UV map, depth-tested `LESS_EQUAL` without depth
  writes. The batch is cached per mesh and dropped in
  `depsgraph_update_post`.
- Image editor (`POST_PIXEL`): a quad over the displayed image, mapped
  with `region.view2d`.
- A timer redraws the ants at 8 Hz only while a selection exists.
- Handlers restore `gpu.state` in a `finally`.
- Preferences: wash colour and opacity, ant colours, show in the 3D view.

## Acceptance

- A lasso on a 4K image shows the wash and ants in both editors within
  one redraw after release.
- Add, subtract, intersect and invert combine as the soft set operations
  above; undo restores the previous mask exactly.
- A feathered box has mask values rising from 0 to 1 over the feather
  width.
- A native stroke across the selection edge in the 3D view leaves texels
  with mask 0 unchanged.
- With no selection, no timer runs and the draw handlers return at once.
- Reopening the file rebuilds the same mask from the saved ops.
