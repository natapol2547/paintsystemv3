# PS-006 Vector channels: normal and tangent space transforms

Epic A. Size M. Milestone M2.

## v2 behaviour

`Channel.update_node_tree` (`paintsystem/data.py:1735-1802, 1866-1886,
1962-1999`) wraps a VECTOR channel's stack with:

- `vector_type` NORMAL/VECTOR, `input_vector_space`, `vector_space`,
  `output_vector_space`, `bake_vector_space` (OBJECT/WORLD/TANGENT).
- `use_space_transform_input` / `use_space_transform_output`,
  `normalize_input`, `tangent_uv_map`, `disable_output_transform` (set by
  isolate channel).
- Nodes: `ShaderNodeNormalMap`, `ShaderNodeVectorTransform`,
  `.PS Tangent Normal` + `ShaderNodeTangent` for tangent output.
- `default_value` NORMAL/WORLD_POSITION/OBJECT_POSITION injects a
  length-compare fallback (`vector_mix`) so an empty stack outputs the
  geometry normal instead of black.

UI: `draw_channels_settings_panel` (`panels/channels_panels.py:156-211`).

### v2 UI

All paths are relative to `~/paintsystem`. A line number without a file
refers to the file cited last before it in the same bullet or
paragraph, or else in the lead-in line of the enclosing list; when
neither names a file, it refers to `panels/channels_panels.py`. PS-005
and PS-032 describe the enclosing "Channel Settings" section (closed
by default, inside the Channels section of
`MAT_PT_PaintSystemMainPanel`) and the rows shared by every channel
type.

Where it is drawn:

- The VECTOR block of `draw_channels_settings_panel` (`:179-203`). It
  sits in the same aligned, property-split column as "Type", "Color
  Space", "Use Alpha" and the optional "Alpha" slider, after them, only
  when `type == "VECTOR"`.
- While the baked image is in use, the function draws the "Use Baked
  Image" row (drawn whenever a bake image exists, PS-005), then for
  VECTOR only `prop(channel, "bake_vector_space", text="")`, a dropdown
  "World Space" / "Object Space" / "Tangent Space" without icons, and
  returns (`:159-167`). The block below is not drawn then.

Controls, in order:

1. `prop(channel, "default_value", text="Default Value")` (`:180`): a
   dropdown "None", "Normal", "World Position", "Object Position"
   without icons (default NONE; `paintsystem/data.py:2387-2401`).
2. `box = col.box()` with `use_property_split = False`, then
   `header, panel = box.panel("vector_space_settings_panel")`. There is
   no `default_closed`, so it starts open. The header is the label
   "Vector Transform" with no icon (`:181-185`).
3. `panel.row(align=True)` with two toggle buttons (`toggle=1`):
   "Transform Input" (`use_space_transform_input`) and "Transform
   Output" (`use_space_transform_output`) (`:186-188`).
4. `panel.use_property_split = True`, then `prop(channel,
   "vector_type", text="Vector Type", expand=True)`: three buttons
   "Point", "Vector", "Normal", never disabled (`:189-190`).
5. `row(align=True)`, enabled only with Transform Input:
   `input_vector_space` "Input Space" ("World" icon WORLD, "Object"
   icon OBJECT_DATA) (`:191-193`).
6. `row(align=True)`, enabled with either toggle on: `vector_space`
   "Layer Space" ("World" icon WORLD, "Object" icon OBJECT_DATA,
   "Tangent" icon MESH_DATA). Unless the space is Tangent, the same row
   ends with `prop(channel, "normalize_input", text="",
   icon="NORMALS_VERTEX_FACE")`, an icon toggle that the row's
   `enabled` also covers (`:194-198`).
7. `row(align=True)`, enabled only with Transform Output:
   `output_vector_space` "Output Space" (same three items and icons as
   Layer Space) (`:199-201`).
8. When the layer or the output space is Tangent:
   `panel.prop_search(channel, "tangent_uv_map", ps_object.data,
   "uv_layers", text="Tangent UV", icon='GROUP_UVS')` (`:202-203`). The
   toggles do not disable it, and it shows for a Tangent layer space
   even with both transforms off, where it has no effect.

