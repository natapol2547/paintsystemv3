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

### v2 UI

All paths are relative to `~/paintsystem`. Line numbers without a file
refer to `panels/extras_panels.py`.

Shader editor panel `NODE_PT_PaintSystemShaderEditor`
(`panels/extras_panels.py:408-485`):

- bl_label "Paint System", bl_idname
  "NODE_PT_paint_system_shader_editor", NODE_EDITOR / UI, category
  "Paint System". It has no `bl_options`, so it starts open.
- Poll: an active group exists and `space_data.tree_type ==
  'ShaderNodeTree'` (`:416-420`). The group comes from the active
  object, not the edited tree, so the panel also shows in a shader
  editor that displays another material or the world (inferred from
  code).
- `draw_header` (`:422-424`): a label with the `sunflower` icon.
- It is the only v2 panel in the node editor sidebar. The "Node Builder"
  panel `EXAMPLE_PT_NodeTreeBuilderPanel`
  (`paintsystem/graph/nodetree_builder.py:1331-1362`, category "Node
  Builder") is dead. Its module is only registered through the
  `"graph"` submodule, which is commented out
  (`paintsystem/__init__.py:5-11`).

Body (`:426-457`), in order:

1. Groups box: a `row(align)` with the label "Groups:"
   (OUTLINER_OB_GROUP_INSTANCE) and a "Create Node" button (ADD). Below
   it is `template_list("MATERIAL_UL_PaintSystemGroups", "", ps_mat_data,
   "groups", ps_mat_data, "active_index",
   rows=max(2, len(groups)))`. The row is the renameable group name
   only (`panels/main_panels.py:60-65`).
2. Channels box, drawn when the group has channels:
   - A `row(align)` with the label "Channels:"
     (OUTLINER_OB_GROUP_INSTANCE). While the editor is inside a group
     (`is_in_nodetree`, path length above one, `utils/nodes.py:211-212`),
     the row adds `paint_system.exit_all_node_groups` "Exit All Groups"
     (NODETREE).
   - An aligned column. Channels after the first are preceded by a
     separator. Each channel is a `col.panel(f"channel_{idx}",
     default_closed=True)` whose header is the channel name with its
     socket icon. The open state is keyed by list index, not by name.
   - Inside an open channel, one row per entry of
     `channel.flattened_unlinked_layers` (`draw_layer_row`, `:453-457`).
     Nothing filters by folder state, so children of collapsed folders
     are listed too.

Layer row (`draw_layer_row`, `:459-485`):

- The entry is resolved with `get_layer_data()`. A linked entry whose
  source is missing draws nothing (`:462-464`).
- A `row(align)` starts with a BLANK1 label. One indent label follows
  per nesting level: `folder_indent` at the deepest level, BLANK1 for
  the others.
- `row.enabled = opacity > 0 and enabled` of the resolved layer.
- `draw_layer_icon` (`panels/common.py:531-573`) and a plain name label.
  The name is not editable here. `draw_layer_icon` draws a folder's
  icon as the `is_expanded` toggle and a solid colour layer's icon as
  its colour swatch (`panels/common.py:545-552`), so both stay editable
  in these rows. Toggling a folder here changes the Layers list, not
  this one.
- For non-FOLDER layers with a node tree, when a material is active
  (`:480`), two icon buttons follow:
  - An add-node button (ADD, text ""). It adds a group node for the
    layer's own node tree into the edited tree.
  - `paint_system.inspect_layer_node_tree` (NODETREE, text ""), with the
    entry's `layer_id` and `channel_name`.

Add-node button (`nodetree_operator`, `panels/extras_panels.py:16-23`):
`node.add_node` with `type = "ShaderNodeGroup"`, `use_transform = True`,
and one `settings` entry with `name = "node_tree"` and `value =
"bpy.data.node_groups['<name>']"`. The new node sticks to the mouse, as
with the Add menu. "Create Node" uses it for the group tree, and the
layer rows use it for the layer tree.

Material properties injection (`draw_paint_system_material`,
`panels/extras_panels.py:487-498`), appended to
`EEVEE_MATERIAL_PT_context_material` in `register` (`:511-513`):

- It is drawn only when the material has groups. A box holds the label
  "Paint System Node Groups:" (`sunflower`).
- A `row(align)` scaled 1.3 x 1.2 through `scale_content` holds, all
  `text=""`: popover `MAT_PT_PaintSystemGroups` (NODETREE), the active
  group `name`, `paint_system.new_group` (ADD), and
  `paint_system.delete_group` (REMOVE).
- The REMOVE button calls the operator directly. It does not go through
  `MAT_MT_DeleteGroupMenu`, unlike the main panel. The operator's own
  confirmation dialog still opens
  (`operators/group_operators.py:452-463`).
- `EEVEE_MATERIAL_PT_context_material` covers EEVEE and Workbench only.
  Cycles draws `CYCLES_PT_context_material` (Blender 5.2
  `scripts/addons_core/cycles/ui.py:1273`), so the injection is missing
  under Cycles.

Operators the panels reach:

- `paint_system.inspect_layer_node_tree`
  (`operators/shader_editor.py:9-87`, bl_label "Inspect Layer Node
  Tree", REGISTER and UNDO). Properties: `layer_id` (Int) and
  `channel_name` (String). Poll: the area is a NODE_EDITOR. Steps:
  - Find the channel by name and the entry by id, then resolve the
    source with `get_layer_data()`. Cancel silently when any step fails.
  - `path.start(material tree)`.
  - Append the group node (the first match, connected or not), then the
    channel node inside the group.
  - Find the first node in the channel tree whose `node_tree` is the
    resolved layer's tree. If none is found, report WARNING "Could not
    find node for layer '<name>'" and cancel.
  - Append that node and run `node.view_all`.
- `paint_system.exit_all_node_groups`
  (`operators/shader_editor.py:90-105`, bl_label "Exit All Groups"):
  poll `is_in_nodetree`, then `path.clear()`.
- `paint_system.focus_ps_node` (`operators/utils_operators.py:466-512`,
  bl_label "Focus PS Node", poll `active_group`):
  - Split the current area (`split_area`,
    `operators/utils_operators.py:409-420`, `screen.area_split`
    VERTICAL at factor 0.55). If that fails, report
    WARNING "Could not split the area." and cancel.
  - Make the new area a NODE_EDITOR with `tree_type = 'ShaderNodeTree'`
    and `show_region_ui = True`, so this sidebar panel is visible.
  - Focus the group node (any), or the Material Output when there is no
    group node. Select only that node, make it active, wait for a
    redraw, then run `node.view_selected` in the new area.
  - The only caller is the warning box in `MAT_PT_Layers`
    (`panels/layers_panels.py:623-631`). The box shows "Paint System
    not connected" (ERROR) / "to material output!" (BLANK1) when no
    group node feeds the Material Output (`find_node` defaults to
    `connected_to_output=True`, `utils/nodes.py:79`). The button reads
    "Open Shader Editor" (NODETREE) and is only drawn when no
    NODE_EDITOR is open on the screen.

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
