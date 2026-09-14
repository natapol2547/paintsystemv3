# PS-024 Gradient layer and empty gizmo

Epic C. Size L. Milestone M2.

## v2 behaviour

`gradient_type` (`data.py:119`): GRADIENT_MAP, LINEAR, RADIAL, DISTANCE,
FAKE_LIGHT (`graph/basic_layers.py:477-517`). A Colour Ramp is the source,
fed by a Map Range:

- LINEAR: Texture Coordinate (object = empty) Object -> Separate XYZ Z.
- RADIAL: Vector Math LENGTH, Map Range inverted.
- DISTANCE: Camera Data View Distance.
- GRADIENT_MAP: the stack colour below (acts like an adjustment).
- FAKE_LIGHT: Combine XYZ -> Vector Rotate (Euler from `object_rotation`
  driven by the empty) dotted with the normal; `new_gradient_layer`
  forces blend MULTIPLY and the name "Fake Light"
  (`layers_operators.py:306-308`).

Empty gizmos are created by `ensure_empty_object` (`data.py:1476,
889-939`): SINGLE_ARROW for linear, SPHERE for radial, offset arrow for
fake light, drivers for rotation, unused empties unlinked from the PS
collection. UI: empty select / fix missing (`layers_panels.py:215-230`),
`gradient_node_settings_panel` with nested Map Range
(`layers_panels.py:334-352`). Menu: "Gradient" submenu plus top-level
"Fake Light" (icon LIGHT); row icon COLOR or LIGHT.

## v3 design

- `PaintSystemGradientLayerNode` with `gradient_type`, `empty_object`.
  GRADIENT_MAP sets `is_clip` like an adjustment.
- Colour Ramp and Map Range are artifact-owned parameter nodes (PS-003);
  the settings panel draws `template_color_ramp` and the Map Range inputs
  from `ctx.param_node`.
- FAKE_LIGHT rotation uses IR drivers (PS-004) on the Combine XYZ inputs.
- Empty management goes through PS-064 (`ensure_empty(node, kind)`), called
  from the add operator and from the `gradient_type` update, never from
  `emit`. `emit` only references `empty_object`; a missing empty compiles
  with object None and raises a warning (PS-019).
- Operators: `new_gradient_layer` (names from the type title, Fake Light
  special case), `select_empty`, `fix_missing_gradient_empty` (recreates
  empties for all gradient nodes in the tree).

## Acceptance

- Linear gradient across the cube matches v2 direction and falloff when
  the empty is placed identically.
- Fake light updates in the viewport when the empty rotates, without a
  recompile.
- Deleting the empty and pressing Fix Missing restores it.
