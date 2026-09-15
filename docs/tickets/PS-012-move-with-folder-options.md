# PS-012 Move up/down with folder movement options

Epic B. Size M. Milestone M1.

## Status

Done for the demo (M0 slice 4) except the clip relationship, which waits
for PS-013. `stack_ops.movement_options` returns `MoveOption(action,
target, placement, folder)` and `stack_ops.move` performs one;
`PaintSystemNodeTree.move_layer_node` wraps it in one compile and expands
the folders around the moved layer. Deviations:

- The operators are `paint_system.move_layer_up` and `move_layer_down`,
  matching the channel operators; their poll greys a button with nothing
  on offer.
- A single option runs without a menu. v2 only skipped the menu for a
  lone `SKIP`.
- `SKIP` is offered only when there is a sibling to skip. v2 always
  offered it and it did nothing without one.
- Down from the last layer of a folder offers `MOVE_OUT_BOTTOM` (leave
  that folder only). `MOVE_ADJACENT` is offered as well when the next
  row is further out, where it leaves several folders at once as in v2.
  Without this the lone `MOVE_ADJACENT` would run without asking and
  jump out of every folder.
- Menu labels name the layer or folder: "Skip over 'X'", "Move into
  'X'", "Move out of 'X'", "Move to top level".

`tests/test_layers.py` has the option table for a stack with nested, empty
and root folders, every action's result with folders carrying their
content, one compile per move, the operators and undo.

## v2 behaviour

`paint_system.move_up` / `move_down` (`operators/layers_operators.py:658,
738`) call `get_movement_options` (`nested_list_manager.py:241-310`):

- UP: if the item above is its parent folder, only `MOVE_OUT`; else
  `MOVE_INTO` when the item above is a folder, `MOVE_ADJACENT` when the
  item above has a different parent, and `SKIP`. At top of a non-root
  parent: `MOVE_OUT`.
- DOWN: at bottom of a non-root parent: `MOVE_OUT_BOTTOM`; else
  `MOVE_INTO_TOP` when the next sibling is a folder, `MOVE_ADJACENT` when
  it has a different parent, and `SKIP`.
- One option executes directly; several open a popup menu.
  `execute_movement` (`:406-460`) performs it.

## v3 design

- `nodetree/stack_ops.py::movement_options(tree, channel, node, direction)`
  computes the same option list from `stack()` (PS-010), using
  `StackItem.parent` and `index_in_parent`.
- `execute_movement(tree, node, direction, action)` implements each action
  as link rewiring: detach the node (reconnect its consumer to its
  upstream), then reinsert with `insert_above` / `insert_into`.
- Operators `paint_system.move_up` / `move_down` keep the v2 UX: execute
  immediately for a single option, otherwise `wm.popup_menu` listing the
  option labels ("Move into 'X'", "Move out of 'X'", "Skip over").
- Folder nodes move with their whole content chain because the chain is
  attached to the folder, not to the stack.
- A move is one undo step and one compile.

## Acceptance

- Table-driven test replicating the v2 option matrix for: root item,
  first child, last child, item above a folder, item below a folder,
  nested folder.
- Moving into a folder from above and from below places the node at the
  expected end.
- Move with a clipped layer keeps its clip target relationship (PS-013
  defines what happens when the base moves away).
