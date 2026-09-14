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
