# PS-093 Selection tools

Epic J. Size M. Milestone M3b.

## Goal

Box, ellipse, lasso, polygon lasso and magic wand selection, select all,
none and invert, and select by face or UV island. The 3D view in texture
paint mode comes first; the image editor follows with the same operators.

## v3 design

### Tools and operators (`tools/`)

- A `tools` package: `__init__`, `shapes` (outline geometry),
  `preview` (the drag preview), `select_ops` (the operators) and
  `workspace_tools` (the `WorkSpaceTool` classes). The top-level
  `__init__.py` registers it after `keymaps`. `paint_system.select_all`
  already exists in `ops/selection_ops.py` (PS-091 milestone 1).
- `WorkSpaceTool` subclasses for `VIEW_3D` in `PAINT_TEXTURE` mode, then
  for `IMAGE_EDITOR` in `PAINT` and `VIEW` modes, so Blender's toolbar,
  tool settings header and keymap editor work as for any built-in tool:
  `paint_system.select_box`, `select_ellipse`, `select_lasso`,
  `select_polygon`, `select_wand`, with Blender's built-in select icons.
  Box is registered after `builtin_brush.mask` with a separator; 4.2
  falls back to the end of the toolbar. Unregistering resets active
  tools to the default brush first.
- Each operator only appends an op to the selection (PS-091), then calls
  `push_undo(context, bl_label)` and `selection.session.notify()`, and
  returns without GPU work. The session tick builds the mask and brings
  the stencil and overlays in step, so an operator stays a few dozen
  lines and every tool shares feather, antialias and undo.
- `bl_options = ops.selection_ops.UNDO_OPTIONS` plus `push_undo`, as
  PS-091's Undo section describes. Selection edits in texture paint mode
  are not undoable on 4.2, 4.5 and 5.0, not just 4.2; the alternative
  is a Ctrl+Z that does nothing.
  Outlines go in through `add_op(points=...)` or `set_points`, which
  store a flat float array. A list of pairs written straight to
  `op['points']` is malformed, and PS-091 reports the op as
  `UNSUPPORTED`.
- Mode keys match Blender's mesh select tools: Shift adds, Ctrl
  subtracts, Shift+Ctrl intersects, plain replaces, on a click-drag; a
  plain click runs `select_all` with `DESELECT`. The keymap is the
  addon's own tool keymap, and it binds no A, Alt+A or Ctrl+I: in texture
  paint those belong to Blender's face mask selection
  (`paint.face_select_all`), which a tool keymap would shadow, and
  Blender's own select tool keymaps bind no select-all keys either.
  `select_all` is reached from PS-091's Selection section, the tool
  header and F3 search.
