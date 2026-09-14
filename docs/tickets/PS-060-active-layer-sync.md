# PS-060 Active layer to canvas, UV map and brush sync

Epic G. Size M. Milestone M1.

## v2 behaviour

`update_active_image` (`paintsystem/data.py:251`): sets
`image_paint.canvas` to the active layer image (None when `lock_layer`
or the channel shows its bake image), sets the object's active UV map to
the layer's map, calls `update_brush_settings` (`:240`, `brush.use_alpha
= not lock_alpha`). Triggered by `active_index` updates
(`data.py:2328`), `paint_system_object_update` (`handlers.py:188`,
depsgraph OBJECT updates compared against
`last_selected_object/material`), and `load_post`.

## v3 design

- `context.py::update_active_image(context)` with the v2 rules over
  `PSContext`: canvas from the active node's `paint_image` property
  (image layers return `image`, image masks their mask image, cached
  layers still paint on the source image, others None), UV map from the
  node's `CoordMixin` (AUTO -> `PS_UVMap`, ensuring it exists per PS-009),
  brush `use_alpha` from `lock_alpha`.
- Callers: `active_row_index` update (PS-011), `set_active_layer`,
  `lock_*` updates, channel switch, object/material selection change
  handler (port `paint_system_object_update` with the last-selected
  tracking props on `scene.paint_system`), `on_load_post`.
- Never called from compile.

## Acceptance

- Selecting an image layer sets the canvas; selecting a solid layer
  clears it; selecting another object with a different material switches
  the canvas to that material's active layer.
