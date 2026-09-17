# PS-097 More selection tools (deferred)

Epic J. Size L. Nice to have, not scheduled.

## Status

Not started. Split out of PS-093 on 2026-09-17, when Rectangle, Ellipse
and Lasso Selection shipped in the 3D view. These tools would be nice to
have, but nothing else waits on them. The design below was written for
PS-093 and has not been checked against the code since.

PS-095's fill floods in screen space the same way the magic wand does,
so whichever of the two is built first builds that flood for both.

## Goal

The selection tools PS-093 did not build:

- a polygon lasso,
- a magic wand,
- select by face, UV island or material,
- the selection tools in the image editor, and clipping painting to the
  selection there.

## v3 design

### Toolbar

`select_polygon` and `select_wand` join PS-093's tool group in the 3D
view, and `IMAGE_EDITOR` tools follow in `PAINT` and `VIEW` modes with
the same operators. They keep PS-093's keymap rules: the modifiers pick
the mode, and no A, Alt+A or Ctrl+I items. They are added after Lasso
Selection in `workspace_tools.TOOLS`: its first tool is the one the
group shows by default, and the toolbar tests check that it is Lasso.

### Polygon lasso

Closes on Enter, double click or a click on the first point; Backspace
removes the last point.

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

### Faces

`paint_system.select_faces(mode='SELECTED'|'ISLAND'|'MATERIAL')` adds a
`FACES` op from the mesh's face selection, the UV island under the
cursor, or the faces of the active material slot. It reuses
`surface.resolve_key` and the texel map's triangle arrays, with the same
digest provider as `VIEW` ops.

### Rasterising `FACES` and `RASTER` ops

`FACES` and `RASTER` ops still raise `UNSUPPORTED` (`TRANSFORM` ops
belong to PS-094). PS-091's self-test does not reach their passes, so it
cannot catch a driver that draws them wrongly.

- `FACES` stores the object, the UV map name and the selected face
  indices, and draws the UV triangles of the evaluated mesh, like the
  texel map.
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

## Acceptance

- Wand with tolerance 0 on a flat colour region selects exactly that
  region; a higher tolerance grows it to near colours.
- Select by UV island selects every texel of the island and nothing else.
- The image editor tools pass PS-093's checks in texel space.
- A stroke in the image editor across the selection edge leaves the
  pixels outside the selection as they were once the stroke ends.
