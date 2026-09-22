# PS-028 Custom node group layer redesign

Epic C. Size L. Milestone M2.

## v2 behaviour

`NODE_GROUP` layer (`graph/basic_layers.py:545-560`): `custom_node_tree`
pointer plus four socket-name enums (`color_input_name`,
`alpha_input_name`, `color_output_name`, `alpha_output_name`,
`data.py:1076-1123`) enumerated from the group interface; `_NONE_` means
unused. Creation (`layers_operators.py:405-576`) lists ShaderNodeTree
groups not prefixed `.PS`/`Paint System`/`PS `, auto-selects sockets named
"Color"/"Alpha", refuses when both outputs are `_NONE_`, warns on
unsupported socket types. Other group inputs are exposed in the settings
box from the live group node (`layers_panels.py:204-214`). Problems: the
extra inputs were only editable on the live node (lost on rebuild), could
not be driven by other layers, and the enums depended on live node state.

### v2 UI

Paths are relative to the v2 repository root (`~/paintsystem`).
Corrected cite for the section above: the four socket-name enums are
`paintsystem/data.py:1076-1099`; `:1101-1128` holds the deprecated
integer indices.

**Host panel.** Custom layer settings are drawn inside the "Layer
Settings" sub-panel (open by default) of `MAT_PT_Layers` (VIEW_3D, UI,
category "Paint System"; `panels/layers_panels.py:494-512, 671-674`),
below the common clip/lock/blend/opacity box, the layer list and the
warnings box (`:603-669`). `draw_layer_settings` sets `layout.enabled
= not lock_layer` (`:174`).

**Settings box** (`panels/layers_panels.py:204-214`). `get_settings_box`
gives a new box in the modern UI (`panels/common.py:464-480`), then
`box.column()`. The inputs listed are the sockets on the live
`ShaderNodeGroup` "source" node that are not linked and whose name is
not "Color" or "Alpha". The filter is by name, not by mapping: an
unmapped input called "Color" is hidden, and a mapped input is hidden
only because it is linked. If any remain, the box draws
`col.label(text="Node Group Settings:", icon='NODETREE')` and then
`col.prop(socket, "default_value", text=socket.name)` for each one,
with no property split. The box itself is drawn even when it is
empty. There is no guard for a missing "source" node: `inputs` is
read from `source_node` directly, so a layer without one raises in
the draw call (`:207-208`).

**"Sockets Settings:" section** (`panels/layers_panels.py:292-300`),
drawn after the type `match` for NODE_GROUP only:
`layout.panel("node_group_panel")` with no `default_closed`, so it
starts open. Its header is `label(text="Sockets Settings:")` with no
icon. The body sets `use_property_split = True` and
`use_property_decorate = False`, then calls
`draw_socket_grid(col, layer, include_inputs=True)`
(`panels/common.py:426-450`). This draws two boxes, each with a
`grid_flow(columns=2, align=True, even_columns=True, row_major=True)`:
the first has "Color Output" / "Alpha Output" labels over the enums
(text ""), the second has "Color Input" / "Alpha Input".

The layer enums read the group interface
(`paintsystem/data.py:1043-1075`, `utils/nodes.py:116-131`). Shader
sockets are excluded; other types, including Int and Boolean, are
listed. Colour, Float and Vector sockets get the `color_socket`,
`float_socket` and `vector_socket` icons; every other type, Int and
Boolean included, falls back to `color_socket`
(`custom_icons.py:40-46`). In the panel, None is the first item of
both input lists and of the alpha output list, and the colour output
has no
None. This order differs from the creation dialog, where None is last.
Every enum change rebuilds the layer tree. `custom_node_tree` is not
drawn anywhere, so the group cannot be swapped after creation (it is
set only by the operator; `operators/layers_operators.py:524-534`,
and by v1 migration).

After the sockets section, the only other section for this type is
the shared "Actions" sub-panel (closed by default, icon
KEYTYPE_KEYFRAME_VEC; `panels/layers_panels.py:462-492`). NODE_GROUP
gets no Transform section, which is drawn for IMAGE and TEXTURE only
(`:379`).

**List row.** Icon NODETREE (`panels/common.py:557-558`). The layer
menu offers "Convert to Image Layer" for NODE_GROUP
(`panels/layers_panels.py:733-739`).

**Add menu entry.** "Custom Layer" (icon NODETREE) is the last entry
of `MAT_MT_AddLayerMenu`, after Random Color. It is a direct operator
button, not a submenu (`panels/layers_panels.py:883-884`).

