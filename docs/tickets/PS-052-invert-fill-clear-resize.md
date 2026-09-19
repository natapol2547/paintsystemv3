# PS-052 Invert, fill, clear, resize

Epic F. Size S. Milestone M3.

## Status

Clear, Fill and Invert Colors shipped on 2026-09-20 as
`filters/actions.py`, `filters/brush_color.py` and `ops/pixel_ops.py`,
checked by `tests/test_selection_actions.py`. Resize is not done, and is
the only part of this ticket left.

What an action covers, which is the safety rule:

- no selection on the tree: the whole layer;
- a selection: inside its mask, weighted by coverage, so a feathered
  edge blends;
- a selection whose mask covers no texel of this layer: nothing at all,
  with the message "The selection covers no pixels of this layer".

That last case departs from PS-093 on purpose. `selection.session`
counts an empty mask as no selection, because painting through it is
harmless. Clearing a whole layer because a box was dragged over empty
background is not, so the actions refuse instead of widening. The
difference is deliberate; do not make the two agree by changing this
side.

Other decisions worth keeping:

- Fill stores the values a full-strength dab of the current colour would
  store. Both halves of that moved in 5.0: where the colour lives (brush,
  unified paint settings, or a brush opting into the unified colour in
  5.3) and what the value means (`COLOR_GAMMA` display sRGB up to 4.5,
  `COLOR` scene linear from 5.0). `filters/brush_color.py` reads both
  from the RNA rather than from the version number, and refuses a byte
  image in a colour space Python cannot convert into.
- Lock Alpha: Fill honours it and keeps the transparency it found. Clear
  is greyed out, because under Lock Alpha it could only do nothing.
- Invert works in the image's own storage space, so a byte layer inverts
  to exactly `255 - k`. A float layer holds scene linear, and inverting
  its sRGB encoding is what makes the two match.
- The actions refuse, by name, a locked layer, a layer with no image, a
  layer whose live bake cache would hide the edit, a UDIM or linked or
  non-still image, and an image with no pixels. The cheap flag checks are
  in `poll`; the ones that cost a read or a GPU pass are refusals in
  `execute`, so the user gets a reason rather than a dead button.
- One Ctrl+Z takes an action back. The operators carry `{'REGISTER'}`
  only and change no document data in the same call (PS-090).
- No keyboard shortcuts: the user chose buttons and the sidebar only.

They draw in the Selection section of the main panel. The floating
action bar in the 3D view is the next slice.

## v2 behaviour

`operators/image_operators.py`: `invert_colors` (`:24`, `image.invert` under
an `edit_image` override, alpha off by default), `resize_image` (`:55`,
relative scale enum 0.5x to 4x or custom, `image.scale`), `clear_image`
(`:117`, bug: writes `numpy.empty` and zeroes only the red channel),
`fill_image` (`:135`, unified brush colour with alpha 1, skips the dialog
when a unified colour owner exists). Menus `MAT_MT_ImageFilterMenu`
(brush painter, blur, invert, fill) and `MAT_MT_ImageMenu` (resize, clear).

## v3 design

- Invert, fill and clear are one-pass GPU filters in the PS-050 registry
  (invert with per-channel toggles; fill with colour and "fill alpha"
  option; clear writes transparent black, fixing the v2 bug).
- Resize stays `image.scale` (CPU, Blender's own resampling) with the v2
  dialog.

## Acceptance

- Clear on a painted image yields all-zero pixels.
- Fill uses the current unified colour without a dialog when a unified
  owner exists.
