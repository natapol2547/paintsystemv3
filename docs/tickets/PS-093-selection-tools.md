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
- Mode keys match Blender's mesh select tools: Shift adds, Ctrl
  subtracts, Shift+Ctrl intersects, plain replaces. The keymap is the
  addon's own tool keymap.
- Tool settings in the header: mode, feather, antialias, and in the 3D
  view `through` (select hidden texels too, like X-ray).
- While dragging, the shape is drawn in screen space by a `POST_PIXEL`
  handler with the same dashed shader as the ants. The mask is built once
  on release.
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
- The result is written once to a greyscale `Image` and stored as a
  `RASTER` op, so undo and reload need no pixel history.
- The wand selects visible surface only. Texels hidden from the view are
  not reached; the tool settings say so.

### Faces and all

- `paint_system.select_faces(mode='SELECTED'|'ISLAND'|'MATERIAL')` adds a
  `FACES` op from the mesh's face selection, the UV island under the
  cursor, or the faces of the active material slot.
- `paint_system.select_all(action='SELECT'|'DESELECT'|'INVERT')`, with
  the addon's keymap using Blender's defaults (A, Alt+A, Ctrl+I) inside the
  selection tools only, so texture paint shortcuts elsewhere are unchanged.

## Acceptance

- Each tool works in the 3D view; Shift adds, Ctrl subtracts, Shift+Ctrl
  intersects.
- A lasso around a cube corner selects the matching texels on all three
  visible faces across two UV islands, and none on the faces behind.
- With `through` on, the same lasso also selects the hidden faces.
- Wand with tolerance 0 on a flat colour region selects exactly that
  region; a higher tolerance grows it to near colours.
- Select by UV island selects every texel of the island and nothing else.
- A 200-point lasso on a 4K image is ready in under 50 ms after release.
- The image editor tools pass the same checks in texel space.
