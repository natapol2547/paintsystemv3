# PS-011 Layer rows view model for the UIList

Epic B. Size M. Milestone M1.

## Status

Done (M0 slice 4), without the mirror collection the design below
describes:

- `PAINTSYSTEM_UL_layers` (`panels/layers_panels.py`) lists `tree.nodes`
  directly. `filter_items` hides nodes outside the stack and rows under a
  collapsed folder, and sorts by stack position. Nothing derived is
  stored, so there is nothing to rebuild on undo, load or channel switch,
  and nothing a compile would write after its undo step.
- `layer_rows(tree)` is the row model (`order`, `level`, `visible`,
  `parent_enabled`), computed from `stack()` on every redraw.
- `PaintSystemNodeTree.active_layer_index` is a get/set property over
  `nodes.active`; the setter selects the node and calls
  `context.update_active_image`, which for now only points the canvas at
  an image layer's image (the full rules are PS-060).
- Rows inside a disabled folder are greyed with `UILayout.active`, not
  disabled as in v2, so a disabled folder still expands and its layers
  can still be renamed or toggled.

Acceptance is covered by `tests/test_layers.py` (rows, filter, active
index) and `tests/test_ui_draw.py`.

## v2 behaviour

`MAT_PT_UL_LayerList` (`panels/layers_panels.py:58-138`) draws from the
flat collection: indentation from `get_item_level_from_id`, hidden rows
when an ancestor is collapsed, `enabled=False` when the parent folder is
disabled, and `flt_neworder` from `flattened_unlinked_layers`.

## v3 design

`template_list` needs a collection and an index. Layer nodes are not a
`CollectionProperty`, so the tree carries a mirror collection that is
rebuilt from `stack()`:

- `PaintSystemNodeTree.layer_rows: CollectionProperty(LayerRow)` with
  `node_name`, `uuid`, `level`, `is_last_level_marker`, `visible`,
  `parent_enabled`. `active_row_index: IntProperty(update=...)` sets
  `tree.nodes.active` and calls `update_active_image` (PS-060).
- `refresh_layer_rows(tree, channel)` rebuilds the collection when the
  stack changes. Trigger points: `mark_dirty` (structural changes always
  go through it), channel switch, `is_expanded` toggles. Rebuild is cheap
  (a few hundred items at most) and happens before the compile in
  `flush`, plus synchronously in operators that need the index right
  away.
- The UIList draws `LayerRow` items and resolves the node by name for
  icons, name editing and toggles. `filter_items` hides rows with
  `visible == False` and keeps order.
- Rows are `SKIP_SAVE`-free so the list is correct right after load
  without a compile; `on_load_post` calls `refresh_layer_rows` for all
  trees.

The mirror is derived, never edited by the UI except `active_row_index`,
so it cannot drift: any doubt, rebuild it.

## Acceptance

- Collapsing a folder hides its descendants; expanding restores them.
- Renaming a layer in the list renames the node (built-in `Node.name`,
  which Blender keeps unique; nodes must not redefine `name`).
- Selecting a row makes the node active and sets the paint canvas.
- Undo after adding a layer leaves the list consistent (rows rebuilt in
  `on_undo_post`).
