# PS-064 Empty object gizmos and the Paint System collection

Epic G. Size M. Milestone M2.

## v2 behaviour

`ensure_empty_object` (`data.py:1476`), display types per gradient type
(`data.py:889-903`), rotation drivers (`:921-939`), unused empties
unlinked from the "Paint System" collection (`:906-914`). Empties are
also used by IMAGE/TEXTURE coord types OBJECT/CAMERA/etc. and DECAL
(`empty_object`). `select_empty` (`layers_operators.py:361`) re-links a
missing empty and selects it; `get_ps_object` maps an active empty back
to its parent mesh.

## v3 design

- `common/gizmos.py`: `ensure_empty(node, kind)` creates or repairs the
  empty (name `PS <layer> Gizmo`, parented to the paint object, display
  type by kind, linked into the `Paint System` collection which is
  excluded from render), stores it in `node.empty_object`.
  `release_empty(node)` unlinks and, when no other node references it,
  removes it. Called from operators and property updates, never from
  compile.
- `normalize_tree` reports (not repairs) nodes whose `empty_object` is
  None but whose type needs one; the warnings API (PS-019) surfaces it.
- `paint_system.select_empty` and `fix_missing_empties` ported.

## Acceptance

- Adding a linear gradient creates one empty in the Paint System
  collection; deleting the layer removes it; two layers sharing an empty
  keep it until both are gone.