**Creation dialog** (`operators/layers_operators.py:405-576`).
`paint_system.new_custom_node_group_layer`, bl_label "New Custom Node
Group Layer", REGISTER/UNDO, poll: active channel. `invoke` runs
`auto_select_sockets` and opens `invoke_props_dialog` (`:537-539`).
The properties are `node_tree_name` ("Node Tree", update
`auto_select_sockets`), `color_input_name` ("Custom Color Input"),
`alpha_input_name` ("Custom Alpha Input"), `color_output_name`
("Custom Color Output") and `alpha_output_name` ("Custom Alpha
Output") (`:480-507`). Item lists (`:411-439`):

- Group picker: every `ShaderNodeTree` whose name does not start with
  ".PS", "Paint System" or "PS ".
- Inputs: interface inputs plus `_NONE_` "None" (icon BLANK1) at the
  end.
- Colour output: interface outputs, no None.
- Alpha output: interface outputs plus None at the end.

Auto-selection (`:441-473`) picks the inputs whose interface socket
name is "Color" and "Alpha" and the output named "Alpha", otherwise
`_NONE_`. The colour output is not auto-selected (that code is
commented out), so it keeps the enum's current value, which is the
first output on first use.

The dialog draws, in order (`:541-575`):

1. `label(text="Select node tree:", icon='NODETREE')`.
2. With no eligible group: `label(text="No supported node trees
   found", icon='ERROR')`, and the dialog stops there.
3. A row scaled 1.5 x 1.5 with `node_tree_name` (text "").
4. If any interface socket is not Color, Float or Vector: an alert box
   with a row holding an ERROR icon label and a column label "Node has
   unsupported sockets (Shader)" (`:474-478`, `:551-557`). The text
   says Shader, but Int, Boolean and other types trigger it too. It is
   only a warning and does not block creation.
5. If a group is chosen: a box with a centred row
   `label(text="Socket Connection", icon='NODETREE')`, then a row
   with two boxes side by side. The left box has a centred "Input"
   label, `color_input_name` (text "Color") and `alpha_input_name`
   (text "Alpha"). The right box has "Output", `color_output_name`
   ("Color") and `alpha_output_name` ("Alpha").

On execute (`:514-535`) the operator cancels with no group. If both
outputs are empty or `_NONE_`, it reports ERROR "Node tree must have
at least one output socket". The colour output cannot be None, so in
practice this fires only for a group without non-shader outputs.
Otherwise it creates a NODE_GROUP layer named after the group, with
the four mappings. It runs once for the Paint System object and once
per other selected mesh's active material
(`operators/common.py:43-78`). The ERROR path returns `{'CANCELLED'}`,
which is truthy, so the base class does not count it and its
"Completed with N error(s)" warning never appears
(`operators/common.py:62-75`).

**Graph and rebuild details not covered above.** `parse_socket_name`
returns the stored name only while it still exists in the group
interface, otherwise None (`paintsystem/graph/basic_layers.py:413-421`).
A removed socket therefore silently unmaps on the next rebuild, but
editing the group does not itself trigger a rebuild; no depsgraph or
msgbus handler watches custom groups
(`paintsystem/handlers.py:136-265`). The next rebuild comes at the
latest on file load: the `load_post`
handler runs `migrate_socket_names`, which rebuilds every NODE_GROUP
layer that has a group, whether or not it had deprecated indices
(`paintsystem/handlers.py:94, 109-114`,
`paintsystem/versioning.py:81-104`). The mapped colour
and alpha inputs receive the stack below
(`paintsystem/graph/basic_layers.py:556-559`). The layer is not
clipped automatically: only ADJUSTMENT forces `Clip`
(`paintsystem/data.py:1910`). The group node keeps its bl_idname
across rebuilds, so the extra input values set in the settings box
are captured and restored rather than lost
(`paintsystem/graph/nodetree_builder.py:508-522, 949-980`). They are
lost only when the node is recreated.

**Node editor.** v2 has no "edit group" button. The Shader Editor
panel `NODE_PT_PaintSystemShaderEditor` (NODE_EDITOR, UI, "Paint
System"; poll: an active group and a shader node tree) lists each
non-folder layer with an icon-only `node.add_node` button (icon ADD)
that adds the layer's wrapper tree as a group node, then a
`paint_system.inspect_layer_node_tree` button, icon NODETREE
(`panels/extras_panels.py:408-485`, `:16-23`, `:480-483`).
It enters the material, group, channel and layer wrapper trees, not
the custom group (`operators/shader_editor.py:9-87`).

**Migration.** v1 NODE_GROUP layers become NODE_GROUP layers whose
`custom_node_tree` is the v1 layer's own tree. Sockets are picked by
the names "Color" and "Color Alpha", falling back to the first item
(`operators/versioning_operators.py:135-163`). The deprecated integer
`custom_*` indices (`paintsystem/data.py:1101-1128`) are converted to
names by `migrate_socket_names` (`paintsystem/versioning.py:81-104`).

## v3 design

`PaintSystemCustomGroupLayerNode(PaintSystemLayerNode)`:

- `node_tree: PointerProperty(ShaderNodeTree, poll=not library/artifact)`.
  On assignment `sync_sockets()` creates a Paint System socket on this node
  for every group interface input (except the ones mapped as colour and
  alpha inputs) and stores unlinked values there. These sockets are
  document data: they survive recompiles, copy with the layer, and can be
  linked from other Paint System nodes (a mask node driving a "Strength"
  input, for example).
- Mapping props: `color_input`, `alpha_input`, `color_output`,
  `alpha_output`, enum items read from `node_tree.interface` on demand;
  `NONE` allowed for inputs and alpha output. Colour input (when mapped)
  receives the stack colour, making the group an adjustment; then
  `is_clip` is forced.
- `emit_source`: one `ShaderNodeGroup` instance; inputs are
  `link_or_set` from the node's sockets; outputs registered by mapping.
- `hash_parts`: the group's name plus a content digest computed by
  walking its nodes and unlinked values (cheap for typical groups). Group
  edits made in the node editor are picked up on the next compile trigger;
  a "Refresh" button forces it.
- Interface changes on the group (sockets added/removed) are detected in
  `normalize_tree` by comparing the interface to the node's sockets and
  resyncing, so a stale layer never errors.
- UI: settings box lists the extra sockets with `draw_socket`-style rows
  (value or "linked"), an "Edit Group" button that opens the shader
  editor on the group (`paint_system.focus_node_group`), and the four
  mapping enums.
- Operator `paint_system.new_custom_node_group_layer` with the v2 dialog
  (group picker, auto-mapping, warnings, refusal when no outputs).

## Acceptance

- A group with inputs Color, Alpha, Strength and outputs Color, Alpha maps
  automatically; Strength shows as an editable socket; linking a Value
  mask node to it compiles a link in the artifact.
- Removing a socket from the group interface resyncs without errors.
- Copying the layer copies socket values.
