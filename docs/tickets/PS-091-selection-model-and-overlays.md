# PS-091 Selection mask model and overlays

Epic J. Size M. Milestone M3.

## Goal

A per-image texel selection that limits every scripted image tool
(filters, fill, transform, brush painter) and is shown in both the image
editor and the 3D view.

## Reference

Pixel Art Studio (`ps_canvas.py:425-439`, `ps_select.py:16-40, 828-862`,
`ps_overlay.py:380-416, 615-648, 789-858`):

- `cv.sel` is a numpy bool (H, W) array or `None`; an all-False mask
  normalises to `None`. `sel_version` keys caches; `combine()` does
  ADD / REMOVE / REPLACE.
- Outline segments come from a numpy XOR of the mask against its
  column- and row-shifted copies, computed only inside the bbox.
- 2D marching ants: segments split into alternating black/white groups
  by `int(time.time() * 6)`, redrawn by a 6 Hz timer only while a
  selection exists.
- 3D: a "wash" shader tints selected texels on the mesh by sampling a
  `GPUTexture` of the mask at the UV.

## v3 design

- `selection/model.py`:
  - `Selection` keyed by `(image_name, tile)`: `mask` numpy bool,
    `version`, cached `bbox`, cached `GPUTexture` (R8) rebuilt lazily
    when `version` changes.
  - `set(mask, mode='REPLACE'|'ADD'|'SUBTRACT'|'INTERSECT')`, `clear()`,
    `invert()`, `all()`. Every mutation pushes a selection-only entry
    through PS-090 so selection changes undo with Ctrl+Z.
  - `limit_texture()` returns the mask texture for GPU passes;
    `limit_array()` the numpy view for CPU passes. Tools treat `None`
    as "everything".
  - Selection is transient: not saved in the file, cleared on
    `load_post`, and dropped when the image is resized or deleted.
- `selection/overlay.py`:
  - One `SpaceImageEditor` `POST_PIXEL` handler draws ants for the
    image shown in that editor when it belongs to a Paint System layer.
    Segments are cached per `version`; ant phase from a timer that runs
    only while any selection exists.
  - One `SpaceView3D` `POST_VIEW` handler draws the wash: the active
    object's evaluated mesh with a `GPUShaderCreateInfo` shader that
    samples the mask at the active UV map and outputs a translucent
    tint, depth-tested `LESS_EQUAL` with depth writes off. Batch cached
    per mesh token (vertex count, UV layer name, object name) and
    dropped from `depsgraph_update_post`.
  - Both handlers restore `gpu.state` in a `finally`.
- Native brush strokes cannot be clipped by the selection (Blender's
  paint has no external stencil). Offer "Selection to Layer Mask" as
  the workaround: PS-015 mask image filled from the selection. A quick
  mask mode is a separate ticket.
- Preferences: ants colours, wash colour and opacity, "show selection in
  3D view" toggle.

## Acceptance

- Set a rect selection on a 4K image; ants appear in the image editor
  and the wash on the mesh within one redraw.
- Add, subtract and invert compose correctly; undo restores the previous
  mask.
- Apply a blur with a selection active: pixels outside the mask are
  bit-identical to before.
- With no selection, no timer runs and the overlays cost nothing.