Enum items and icons: `paintsystem/data.py:2402-2444`.

`disable_output_transform` and `bake_uv_map` are drawn nowhere in
`panels/`; the bake operators set `bake_uv_map`
(`operators/bake_operators.py:263, 336`).

Properties (`paintsystem/data.py:2369-2456, 2487-2497`):

- `normalize_input` "Normalize", default False.
- `use_space_transform_input` default True,
  `use_space_transform_output` default False. Both have the
  description "Use space transform for the channel".
- `default_value`: the enum above. Its update rebuilds the group and
  the channel (`:2387-2389`), because it also drives the group
  socket's `hide_value`.
- `vector_type`: POINT, VECTOR, NORMAL; default VECTOR. The paragraph
  above omits POINT.
- `input_vector_space`: WORLD, OBJECT; default WORLD. It has no
  Tangent item, unlike the list above.
- `vector_space` ("Vector Space", "Space used when painting"): WORLD,
  OBJECT, TANGENT; default OBJECT.
- `output_vector_space`: WORLD, OBJECT, TANGENT; default WORLD.
- `tangent_uv_map`: default "UVMap".
- `disable_output_transform`: default True, "For legacy reasons" per
  the code comment.
- `bake_vector_space`: WORLD, OBJECT, TANGENT; default OBJECT; update
  `update_bake_image` (`:2466-2470`).
- The others use `update=update_node_tree`, which rebuilds only the
  channel tree (`:1822`).

Add Channel dialog (details in PS-005): for VECTOR it offers only
"Normalize", and `process_material` passes `vector_space="OBJECT"`
(`operators/channel_operators.py:92, 112-113`). A custom vector channel
therefore has Transform Input on (World to Object), Transform Output
off and Default Value None, so it outputs object-space values. With
"Normalize" ticked they leave colour-encoded, because the input side
encodes them and no output side decodes them. The `vector_space`
keyword only restates the property default (`paintsystem/data.py:2431`).

`vector_transform` helper (`paintsystem/data.py:1724-1804`):

- Arguments: the builder, the target node id and socket
  (`color_name`, `color_socket`), `convert_from`, `convert_to`,
  `normalize_input`, `normalize_output`, `vector_type` and
  `tangent_uv` (default "UVMap"). It inserts a chain whose last node
  feeds the target, and returns the chain's first input, or the target
  itself when no node is needed (`:1799-1804`).
- Steps, in order:
  1. `convert_from == "TANGENT"` forces `normalize_input`
     (`:1746-1748`).
  2. `convert_to == "TANGENT"` forces `normalize_output` and changes
     `convert_to` to WORLD (`:1749-1753`).
  3. `normalize_input`: `ShaderNodeNormalMap` "normal_map" with
     `space = convert_from` and `uv_map = tangent_uv`, Color in, Normal
     out. `convert_from` becomes WORLD (`:1754-1764`). The node decodes
     a 0-1 colour into a world-space unit normal.
  4. `convert_from != convert_to`: `ShaderNodeVectorTransform`
     "vector_transform_output" with `vector_type`, `convert_from` and
     `convert_to` (`:1765-1774`).
  5. `normalize_output` with a Tangent target: `ShaderNodeTangent`
     "tangent" (UV_MAP, `uv_map = tangent_uv`) > Tangent of a
     `.PS Tangent Normal` group node "tangent_normalize". The vector
     enters "Custom Normal" and leaves "Tangent Normal" (`:1776-1788`).
  6. `normalize_output` otherwise: a hidden Vector Math MULTIPLY_ADD
     "normalize" with (0.5, 0.5, 0.5) and (0.5, 0.5, 0.5), which maps
     -1..1 to 0..1 (`:1789-1798`). Despite the name it does not change
     the length.
- `.PS Tangent Normal` (`paintsystem/library2.blend`, inspected in
  Blender 5.2.1): inputs Custom Normal (vector) and Tangent (vector,
  default (1, 0, 0)), output Tangent Normal (colour). With N the
  Geometry Normal and B = N x Tangent, it outputs
  (n.T, n.B, n.N) x 0.5 plus 0.5: the world-space normal expressed in
  the tangent frame and colour-encoded.
