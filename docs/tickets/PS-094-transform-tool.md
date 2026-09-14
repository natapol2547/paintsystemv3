# PS-094 Transform and move tool, pixel clipboard

Epic J. Size L. Milestone M3.

## Goal

Move, scale, rotate and skew the selected texels of the active image
layer with live preview, in the image editor and the 3D view. Cut, copy
and paste selected texels.

## Reference

Pixel Art Studio (`ps_xform.py`, `ps_transform.py`, `ps_clipboard.py`,
GPL-3.0-or-later):

- `TransformBox.begin()` copies the layer twice: `base` for undo and
  `floor` as the backdrop, cuts the selection out of the floor into an
  immutable `content` buffer, then `apply()` every frame restores the
  floor and blits the resampled content. The layer's mask layer is a
  second part transformed in lockstep.
- Handles: 4 corners, 4 sides, 4 rotate, 4 skew, pivot, move; grab
  radius in screen pixels.
- `commit()` diffs `base` against the layer per part and pushes one undo
  entry with `resume=self.snapshot()` so undo can re-enter the
  transform. `cancel()` restores `base`.
- `FloatingSelection` pins the floating content keyed by
  `sel_version` so a second drag resumes the original pixels instead of
  re-cutting.
- Clipboard is an addon-private buffer of `content`, `mask`, `origin`.
- The preview is written into the real layer array and flushed to the
  image each frame; the overlay draws only the box.

## v3 design

- `tools/transform.py`: `paint_system.transform_selection` modal,
  registered as a `WorkSpaceTool` in both editors, plus `G`/`R`/`S`
  style hotkeys inside the modal for keyboard-driven transforms.
- GPU state per part (layer image, and its mask image if PS-015 mask
  is an image): `floor` and `content` as `GPUTexture`s, `snapshot`
  from PS-090 for undo. Each frame: one pass writes `floor`, one pass
  draws `content` as a textured quad under the current matrix into the
  layer's ping-pong target (PS-050), read back only the union of the
  old and new bounding rects. Resampling is `CLOSEST` or `LINEAR`,
  selectable; pixel-art rotation algorithms are not ported.
- The selection mask is transformed with the same matrix so ants follow
  the content.
- 2D: the box lives in texel space; handles hit-tested in region space.
- 3D: the selected texels are rendered to a screen-space sprite with
  `sprite_to_screen` (PS-092), the user transforms the sprite as a 2D
  object on screen, and each frame `project_stencil` in sprite mode
  reprojects it onto the mesh into the layer target. Seams are handled
  by construction; occluded faces are not painted, as in native
  projection paint.
- Commit on Enter or click outside, cancel on Esc, Alt-drag duplicates
  (floor keeps the original). Undo entry carries `resume` so Ctrl+Z
  during a transform steps back inside it.
- `tools/clipboard.py`: `paint_system.pixels_copy`, `pixels_cut`,
  `pixels_paste`, `pixels_paste_new_layer`. Buffer is numpy plus origin
  rect; paste starts a transform at the cursor. Distinct from the layer
  clipboard in PS-017.
- Overlay: box, handles, pivot and sprite outline drawn by
  `selection/overlay.py`.

## Acceptance

- Move a selection by 10 texels and commit: the vacated area is
  transparent, content is offset exactly, undo restores the original.
- Rotate by 90 degrees with `CLOSEST`: pixels are a lossless
  permutation.
- 3D: drag a decal across a UV seam on a cube; the result is continuous
  on the mesh.
- Layer mask moves with the layer content.
- Cut, paste as new layer: new image layer contains only the selection
  at the same position.
