# PS-010 Folder layer node and hierarchical stack walk

Epic B. Size L. Milestone M1. This ticket defines how the v2 nested list
maps onto the node graph; most of Epic B and D builds on it.

## v2 behaviour

Layers are a flat collection with `id`, `parent_id`, `order`
(`paintsystem/nested_list_manager.py`). `Channel.update_node_tree`
(`data.py:1818-2004`) walks it bottom-up: a FOLDER layer starts a new
sub-stack whose result enters the folder group through `Over Color` /
`Over Alpha` (`graph/basic_layers.py:433-435`, `data.py:861-863,
1953-1958`). `get_item_level_from_id` gives indentation, `is_expanded`
collapses children in the UIList filter (`panels/layers_panels.py:110-138`).

## v3 design

The hierarchy is the graph. There is no `parent_id`.

- `PaintSystemFolderLayerNode(PaintSystemLayerNode)` has the standard
  inputs (Color, Alpha, Mask) plus `Content Color` and `Content Alpha`.
  Its `emit_source` returns the Content refs, so the folder blends its
  content over the stack below with its own opacity and blend mode
  exactly like any other layer. `is_expanded: BoolProperty` lives on it.
- Children are the chain upstream of `Content Color`. The bottom child's
  Color/Alpha inputs are unlinked, which the base layer node already
  treats as a transparent constant.
- `PaintSystemNodeTree.layer_chain(channel)` is replaced by
  `stack(channel) -> list[StackItem]` where
  `StackItem(node, level, parent: StackItem | None, index_in_parent)`.
  It walks from the Group Output upstream through `Color` inputs, and on a
  folder recurses into `Content Color` at `level + 1` before continuing
  below the folder. Output order is top-first, matching v2
  `flatten_hierarchy`.
- Structural edits are link operations on `nodetree/stack_ops.py`:
  `insert_above(node, target)`, `insert_into(folder, node, at_top: bool)`,
  `remove(node)` (already in `remove_layer`), `move(node, action)` (PS-012).
  Every function keeps the invariant that each layer node has at most one
  downstream consumer on `Color` and that `Alpha` follows `Color`.
- `normalize_tree` gains a check that `Alpha` links mirror `Color` links
  and repairs them, so a user rewiring in the node editor cannot break the
  stack walk.
- A layer's `enabled` state is independent of its parent, but the
  compiled folder passes its content through its own Opacity, so a
  disabled folder hides children without touching them (v2 greys the row,
  `layers_panels.py:73-76`).

Why not nested trees for folders: a nested `PaintSystemNodeTree` (the
existing group layer) is a real datablock with its own channels. Folders
are lighter: a visual group inside one channel. Keeping them as nodes in
the same tree means one artifact, one undo history, one uuid namespace.

## Acceptance

- `stack()` on a tree with folders nested two deep returns the v2 order
  and levels for the same layout.
- Inserting into an empty folder, into a folder at top and at bottom,
  removing a folder with children (children are removed with it, as in
  v2 `delete_item`) all keep `normalize_tree` silent.
- Compiled output equals a flat stack when the folder is MIX at opacity 1.
- Node editor view of a two-level stack is readable after
  `arrange` (folders' content chains placed above the folder).