- Node ids come from `get_unique_identifier`, so a second chain gets
  "_1" suffixes, and the Normal Map, Vector Transform and group nodes
  are added with `force_properties=True`. The Tangent node is the
  exception: fixed id "tangent" and no forcing (`:1779`). Two effects
  follow (inferred from code). When both sides need it, the second
  `add_node` replaces the first command and both groups share one node
  (`paintsystem/graph/nodetree_builder.py:473`). The builder restores
  captured non-forced properties on rebuild
  (`paintsystem/graph/nodetree_builder.py:1071-1086`), so a later
  Tangent UV change reaches the Normal Map node but not an existing
  Tangent node.

Channel graph for VECTOR (`Channel.update_node_tree`,
`paintsystem/data.py:1865-2004`):

- Output side, built first (`:1872-1885`), only when Transform Output
  is on and `disable_output_transform` is off: `vector_transform` in
  front of Group Output Color, from `bake_vector_space` when the baked
  image is in use and `vector_space` otherwise, to
  `output_vector_space`, with `normalize_input =
  channel.normalize_input`, `normalize_output = False`, and UV
  `bake_uv_map` when baked, `tangent_uv_map` otherwise.
- Baked image in use: an Image Texture "bake_image" (interpolation
  Closest) read through a UV Map node with `bake_uv_map`. Its Color
  feeds that chain (or Group Output), its Alpha goes straight to Group
  Output Alpha, and the build returns, with no input side
  (`:1887-1895`). The two nodes are also added when a bake image exists
  but is not in use; the image output then stays unused.
- The layers are wired next (`:1897-1964`).
- Input side (`:1966-1979`), only when Transform Input is on:
  `vector_transform` in front of the bottom of the stack, from
  `input_vector_space` to `vector_space`, with `normalize_input =
  False`, `normalize_output = channel.normalize_input` and UV
  `tangent_uv_map`.
- Default value fallback (`:1980-1999`). It is nested inside the input
  branch, so it exists only with Transform Input on and Default Value
  not None:
  - "vector_length": Vector Math LENGTH of Group Input Color.
  - "compare": Math COMPARE against 0 with epsilon 0. EEVEE's
    `math_compare` raises the epsilon to at least 1e-5 (GLSL source
    embedded in the Blender 5.2.1 binary), so the result is 1 for a
    zero or near-zero vector and 0 otherwise.
  - "vector_mix": Mix (VECTOR), Factor from "compare", A = Group Input
    Color, B = Geometry Normal (Normal), Geometry Position (World
    Position) or Texture Coordinate Object (Object Position). The
    result feeds the input chain.
  - An unlinked group input is the zero vector, because `hide_value`
    hides its field and v2 never sets it (PS-005). The substitute sits
    under the layers, so it shows wherever they leave it uncovered, not
    only when the stack is empty as the paragraph above says.
- Group Input Color > first node of the input side (`:2000`).
- Full colour path: Group Input Color > [fallback mix] > [input
  transform] > layers > [output transform] > Group Output Color. The
  alpha path is as in PS-005.
- What `normalize_input` means: layers work on colour-encoded vectors.
  The input side encodes them (MULTIPLY_ADD, or the tangent group) and
  the output side decodes them with a Normal Map node, which also
  normalises the result. A Tangent layer space always takes this path,
  which is why the panel hides the toggle for it.
- Group socket: VECTOR channels get `NodeSocketVector` with
  `hide_value` when Default Value is not None
  (`paintsystem/data.py:2592-2594`). The channel tree has only Color
  and Alpha sockets (`:1828-1832`), so the vector converts implicitly
  at the group links. The list row draws no value for vector inputs
  (`panels/channels_panels.py:59-63`).

Normal template (`paintsystem/data.py:2739-2750`):

