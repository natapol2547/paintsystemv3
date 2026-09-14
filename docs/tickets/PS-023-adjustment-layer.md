# PS-023 Adjustment layer

Epic C. Size M. Milestone M2.

## v2 behaviour

`adjustment_type` (`data.py:127`): BRIGHTCONTRAST, GAMMA, HUE_SAT, INVERT,
CURVE_RGB, RGBTOBW, MAP_RANGE, mapped by `get_adjustment_identifier`
(`graph/basic_layers.py:400-410`). The source node takes `group_input.Color`
(the stack below), a Value node of 1 feeds `Fac` where present, RGB to BW
outputs `Val`, Map Range uses `Value`/`Result` (`:452-475`). Always clipped
(`data.py:1912`). Settings drawn with `template_node_inputs` on the live
node (`layers_panels.py:197-203`). Menu "Adjustment" submenu generated
from `ADJUSTMENT_TYPE_ENUM`; layer named after the enum label
(`layers_operators.py:229`).

## v3 design

- `PaintSystemAdjustmentLayerNode` with `adjustment_type`; `is_clip` forced
  True and hidden; `modifies_color_data = True` (blocks merge, PS-018).
- `emit_source`: the adjustment node is an artifact-owned parameter node
  (PS-003) so curves and sliders are edited on it directly. Its colour
  input is IR-linked to the incoming stack colour (the clip base source
  via PS-013), which the builder must link even on a user-owned node:
  links are always IR-managed, only values and properties are not.
- Settings panel: `ctx.param_node(node, 'adjust')` drawn with
  `template_node_inputs`, falling back to "Compile to edit" before the
  first compile.
- Changing `adjustment_type` changes `bl_idname`, which the builder
  handles by recreating the node (identifier stays); previous parameter
  state is lost, as in v2.
- Alpha: v2 output alpha was the stack alpha (clip). Same here through
  the clip path.

## Acceptance

- Invert over a red solid renders cyan; opacity 0.5 renders the midpoint.
- Curve edits survive recompile and undo.
- Node cache invalidates after a curve edit on the next compile.
