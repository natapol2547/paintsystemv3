# PS-022 Attribute layer

Epic C. Size S. Milestone M2.

## v2 behaviour

`ShaderNodeAttribute` source (`graph/basic_layers.py:442-450`);
`attribute_type` (GEOMETRY/OBJECT/INSTANCER/VIEW_LAYER) and
`attribute_name` were operator properties written straight onto the node
(`layers_operators.py:194-228`), so they were lost on rebuild and not
copied with the layer. `color_output_name` / `alpha_output_name` choose
Color/Vector/Fac/Alpha outputs. Settings panel `attribute_node_settings_panel`
(`layers_panels.py:367-377`) drew the live node. Menu entry "Attribute
Color" (icon MESH_DATA).

## v3 design

- `PaintSystemAttributeLayerNode` with `attribute_type`, `attribute_name`
  (with a search over the active object's colour attributes and UV maps
  when type is GEOMETRY), `color_output` (COLOR/VECTOR/FAC),
  `alpha_output` (ALPHA/FAC/NONE).
- `emit_source` emits one Attribute node.
- Warning when `attribute_name` is not found on the active object.
- Operator `paint_system.new_attribute_layer`.

## Acceptance

- Colour attribute on the cube shows through; renaming the attribute
  property patches in place; copy/paste keeps the name.
