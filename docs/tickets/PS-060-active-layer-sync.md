# PS-060 Active layer to canvas, UV map and brush sync

Epic G. Size M. Milestone M1.

## Status

Partly done (M0 slice 6):

- `context.py::update_active_image` sets the canvas to the active layer's
  `paint_image` (the base layer returns None, image layers their
  `image`), or None when the layer is locked, and switches image paint
  from Material to Single Image mode. It makes the layer's `uv_map` the
  mesh's active UV map, or the active render UV map when the layer names
  none, since that is what the compiled group samples; `AUTO` and
  `PS_UVMap` wait for PS-008 and PS-009. In texture paint mode it sets
  `brush.use_alpha = not lock_alpha`. It writes only what differs.
- Callers: the `active_layer_index` setter, `add_layer`,
  `toggle_paint_mode` (PS-061), the `lock_layer`, `lock_alpha`, `image`
  and `uv_map` updates, and `handlers/paint_handlers.py`.
- Selection changes need three triggers, because Blender reports them
  differently:
  - another active object is a depsgraph update. `depsgraph_update_post`
    compares the active object and material pointers with the pair it
    last synced. The pair is module state rather than v2's scene
    properties, so it is neither saved nor part of undo, and `undo_post`
    and `redo_post` forget it;
  - another material slot is no depsgraph update, but notifies the
    message bus. `Object.active_material_index` is subscribed, and
    subscribed again on `load_post`, because loading a file drops every
    subscriber;
  - clicking a node in the node editor makes it active without any update,
    message bus notification or handler call. A `SpaceNodeEditor` draw
    callback compares each Paint System tree's active node with the one it
    last drew and registers a timer that syncs, since drawing cannot write
    data.
- `load_post` syncs.
- `update_active_image` calls `selection.session.notify()` as its first
  statement, before it knows whether there is a tree, so every caller
  above also brings the live selection (PS-091) in step with a new
  active layer, tree, object or material, including a change to one
  with no tree. The session syncs on a timer after the function returns.
- The Layers panel box shows the `lock_alpha` toggle for layers with a
  paint image, as v2 did for image layers.
- Open: switching channels is no caller, because the active layer is the
  tree's active node whichever channel is shown (PS-032). Image masks and
  cached layers get `paint_image` with those features.

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
