# PS-016 Linked layers

Epic B. Size M. Milestone M2.

## v2 behaviour

`paste_layer(linked=True)` creates a BLANK layer with `linked_layer_uid`
and `linked_material` (`layers_operators.py:867-913`). `get_layer_data()`
(`data.py:1524`) resolves the source through a cached uid map; the linked
layer shares the source's node tree datablock, so edits anywhere show
everywhere. `unlink_layer` (`:915`) copies the data out. The list shows a
`LINKED` icon; `MAT_MT_LayerMenu` offers "Unlink Layer".

## v3 design

- `PaintSystemLinkedLayerNode(PaintSystemLayerNode)`: `source_tree`
  (PointerProperty to a `PaintSystemNodeTree`, may be the same tree) and
  `source_uuid`. `resolve()` returns the source node or `None`.
- `emit_source` emits the source node's source subgraph under the linked
  node's own uuid prefix (`ctx.emit_as(self, source_node)` swaps the
  node-id namespace while the source's `emit_source` runs). Blend mode,
  opacity, clip and masks belong to the linked node; only the content is
  shared. Sources that are folders link the whole content chain.
- `hash_parts` includes `source_tree.compiled_hash`-independent data: the
  source node's `subtree_hash` computed in the source tree's context.
- Editing: the Layer Settings panel for a linked node draws the source
  node's settings (`draw_layer_settings` delegated) with a header row
  "Linked to <tree> / <layer>" and the "Unlink" operator, which replaces
  the node by a deep copy of the source chain.
- Missing source (deleted) compiles to transparent and shows the warning
  icon with "Linked layer source missing".
- Cross-material linking works because trees are datablocks: the linked
  node stores the tree pointer, not a material.

## Acceptance

- Paint on the source image; the linked layer in another material
  updates after compile.
- Unlink produces an independent copy with new uuids and a copied image
  reference (image is shared, as in v2).
- Deleting the source shows the warning and does not crash the compile.