- Tool settings in the header: feather (0 to 1024 pixels), antialias,
  and in the 3D view `through`, labelled "Through" ("Select faces hidden
  behind others too"). No mode row: the modifiers pick the mode, which
  is `SKIP_SAVE`, so a mode row would look live but do nothing on a
  drag.
- `invoke` needs `context.region_data`, and reports
  `session.label(...)` and cancels when `session.resolve_target` gives a
  reason. The start point comes from `mouse_prev_press_x/y`.
- While dragging, the shape is drawn in screen space by a `POST_PIXEL`
  handler as `LINES` with a per-segment tangent, through
  `overlay_shader.ANT_GLSL` with the colours and phase of
  `overlay.ant_style(context)`, so it crawls in step with the committed
  ants. The mask is built once on release.
- A shape that encloses nothing is cancelled, not stored: a box or
  ellipse with zero width or height, and a lasso with fewer than three
  distinct points or with every point on one line. With anti-alias or
  feather such a lasso would still draw a faint line.
- Selection operators build no mask. The session calls `get_mask`
  inside `try/except MaskUnavailable`, and the Selection section shows
  the reason. Tools that edit pixels through the selection (PS-094,
  PS-095) check `availability()` in `poll` or `invoke` and call
  `get_mask` the same way, reporting its message. An empty
  `availability()` is not a promise: it runs no self-test and starts no
  background GPU context, because `gpu.init()` crashes Blender instead of
  raising when EGL cannot start. `GPU_ERROR` is transient, so a later try
  can succeed; `SELF_TEST` lasts for the session, and `availability()`
  reports it from then on.
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
- `paint_system.select_all(action='SELECT'|'DESELECT'|'INVERT')` shipped
  with PS-091 milestone 1, with no keymap items (see above). `SELECT`
  appends `ALL` with `REPLACE`, `DESELECT` clears the ops, and `INVERT`
  calls `PaintSystemSelection.invert()`, which toggles a trailing
  `INVERT` and turns nothing and `ALL` into each other.

### Rasterising the new kinds

PS-091's rasteriser raises `UNSUPPORTED` for `FACES`, `RASTER` and
outlined `VIEW` ops. This ticket adds their passes to
`selection/raster.py` and moves the kinds into `SUPPORTED_KINDS`.
PS-091's self-test does not reach these passes, so it cannot catch a
driver that draws them wrongly.

- `FACES` stores the object, the UV map name and the selected face
  indices, and draws the UV triangles of the evaluated mesh, like the
  texel map.
- `VIEW` ops store the `object` pointer and the resolved `uv_map` name
  along with the region size and matrices. A pointer survives rename,
  undo and reload. A missing object, a non-mesh or a missing UV map makes
  the mask unavailable with reason `SURFACE` ("The object or UV map this
  selection was drawn on is gone"), with no fallback to another map,
  which would change the selection without telling the user; a region
  size of 0 or less is `VIEW`. `texel_map` gains
  `fallback_to_active=False` for these ops. Feather for `VIEW` ops is in
  region pixels.
- `FACES` and `VIEW` digests include a 16-byte content fingerprint of
  the surface from `gpu_passes/surface.py`, not an epoch: a stroke undo
  also reports geometry updates, so an epoch would rebuild every `VIEW`
  mask after each stroke undo. The depsgraph handler marks an object
  suspect next to `texel_map.invalidate`, `undo_post` marks every object
  suspect, and a file read forgets the keys; the session tick resolves a
  suspect key (`resolve_key`) and the overlay's draw only peeks
  (`peek_key`). The fingerprint costs 178–262 ms at 1M triangles on the
  first build after an undo or mode switch. Hashing the UVs alone costs
  47–62 ms at 4M loops. `session_uid` is never hashed.
- `VIEW` shapes are built in three stages. A: the shape and lasso
  shaders, compiled again with `SIGNED_DISTANCE`, write a screen signed
  distance at region size (`R32F`). B, skipped with `through`: a depth
  pass of the surface stores `q` (`-1/d` in perspective, `d` in
  orthographic), slope and depth. C: a banded pass at mask size projects
  each texel-map position (PS-092), reads the shape with a manual
  bilinear read, tests facing against the smooth normal and depth with
  4-tap PCF, skips depth for margin texels, and combines like `UV` ops.
  Two `MAT4` alone fill the 128 bytes of push constants Vulkan
  guarantees, so the matrices go through a std140 uniform buffer
  (`PSViewBlock`, 176 bytes) and push constants keep the ints.
- `RASTER` images are float, written with alpha 1 and packed right
  after the write: a packed float image with alpha 0.5 came back
  premultiplied after an undo past its creation and a redo. They are
  uploaded through `foreach_get` into a `Buffer`-backed texture, never
  `gpu.texture.from_image`, which segfaults a background 5.2. Their
  `session_uid` survives undo and redo and is part of the digest.

### Image editor clipping

Blender's 2D painting ignores the Stencil Mask, so PS-091 milestone 1
only shows a header note there. The image editor tools own the
fallback: keep a GPU copy of the layer image, and after each stroke
(image update seen in `depsgraph_update_post`) write
`mix(copy, image, mask)` back in one pass and refresh the copy. Paint
outside the selection shows until the stroke ends.

The tools must not ship before the `VIEW` rasteriser lands: until then a
`VIEW` op is `UNSUPPORTED`, and PS-091 blocks painting for a selection
it cannot use.

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
