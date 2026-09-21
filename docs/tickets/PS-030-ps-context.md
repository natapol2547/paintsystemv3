# PS-030 `PSContext` resolver

Epic D. Size S. Milestone M1.

## Status

Done for the demo (M0 slice 3): `context.PSContext` and
`parse_context(context)`, covered by `tests/test_stack.py`. Deviations from
the design below:

- No `prefs`, `trees` or `source_layer` fields yet; they arrive with the
  tickets that need them (PS-040, PS-016). `scene_settings`,
  `active_object`, `ps_objects`, `material` and `material_settings` are
  left out too, since nothing reads them; each comes back with the first
  code that does. `PSContext` holds `ps_object`, `tree`, `channel`,
  `layer` and `stack_item`.
- No `PSContextMixin`. Call sites use `parse_context(context)` directly,
  which reads the same without a base class.
- `ps_object` accepts meshes only (an EMPTY resolves to its parent mesh).
  Grease Pencil support comes with the ticket that paints on it.
- Panel polls do not use it yet; the layers panel rework (PS-034) moves
  them over.

## v2 behaviour

`paintsystem/context.py`: `PSContext` dataclass (`ps_settings`,
`ps_scene_data`, `active_object`, `ps_object`, `ps_objects`,
`active_material`, `ps_mat_data`, `active_group`, `active_channel`,
`active_layer`, `unlinked_layer`, `active_global_layer`).
`get_ps_object` maps EMPTY to its parent mesh, MESH and GREASEPENCIL to
themselves. `parse_material` clamps every active index. Panels call
`PSContextMixin.parse_context(context)` in both `poll` and `draw`.

## v3 design

`context.py` grows a `PSContext` dataclass and `parse_context(context)`:

- `prefs`, `scene_settings`, `active_object`, `ps_object` (EMPTY -> parent
  mesh; needed for gradient empties), `ps_objects` (selected meshes),
  `material`, `material_settings`, `tree` (from `get_active_tree`, which
  already prefers the node editor's edit tree), `trees` (PS-040),
  `channel`, `layer` (active node if `is_layer_node`), `stack_item`
  (PS-010) and `source_layer` (linked layer resolved, PS-016).
- `PSContextMixin` with a `parse_context` staticmethod for panels and
  operators, keeping the v2 call sites readable.
- No caching; the walk is a handful of attribute reads.

## Acceptance

- Unit test in headless Blender: with an empty parented to the cube
  active, `ps_object` is the cube; with no material, `tree` is None and
  every panel poll returns False without raising.
