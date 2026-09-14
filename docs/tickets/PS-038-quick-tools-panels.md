# PS-038 Quick Tools panels

Epic D. Size S. Milestone M2.

## v2 behaviour

`panels/quick_tools_panels.py`, sidebar category "Quick Tools":

- Display (`:8`): wireframe toggle, `toggle_transform_gizmos`
  (`utils_operators.py:355`, stores gizmo flags on the WindowManager in
  paint modes).
- Mesh (`:46`): primitive add row, normals check / `recalculate_normals`
  / `flip_normals` (`utils_operators.py:216-260`), non-uniform scale alert
  box with Apply Transform, Set Origin.
- Paint (`:120`, poll `obj.mode == 'TEXTURE_PAINT'`): "Edit Externally"
  (PS-054), `add_preset_brushes` (`utils_operators.py:60`).

## v3 design

Port the three panels and their operators into `panels/quick_tools.py`
and `ops/utils_ops.py` without behaviour changes. They do not touch the
node tree.

## Acceptance

- Panels appear under "Quick Tools" with the v2 widgets; each button runs.
