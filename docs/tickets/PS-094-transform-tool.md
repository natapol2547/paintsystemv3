# PS-094 Transform tool and pixel clipboard

Epic J. Size L. Milestone M3b.

## Goal

Move, scale, rotate and skew the selected content of the active image
layer with a live preview, in the 3D view and then the image editor.
Enter merges the result into the layer. Cut, copy and paste selected
content.

## Design: a floating layer the compiler draws

While a transform is open, nothing is written to the layer's pixels.
The moved content is a floating state of the tool session, and the
compiled material draws it. Dragging a handle changes an attribute the
material reads, not pixels, so the preview runs at shader speed on any
image size. Enter writes pixels once.

### Floating state

`tools/transform.py` keeps a `FloatingSession` in module memory while a
transform is open:

- `layer`: the layer being transformed, and `selection_ops`: the ops the
  mask was built from, frozen at the start.
- `space`: `UV` or `SURFACE` (below).
- `matrix`: a 3 x 3 affine in UV space, or for `SURFACE` the decal's
  world matrix.
- `interpolation`: `CLOSEST` or `LINEAR`.
- `source`: for pastes and surface moves, a write-once packed `Image`
  holding the content; `None` when the content is the layer's own image.
- `history`: the matrices before each drag or keyboard step.

The session is not document data (decided 2026-09-16 from PS-096). A
commit is a single image undo step (PS-090); if the floating state lived
in the document, undoing or redoing that step would leave the floating
content drawn over, or missing from, the merged pixels. With the session
outside the document, the document is identical before, during and after
a transform, so any order of undo and redo stays coherent.

