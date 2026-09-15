# PS-094 Transform tool and pixel clipboard

Epic J. Size L. Milestone M3b.

## Goal

Move, scale, rotate and skew the selected content of the active image
layer with a live preview, in the 3D view and then the image editor.
Enter merges the result into the layer. Cut, copy and paste selected
content.

## Design: a floating layer the compiler draws

While a transform is open, nothing is written to the layer's pixels.
The moved content is a floating state in the document, and the compiled
material draws it. Dragging a handle changes node values, not pixels, so
the preview runs at shader speed on any image size. Enter writes pixels
once.

### Floating state

`PaintSystemFloating` on the tree, set while a transform is open:

- `layer`: the layer being transformed, and `selection_ops`: the ops the
  mask was built from, frozen at the start.
- `space`: `UV` or `SURFACE` (below).
- `matrix`: a 3 x 3 affine in UV space, or for `SURFACE` the decal's
  world matrix.
- `interpolation`: `CLOSEST` or `LINEAR` (Cubic added if spike tests show
  it is needed).
- `source`: for pastes and surface moves, a write-once `Image` holding
  the content; `None` when the content is the layer's own image.

Because it is document data, Ctrl+Z while floating undoes the last handle
tweak through memfile undo. Esc clears the state; nothing needs restoring.

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
Handle drags set Mapping or Vector Math values only and use the value-only
compile path (PS-081). Opening and closing a transform adds and removes
nodes, a one-off recompile that PS-096 spike 1 measures.

The image editor shows the material only through the 3D view, so its
preview is a draw handler drawing the same composition with the overlay
shader (PS-091) over the image.

### Surface space (3D view)

For moves that should follow the surface across seams:

- At the start, render the selected content from the current view into a
  write-once `source` image through the texel map (PS-092). Its world
  plane is the decal.
- The decal is compiled as a projected layer inside the source layer:
  object-space projection from the decal's matrix, a facing test on the
  normal and a depth slab around the plane. PS-008's projection mapping
  supplies the coordinates.
- The handles are a world-space `GIZMO_GT_cage_2d` on the decal plane
  (PS-096 spike 3), like Blender's area light gizmo.
- Commit bakes the same projection, facing and depth slab into the layer
  through the texel map, so preview and commit share one definition of
  what the decal covers. Spike 6 decides between resampling the decal
  image and storing UV lookups.

### Handles

- `GizmoGroup` per editor with a `GIZMO_GT_cage_2d` bound to the floating
  matrix: translate, scale (Shift keeps the aspect), rotate and skew, pivot
  as the cage's centre (Ctrl drags it).
- Keyboard: G, R, S inside the tool with typed values and axis locks,
  following Blender's transform conventions.
- The tool setting bar shows location, rotation, scale and interpolation
  as editable fields.

### Commit

- Enter, or clicking outside the cage, runs one GPU pass (PS-050) that
  evaluates the same composition into the layer image at its size and
  writes pixels inside `record()` (PS-090). One undo entry.
- A layer mask image (PS-015) under the layer is moved with the same
  matrix in the same step.
- The selection gets a `TRANSFORM` op with the matrix, so the ants follow
  the moved content.
- Alt+drag at the start duplicates: the hole term is dropped.

### Clipboard (`tools/clipboard.py`)

- `paint_system.pixels_copy` and `pixels_cut` copy the masked content
  into a numpy buffer in module memory with its origin rectangle. Cut
  also clears the texels inside a PS-090 entry. The buffer does not
  survive a restart and is separate from the layer clipboard (PS-017).
- `pixels_paste` writes the buffer to a write-once `source` image and
  opens a floating state on the active layer at the same place.
  `pixels_paste_new_layer` adds an image layer first.

## Acceptance

- Move a selection by 10 texels and press Enter: the vacated texels are
  transparent, the content is offset exactly, and one undo restores the
  original.
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
