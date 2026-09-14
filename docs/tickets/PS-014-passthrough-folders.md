# PS-014 Pass-through folders

Epic B. Size S. Milestone M1.

## v2 behaviour

Only folders may pick `PASSTHROUGH` (`get_blend_mode_items`,
`data.py:1357`; enum built at `data.py:62-68`). A pass-through folder's
children are routed straight onto the stack below the folder
(`connect_passthrough`, `data.py:1849-1861, 1945`) and
`get_parent_layer_id(ignore_passthrough=True)` skips it for clipping
purposes. The folder's opacity still applies as a global fade in v2 via
the Mix node with `PASSTHROUGH -> MIX`.

## v3 design

- `blend_mode` items for folder nodes include `PASSTHROUGH`; the base
  layer node's enum callback checks `is_folder`.
- In `PaintSystemFolderLayerNode.emit`, when `blend_mode == 'PASSTHROUGH'`
  the folder's content chain is compiled with its bottom child's unlinked
  Color/Alpha resolving to the folder's own incoming Color/Alpha refs
  instead of the transparent constant. The result then goes through a
  MIX blend group with the folder's opacity, where `Prev` is the incoming
  stack, so opacity fades between "with content" and "without".
- Implementation hook: `CompileContext.push_stack_base(color_ref, alpha_ref)`
  / `pop_stack_base()`; `PaintSystemLayerNode.emit` consults the current
  stack base when its Color input is unlinked. The folder pushes before
  its content chain is emitted. Because the topological walk emits
  upstream nodes first, the folder must emit its content chain itself:
  `topological_order` treats folder nodes' `Content` inputs as owned by
  the folder (skip them in the global walk), and the folder emitter walks
  them with the base pushed.
- Clipping across a pass-through boundary works as in v2 because the
  content chain's bottom child sees a real base.

## Acceptance

- Pass-through folder with a MULTIPLY child over a red stack renders the
  same as the child directly on the stack.
- Folder opacity 0.5 in pass-through renders as a 50% blend between the
  two states.
- Switching a folder from MIX to PASSTHROUGH and back patches in place.
