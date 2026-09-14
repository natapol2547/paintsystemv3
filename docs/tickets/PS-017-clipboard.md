# PS-017 Clipboard: copy, copy all, paste, paste linked, unlink

Epic B. Size M. Milestone M2.

## v2 behaviour

`copy_layer` / `copy_all_layers` (`layers_operators.py:820, 843`) store
`(material, uid)` pairs in `scene.ps_scene_data.clipboard_layers`.
`paste_layer` (`:867`) resolves each source, creates a same-type layer and
`copy_layer_data`, or a linked layer when `linked=True`; first entry at
CURSOR, rest AFTER; parents remapped through `new_layer_id_map`, roots
placed under the active folder. Menu entries in `MAT_MT_LayerMenu`
(`layers_panels.py:724-785`).

## v3 design

- `PaintSystemSceneSettings.clipboard: CollectionProperty(ClipboardEntry)`
  with `tree` pointer and `uuid`. Copy stores the active node; copy all
  stores every top-level node of the active channel (children come with
  their folder).
- `nodetree/clipboard.py::duplicate_subtree(tree, node, into_tree)` deep
  copies a node with its content chain (folders) and mask chain, gives new
  uuids, and returns the new top node. Uses `tree.nodes.new` + property
  copy via `node_state` (already excludes uuid/cache); user-owned
  parameter nodes are copied per PS-003.
- Paste: for each entry in order, duplicate (or create a linked node,
  PS-016) and `insert_above(active)` for the first, then above the
  previous paste. If the active node is an expanded folder, insert into
  it at top, matching v2's "under active folder" rule.
- Unlink is PS-016.
- Images are shared between copy and source (v2 behaviour). A later
  "Duplicate with new image" option is out of scope.

## Acceptance

- Copy a folder with two children and a mask, paste in another material,
  stack order and levels identical, all uuids new.
- Paste linked creates linked nodes for each entry.
- Clipboard survives switching objects; entries whose tree was deleted
  are skipped with a report.
