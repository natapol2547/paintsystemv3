# PS-093 Selection tools

Epic J. Size M. Milestone M3b.

## Goal

Box, ellipse, lasso, polygon lasso and magic wand selection, select all,
none and invert, and select by face or UV island. The 3D view in texture
paint mode comes first; the image editor follows with the same operators.

## v3 design

### Tools and operators (`tools/select.py`)

- `WorkSpaceTool` subclasses for `VIEW_3D` in `PAINT_TEXTURE` mode, then
  for `IMAGE_EDITOR` in `PAINT` and `VIEW` modes, so Blender's toolbar,
  tool settings header and keymap editor work as for any built-in tool:
  `paint_system.select_box`, `select_ellipse`, `select_lasso`,
  `select_polygon`, `select_wand`.
- Each operator only appends an op to the selection (PS-091) and asks
  for a rebuild. The rasteriser does the rest, so an operator stays a
  few dozen lines and every tool shares feather, antialias and undo.
  Outlines go in through `add_op(points=...)` or `set_points`, which
  store a flat float array. A list of pairs written straight to
  `op['points']` is malformed, and PS-091 reports the op as
  `UNSUPPORTED`.
- Mode keys match Blender's mesh select tools: Shift adds, Ctrl
  subtracts, Shift+Ctrl intersects, plain replaces. The keymap is the
  addon's own tool keymap.
- Tool settings in the header: mode, feather (0 to 1024 pixels),
  antialias, and in the 3D view `through` (select hidden texels too,
  like X-ray).
- While dragging, the shape is drawn in screen space by a `POST_PIXEL`
  handler with the same dashed shader as the ants. The mask is built once
  on release.
- A shape that encloses nothing is cancelled, not stored: a box or
  ellipse with zero width or height, and a lasso with fewer than three
  distinct points or with every point on one line. With anti-alias or
  feather such a lasso would still draw a faint line.
- Operators check `availability()` in `poll` or `invoke` and call
  `get_mask` inside `try/except MaskUnavailable`, reporting its message.
  An empty `availability()` is not a promise: it runs no self-test and
  starts no background GPU context, because `gpu.init()` crashes Blender
  instead of raising when EGL cannot start. `GPU_ERROR` is transient, so
  a later try can succeed; `SELF_TEST` lasts for the session, and
  `availability()` reports it from then on.
- Box and ellipse accept Shift after the drag starts for a square or
  circle and Alt for drawing from the centre, the way Blender's add
  object tool does. The polygon lasso closes on Enter, double click or a
  click on the first point; Backspace removes the last point.

### Magic wand

The wand flood runs where the user sees the colours, not in UV space.
That keeps it off seams and islands entirely.

- 3D view: render the source (the active layer, or the composite of its
  channel when "Sample Merged" is on) into a region-size texture through
  the texel map (PS-092), flood from the clicked pixel in screen space,
  and turn the region into a `VIEW` op. Refinement: texels near the
  projected edge are tested again in texel space against the seed colour,
  so the edge follows the image resolution rather than the screen's.
- Image editor: the same flood on the image in texel space.
- Colour distance is Euclidean in OKLab with alpha as a fourth axis, so a
  tolerance means the same visual step for dark and light colours. Fully
  transparent texels match a transparent seed.
- Contiguous or global. The first version floods on the CPU with numpy
  over a GPU match mask; a jump flooding connectivity pass replaces it if
  profiling asks for it (PS-081).
- The result is written once to a greyscale `Image`, packed (PS-091),
  and stored as a `RASTER` op, so undo and reload need no pixel history.
- The wand selects visible surface only. Texels hidden from the view are
  not reached; the tool settings say so.

### Faces and all

- `paint_system.select_faces(mode='SELECTED'|'ISLAND'|'MATERIAL')` adds a
  `FACES` op from the mesh's face selection, the UV island under the
  cursor, or the faces of the active material slot.
- `paint_system.select_all(action='SELECT'|'DESELECT'|'INVERT')`, with
  the addon's keymap using Blender's defaults (A, Alt+A, Ctrl+I) inside the
  selection tools only, so texture paint shortcuts elsewhere are unchanged.
  `SELECT` appends `ALL` with `REPLACE`, `DESELECT` clears the ops, and
  `INVERT` appends an `INVERT` op, which PS-091 stores with mode `ADD`.

### Rasterising the new kinds

PS-091's rasteriser raises `UNSUPPORTED` for `FACES`, `RASTER` and
outlined `VIEW` ops. This ticket adds their passes to
`selection/raster.py` and moves the kinds into `SUPPORTED_KINDS`.
PS-091's self-test does not reach these passes, so it cannot catch a
driver that draws them wrongly.

- `FACES` stores the object, the UV map name and the selected face
  indices, and draws the UV triangles of the evaluated mesh, like the
  texel map.
- `FACES` and `VIEW` digests include a per-object epoch that
  `depsgraph_update_post` bumps on a geometry update of the mesh or a
  transform update of the object, and that `undo_post` and `redo_post`
  bump for every object. Hashing the UVs instead costs 47–62 ms at 4M
  loops. On 4.2 in texture paint mode undo fires only `undo_post`.
- `VIEW` shapes are built by the same passes in screen space at region
  size, then sampled at each texel's projected position through the
  texel map (PS-092), as PS-091 describes. Two `MAT4` alone fill the
  128 bytes of push constants Vulkan guarantees, so the matrices go
  through a uniform buffer or are applied in numpy.
- `RASTER` images are float, written with alpha 1 and packed right
  after the write: a packed float image with alpha 0.5 came back
  premultiplied after an undo past its creation and a redo. They are
  uploaded through `foreach_get` into a `Buffer`-backed texture, never
  `gpu.texture.from_image`, which segfaults a background 5.2. Their
  `session_uid` survives undo and redo and is part of the digest.

## Acceptance

- Each tool works in the 3D view; Shift adds, Ctrl subtracts, Shift+Ctrl
  intersects.
- A lasso around a cube corner selects the matching texels on all three
  visible faces across two UV islands, and none on the faces behind.
- With `through` on, the same lasso also selects the hidden faces.
- Wand with tolerance 0 on a flat colour region selects exactly that
  region; a higher tolerance grows it to near colours.
- Select by UV island selects every texel of the island and nothing else.
- A 200-point lasso on a 4K image is ready in under 50 ms after release
  on a hardware GPU, not counting the first build of a session (shader
  compile and self-test). CI runs llvmpipe and asserts only 2 s.
- The image editor tools pass the same checks in texel space.
