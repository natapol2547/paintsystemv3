# PS-006 Vector channels: normal and tangent space transforms

Epic A. Size M. Milestone M2.

## Status

Done: Holds, Paint In and the tangent UV map, with the conversions in
`compiler/vector.py` and the normal map inverse and tangent groups in
`compiler/library.py`. The v3 design below describes the code as it is. Bakes (`bake_vector_space`)
are PS-007, and geometry layers that write normals are PS-027.

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

One choice replaces v2's three spaces and two toggles. Every shader
socket that takes a vector (a BSDF's Normal, Bump, Displacement) expects
world space, so the material's input and output are always world-space
vectors, and the user picks only the space the layers paint in. That
removes v2's states whose output the material cannot read, such as
Normalize with Transform Output off, which leaves colour-encoded values.

Properties of `PaintSystemChannel` (`props/channel.py`), all with
`_on_channel_changed`, so a change recompiles the tree:

- `vector_kind` "Holds": Normals or Vectors, default Normals. It replaces
  `vector_type` and `normalize_input`.
  - Normals are unit vectors, stored as the colours of a normal map, so
    a normal map image painted or loaded into a layer is right as it is.
    An unlinked material input stands for the shading normal, so an empty
    stack gives the surface back as it is, and the interface input hides
    its value (`hide_value`). Hiding the field does not clear a value
    typed while the channel held Vectors, so switching to Normals resets
    each material's unlinked input to (0, 0, 0) (`_on_vector_kind_changed`).
  - Vectors are stored as they are, and the interface input shows its
    value, which the stack starts from. Every other channel sets
    `hide_value` off, so a socket that stops holding normals shows its
    value again. The Group Input's hash leaves `hide_value` out, since it
    changes no value.
  - v2's Point is dropped. It differs from Vector only in Object space,
    by the object's location, and painting positions is rare. It can come
    back as a third kind.
- `paint_space` "Paint In": Tangent, Object or World, default Tangent,
  the space of most normal maps.
- `tangent_uv_map` "UV Map", used by Tangent only. Empty means the mesh's
  active render UV map, which is also what a layer without a UV map of
  its own paints on. v2's default was the name "UVMap".
- No `default_value`. Normals fall back to the shading normal, and
  Vectors start from the input's value. v2's World and Object Position
  defaults are dropped with Point.
- No `disable_output_transform`. Preview Channel (PS-061) is a compile
  option, and its Group Output emitter shows the layer values as the
  layers hold them: normals as normal map colours, flat blue for an
  empty tangent-space stack.

Compiler (`compiler/vector.py`):

- `from_world(ctx, owner, role, channel, vector)` turns a world-space
  vector into a layer value, and `to_world` reads one back. Both return
  anything but a vector channel as it is. The nodes are *owner*'s, with
  roles that start with *role*.
- The compiled group's inputs are read through `CompileContext
  .channel_base`, one `NodeGroupInput` (IR id `group:in`) for the whole
  tree. For a vector channel it calls `input_value`, whose nodes belong
  to the channel (`<channel uuid>:input:*`), so a Group Input and a
  flattened Group Output share them.
- The Group Output calls `to_world` for each channel
  (`<node uuid>:out:<socket identifier>:*`). The flatten of a channel
  without alpha happens before it, in layer values.
- Unlinked normal input: Vector Math LENGTH, Math LESS_THAN 1e-5 and a
  Mix (VECTOR) with the Geometry Normal, as v2's `vector_mix`. An
  unlinked input reads (0, 0, 0): that is its default, its field is
  hidden, and switching to Normals clears a value typed before.
- Normals are read back with a Normal Map node set to the paint space
  and, in Tangent, the channel's UV map. It is how Blender reads a normal
  map, and it normalises, so a painted layer looks as its image would
  through a plain Normal Map node.
- Normals are encoded by the library group "Normal Map Inverse"
  (`normal_map_inverse_group`), which gives the colour that node reads as
  a given world-space normal. Four more Normal Map nodes, set up as the
  decoder, read +X, +Y, +Z and (1, 1, 1) (`NORMAL_MAP_PROBES`). In both
  engines and every space the node reads a colour c as
  normalize(F (2c - 1)), with F a matrix of the point being shaded: the
  UV map's tangent frame, the object's matrix, or none, turned round on a
  back face where the engine does that. The first three readings are F's
  columns, normalised, and the fourth is their sum, normalised, which
  recovers the lengths the first three lost. Part i of the colour is
  N . R_i over XYZ . R_i, where R_i is the cross product of the other two
  readings; that is F^-1 N up to a scale the normalising cancels, along
  with the sign of F's determinant. Where the readings span no volume
  (|det| < 1e-4), the colour is flat (0.5, 0.5, 1).
  - Why: the node's F differs between engines and cases, and copying it
    fails somewhere. A first version encoded through the tangent groups
    and Vector Transform NORMAL, and it disagreed with the decoder on
    back faces and on unevenly scaled objects in Tangent space (up to
    0.24 in Cycles on a stretched mesh). Inverting the node itself is
    exact on back faces, mirrored UV maps and unevenly or negatively
    scaled objects, in Cycles and EEVEE (scratch probes, see
    Acceptance).
  - Switching spaces changes only the `space` and `uv_map` of the five
    Normal Map nodes, so the compiled tree is patched in place.
- Vectors in Object space: a Vector Transform (VECTOR) between World and
  Object, so they turn and scale with the object.
- Vectors in Tangent space: the library groups "World To Tangent" and
  "Tangent To World" (`compiler/library.py`), built in Python, so PS-002
  is not needed. A group cannot pick a UV map, so the compiled tree feeds
  each one a Tangent node (UV_MAP) and a Normal Map node (Tangent) reading
  pure +Y, both on the channel's UV map. The frame (`_tangent_frame`) is
  T, the tangent made perpendicular to the shading normal (EEVEE's
  Tangent node is not); N, the shading normal turned round on a back
  face, where it faces the viewer; and B, the shading normal x T, turned
  round where the probe says the bitangent points the other way (a
  mirrored UV map) and on a back face. So a vector means the same seen
  from either side, and the frame is orthonormal, so the two groups undo
  each other. v2's `.PS Tangent Normal` has no mirroring sign, so on a
  mirrored half its encoding and the Normal Map node's decoding disagree
  (inferred from the group's math).
- Group layers: the tree a group layer wraps takes and gives world-space
  vectors, and a stack in the parent holds the parent channel's layer
  values. So the group layer calls `to_world` before the wrapped tree and
  `from_world` after it, on the parent's vector channel that each output
  feeds, not the one of its name (`_outer_channels`). That channel comes
  from `stack_ops.output_channel`, which follows the stack up through
  layers, folders and group layers to the active Group Output, the way
  the compiler reads it. `channel_of`, which filter layers use, is built
  on it. Its hash includes those channels' settings.
- Hashes: `space_settings(channel)` (kind, space and, in Tangent only,
  the UV map) is part of the Group Input's `hash_parts`, keyed by socket
  identifier, so a layer cache above the input goes stale when they
  change, and part of a group layer's.
- Layer caches (`bake_node_cache`): a vector channel's cache is a float
  image in Non-Color, since Vectors can be negative, which a byte image
  clamps to 0, and encoded normals keep more precision. The values are
  data, so the cache is Non-Color whatever colour space the channel's
  layers paint in. A cache baked before the channel changed type is
  replaced, and the old image is removed when nothing else uses it. Other
  channels' caches stay byte images, now in the channel's colour space
  (`image_colorspace`) rather than always sRGB, so the cache of a Float
  channel in Non-Color is Non-Color too.
- Filter layers refuse any channel that is not a colour channel
  (`resolve_input`), so they never see vector values.

UI (`panels/main_panels.py`): Channel Settings shows, for a vector
channel, "Holds" as two buttons, "Paint In", and in Tangent the UV map
search (`draw_uv_map`, the layers' field). The channel list row shows no
value for a vector channel, since three fields do not fit.

## Known gaps

- A colour property cannot be negative, so a Solid Color layer cannot
  paint a Vectors value with a negative component. Normals are encoded,
  so they are not affected, and a blend such as Subtract can still take
  a Vectors value below 0.
- Filter layers refuse vector channels. Their results are byte images,
  which would clamp Vectors below 0 and lose precision on normals, so
  allowing them needs float results first.
- A tangent UV map the mesh does not have (a typo, or a UV map renamed
  after it was picked, since the name is not followed): in Cycles the
  Tangent node gives 0 and the Normal Map node the plain normal, so the
  inverse group finds no volume and encodes flat. A linked normal input
  is then lost and the shading normal comes out; a painted layer still
  matches a plain Normal Map node on that name, and Tangent-space Vectors
  lose their T and B. EEVEE falls back to a UV map it has. A mesh with no
  UV maps at all gets Blender's generated tangents in both engines, so it
  keeps a frame.
- Switching a channel's type to Vector keeps its Use Alpha and colour
  space (PS-005), so a vector channel made from a colour channel has an
  alpha socket and paints new images in sRGB until those are changed.
  A new Vector channel starts with both right (`channel_defaults`), and
  so will the channel templates of PS-041.

## Acceptance

`tests/test_vector_channels.py` bakes with Cycles on a curved, rotated
mesh whose tangent UV map is mirrored on one half, so both handednesses
are checked, and renders the back faces with an orthographic Cycles
camera behind the mesh (`render_back`). It compares the compiled shader
with Blender's own nodes, not with v2, which disagrees with them on
mirrored UVs.

- An empty stack gives the linked normal back in all three spaces, on
  the front and the back faces, and on an unevenly scaled object in all
  three spaces.
- A painted normal equals a Normal Map node of that colour in each
  space, on both sides.
- Vectors pass through an empty stack, and a painted vector points where
  a normal map of it points (Tangent) or turns and scales with the object
  (Object). A tangent-space (0, 0, 1) is the surface's own normal,
  (1, 0, 0) the UV tangent and (0, 1, 0) runs along the UV map's V, seen
  from either side.
- A group layer converts between a parent in Tangent and a child in
  Object, and its hash follows the parent's space. A group layer whose
  Normal sockets are wired into the parent's Bump stack converts on Bump,
  so a normal its tree paints comes out as painted.
- A cache of a Vectors stack below 0 is a float Non-Color image, even
  with the channel in sRGB, and gives the same values as the live stack.
- Switching a channel to Normals clears a typed input value and hides the
  field; switching it to another type shows the field again.
- The preview of an empty tangent-space stack is flat (0.5, 0.5, 1), on
  the channel's UV map and on one the mesh does not have.
- Switching spaces patches the same Normal Map node, and the kind, the
  space and the tangent UV map each change a cache's hash above the
  input.

Scratch probes, not in the suite, rendered the same round trips on both
sides in Cycles and EEVEE on Blender 4.2, 4.5, 5.0, 5.1, 5.2 and 5.3,
including unevenly scaled objects, and on negatively scaled objects on
4.2 and 5.2. The largest error was below 0.0001 everywhere but the
missing UV map above.