- `create_channel("Normal", "VECTOR", use_alpha=False,
  normalize_input=True, color_space="NONCOLOR", default_value="NORMAL",
  use_space_transform_input=True, use_space_transform_output=True)`.
  The rest keeps the property defaults: Vector Type Vector, Input
  World, Layer Object, Output World, Tangent UV "UVMap".
  `create_channel` sets `disable_output_transform = False` (`:2670,
  2680`).
- It moves the BSDF Normal link into the group input, or copies the
  BSDF value (`utils/nodes.py:50-72`), and wires Group Output Normal to
  the BSDF Normal (`paintsystem/data.py:2742-2746`). This runs only when
  the group node and a Principled or Diffuse BSDF are found
  (`paintsystem/data.py:2697-2700`).
- Resulting graph: Group Input Normal > vector_mix (B = Geometry
  Normal) > Vector Transform (Vector, World to Object) > MULTIPLY_ADD >
  layers > Normal Map (Object space, "UVMap") > Group Output Normal. An
  empty stack gives back the shading normal.
- Layers: Geometry "Normal" (OBJECT_NORMAL, `normalize_normal=True`)
  when no link was moved, then Image "Image". The NORMAL branch has no
  `return`, so the call returns None (`:2739-2752`).
- New geometry layers made by `paint_system.new_geometry_layer` copy
  the channel's `normalize_input` into `normalize_normal` on VECTOR
  channels and pass False on others
  (`operators/layers_operators.py:312-342`, the rule at `:335`).

`disable_output_transform` in practice:

- The True default applies to channels saved before the property
  existed. Channels made by `create_channel` start False
  (`paintsystem/data.py:2451-2456, 2670, 2680`).
- Isolate channel sets it True while isolating and False on restore,
  and switches the view transform to "Standard"
  (`paintsystem/data.py:2543-2544, 2553`). The preview therefore shows
  the layer-space values, colour-encoded when Normalize is on or the
  layer space is Tangent. Isolate works on the active channel only
  (`:2525`).
- Selecting another channel while isolated calls `isolate_channel`
  twice on the new channel (`paintsystem/data.py:2647-2653`). The
  previously isolated channel is never reset and keeps its output
  transform off (inferred from code).
- A non-tangent bake sets it True for the bake
  (`paintsystem/data.py:2161-2163`, restored at `:2269, 2284`). The
  image stores layer-space values, and Bake Channel then sets
  `bake_vector_space = vector_space` when it bakes into the channel's
  own image; "As Layer" bakes record nothing
  (`operators/bake_operators.py:230-253, 277-281`). A tangent bake
  instead sets
  `output_vector_space = "TANGENT"` and `tangent_uv_map = bake_uv_map`
  for the bake (`paintsystem/data.py:2156-2160`), so it relies on
  Transform Output being
  on and `disable_output_transform` off, and Bake Channel records
  TANGENT (`operators/bake_operators.py:277-280`). Bake Channel's
  `invoke` presets "As Tangent Normal" from `bake_vector_space`
  (`:207-210`). Bake All Channels bakes without the tangent option and
  never sets `bake_vector_space` (`:324-338`), so the old value stays
  (inferred from code).

## v3 design

- Add the properties above to `PaintSystemChannel`. Each `update=` marks
  the tree dirty.
- New `compiler/vector.py` with `emit_vector_input(ctx, channel, ref)` and
  `emit_vector_output(ctx, channel, ref)` called by the Group Input and
  Group Output emitters for VECTOR channels. Roles
  `"<channel uuid>:vin_*"` / `":vout_*"`.
- `disable_output_transform` is not stored on the channel. Preview Channel
  (PS-061) is a compile option on the tree (`tree.preview_channel`), and
  its Group Output emitter can pick what a vector channel shows, so the
  artifact reflects preview state without mutating channel data.
- Geometry layers writing normals use `normalize_normal` (PS-027) to match
  `normalize_input`.

## Acceptance

- Test: normal channel with a solid (0.5,0.5,1) layer and tangent output
  produces the same vector as v2 on a UV sphere (compare a baked pixel).
- Test: switching spaces patches nodes in place without artifact churn.
