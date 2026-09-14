# PS-019 Base layer flags and warnings API

Epic B. Size S. Milestone M1.

## v2 behaviour

`Layer` (`data.py:1240-1290`): `lock_layer` (row icon VIEW_LOCKED, layer
settings disabled, paint canvas cleared), `lock_alpha` (brush
`use_alpha`), `is_expanded` (folders), `external_image` (quick edit),
`edit_external_mode`. `get_layer_warnings()` feeds the warning icon in the
list and the wrapped warnings box under it (`layers_panels.py:101-103,
659-669`; `show_layer_warnings` operator `layers_operators.py:1014`).

## v3 design

- Add to `PaintSystemLayerNode`: `lock_layer`, `lock_alpha`,
  `external_image`, `edit_external_mode`. `lock_*` updates call
  `update_active_image` (PS-060) rather than `mark_tree_dirty`, since they
  do not affect the graph.
- `is_expanded` lives on the folder node only (PS-010).
- `warnings(context) -> list[str]` on the base node, default empty.
  Types override: image layer with no image, missing UV map, missing
  empty, linked source missing, cache stale, custom group missing
  sockets. The list and the box read this.
- `is_folder`, `is_clip`, `modifies_color_data` (adjustment) class
  attributes for poll checks.

## Acceptance

- Locking a layer greys the settings panel and clears the canvas.
- Warning icon appears for an image layer whose image was removed and
  the dialog wraps the text as in v2.
