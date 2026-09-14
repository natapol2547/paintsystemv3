# PS-037 Node editor panel, material properties injection, inspect layer

Epic D. Size S. Milestone M2.

## v2 behaviour

- `NODE_PT_PaintSystemShaderEditor` (`panels/extras_panels.py:408`),
  category "Paint System" in the shader editor: group list, channels,
  inspect buttons.
- `draw_paint_system_material` appended to
  `EEVEE_MATERIAL_PT_context_material` (`:487-498, 513`): group popover,
  name, add, delete menu inside the material properties tab.
- `inspect_layer_node_tree` (`operators/shader_editor.py:9`): rebuilds the
  node editor path to material -> group -> channel -> layer group and
  frames it; `exit_all_node_groups` (`:90`). `focus_ps_node`
  (`utils_operators.py:466`): splits an area into a shader editor and
  frames the group node.

## v3 design

- Shader editor sidebar panel: material's trees (PS-040), "Edit Paint
  System Tree" (switches the editor to the `PaintSystemNodeTree`),
  "Inspect Compiled" (enters the artifact group), compiled info block
  under `developer_mode`.
- Material properties injection ported verbatim.
- `paint_system.inspect_layer`: enters the artifact and selects/frames the
  nodes whose `ps_identifier` starts with the active layer's uuid. There
  is no per-layer group any more, so framing the identifier set is the
  equivalent.
- `paint_system.focus_ps_node` ported: frames the material's group node.

## Acceptance

- Inspect from the sidebar lands on the layer's blend node in the
  artifact; exit returns to the material tree.