- Ctrl+Z and Ctrl+Shift+Z inside the tool step through `history` (the
  tool's own keymap items, active only while floating), as Pixel Art
  Studio's transform box does.
- A Blender undo or redo while floating (from a menu or another editor)
  cancels the session in `undo_post` and `redo_post`; so does a file
  load. Esc cancels; nothing needs restoring.
- The matrix reaches the material through a view-layer attribute: the
  session writes it into a custom property on the scene (removed when
  the session ends) and the floating nodes read it with an Attribute
  node (`attribute_type='VIEW_LAYER'`). PS-096 spike 1: no update tag
  needed, 9–11 ms per frame at 50 layers, no node value changes and no
  compile.

### UV space (image editor, and 3D moves that stay in UV)

The image layer's emitter checks for a floating state on its layer. When
one exists, the layer's colour is composed inside the layer, before its
opacity, clipping and blend:

- `hole = layer * (1 - mask)`, the layer with the selection cut out.
- `moved = layer(inverse(matrix) * uv) * mask(inverse(matrix) * uv)`,
  the same image and mask sampled through the transform.
- `colour = over(hole, moved)`.

The mask is the selection's derived image (PS-091). This is exactly what
the commit writes, so the preview and the merge match by construction.
The floating nodes (the second sample of the layer and the mask, the
attribute node and the affine math) are added to the active layer's
artifact when the tool activates and removed when it deactivates: one
compile of 0.3 s at a few layers to 1.9 s at 50 (5.2, NVIDIA; up to 3 s
on 4.2), never during a drag. Switching the active layer with the tool
active repeats it for the new layer. Without a session the nodes pass
the layer through unchanged.

The image editor shows the material only through the 3D view, so its
preview is a draw handler drawing the same composition with the overlay
shader (PS-091) over the image.

### Surface space (3D view)

For moves that should follow the surface across seams:

- At the start, render the selected content as seen from the current
  view into a write-once packed `source` image through the texel map
  (PS-092). The decal stores the UV each of its pixels saw (RGBA32F, at
  twice the view's texel density, with a seam-edge guard that drops
  lookups straddling an island boundary), not colour, so the content is
  resampled once. Its world plane is the decal.
- The decal is compiled as a projected layer inside the source layer:
  object-space projection from the decal's matrix, a facing test on the
  normal and a depth slab around the plane, then the layer sampled at
  the decal's stored UV. PS-008's projection mapping supplies the
  coordinates.
- The handles are the same gizmo as in UV space, drawn on the decal
  plane in world space.
- Commit bakes the same projection, facing and depth slab into the layer
  through the texel map, so preview and commit share one definition of
  what the decal covers. PS-096 spike 6: with `CLOSEST` an unmoved decal
  reproduces the layer exactly; with `LINEAR` errors stay within 1/255
  away from seams. A colour decal (two resamples) blurred every texel,
  and float16 UV storage misses the texel at 4K.

### Handles

- `PAINT_SYSTEM_GT_transform`, a `bpy.types.Gizmo` subclass, one
  instance in a `GizmoGroup` per editor, bound to the session matrix.
  `GIZMO_GT_cage_2d` was tried in PS-096 spike 3 and rejected: its modal
  ignores Ctrl and Shift, rotation never triggers in the 3D view, and
  its corner hit zones in the image editor are a few pixels wide.
  - `draw` and `draw_select`: the box, corner and edge handles, the
    rotate handle above the top edge and the pivot. In the 3D view the
    box lies on the decal plane; in the image editor it is in region
    pixels from `region.view2d`.
  - `test_select` picks the handle nearest the cursor in region pixels
    with a generous radius, corners before edges, and returns -1 when
    nothing is near so clicks pass through.
  - `invoke` stores the matrix and cursor; `modal` reads the modifiers
    from the event: translate, scale (Shift keeps the aspect), rotate
    (Ctrl snaps to 15 degrees) and skew, pivot as the cage's centre
    (Ctrl drags it). `exit` pushes the drag onto the session history
    unless cancelled.
  - Drags push no Blender undo step; drag undo is the session history.
    (`Gizmo.use_undo` exists on 5.2 only and pushes memfile steps, which
    the session design avoids.)
- Keyboard: G, R, S inside the tool with typed values and axis locks,
  following Blender's transform conventions.
- The tool setting bar shows location, rotation, scale and interpolation
  as editable fields.

### Commit

- Enter, or clicking outside the cage, runs one GPU pass (PS-050) that
  evaluates the same composition into the layer image at its size and
  writes it with `write_pixels` (PS-090): one image undo step. Ctrl+Z
  after Enter restores the layer's pixels and leaves the transform
  closed. The commit operator has no `UNDO` option. Cost at 4K: pass and
  read back 100–300 ms, write 20–60 ms, registration 75–120 ms.
- Before the write, the selection gets a `TRANSFORM` op with the matrix
  as its own memfile step (`ed.undo_push`), so the ants follow the moved
  content: the first Ctrl+Z after Enter restores the pixels, the second
  puts the ants back. On 4.2 in texture paint mode the op is applied
  without a step (document changes are not undoable there, PS-090).
- A layer mask image (PS-015) under the layer is moved with the same
  matrix, written before the layer as its own image step (Blender's
  image undo takes one image per push from Python).
- Alt+drag at the start duplicates: the hole term is dropped.
- Refinement once the base works: Pixel Art Studio reopens its floating
  transform when the commit is undone. v3 can do the same for the most
  recent commit: the session keeps a resume record (matrix, ops, source,
  the written rectangle and a crop of the pixels before the write);
  `undo_post` reads the layer back (about 50 ms at 4K) and reopens the
  session when the crop matches, and `redo_post` closes it again when
  the crop matches the written result.

### Clipboard (`tools/clipboard.py`)

- `paint_system.pixels_copy` and `pixels_cut` copy the masked content
  into a numpy buffer in module memory with its origin rectangle. Cut
  also clears the texels with `write_pixels` (PS-090), one image step.
  The buffer does not survive a restart and is separate from the layer
  clipboard (PS-017).
- `pixels_paste` writes the buffer to a write-once packed `source` image
  and opens a floating session on the active layer at the same place.
  `pixels_paste_new_layer` adds an image layer first.

## Acceptance

- Move a selection by 10 texels and press Enter: the vacated texels are
  transparent, the content is offset exactly, one Ctrl+Z restores the
  original pixels and a second puts the selection back.
- Ctrl+Z while floating steps back one drag without touching Blender's
  undo stack. Undo, undo, redo, redo around a commit never shows floating
  content over merged pixels.
- Handles: Shift keeps the aspect on a corner drag, Ctrl snaps rotation
  to 15 degrees, and a corner drag in the image editor registers on the
  first try.
- Rotate by 90 degrees with `CLOSEST`: the pixels are a lossless
  permutation.
- While dragging on a 4K layer with 50 layers, no shader compilation job
  runs and the viewport stays interactive.
- The baked colour of the material before Enter matches the layer after
  Enter within 1/255 on every texel (preview equals merge).
- 3D: a surface move across a UV seam on the cube is continuous on the
  mesh after commit.
- Esc leaves the layer's pixels untouched.
- Cut, then paste as new layer: the new layer holds only the selected
  content at the same position.
