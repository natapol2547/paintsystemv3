# PS-052 Invert, fill, clear, resize

Epic F. Size S. Milestone M3.

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
