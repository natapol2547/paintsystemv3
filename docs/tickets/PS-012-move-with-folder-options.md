# PS-012 Move up/down with folder movement options

Epic B. Size M. Milestone M1.

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
