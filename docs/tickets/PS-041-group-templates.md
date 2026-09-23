# PS-041 Group templates and channel templates

Epic E. Size L. Milestone M1.

## Status

Done: the Add Paint System dialog with five material templates, its
recommendation and live summary (`templates.py`,
`ops/node_tree_ops.py`), the Add Channel menu with four channel templates
(`ops/channel_ops.py`, `panels/main_panels.py`), and reconnecting a
material whose group node was deleted. The v3 design below describes the
code as it is. Several trees per material stay PS-040, and undoing a
template stays PS-042.

## v2 behaviour

`paint_system.new_group` (`operators/group_operators.py:73-380`),
`TEMPLATE_ENUM` (`data.py:91`):

- BASIC "Blank Canvas" (IMAGE): one COLOR channel with alpha; optional
  Solid Color and Image layers; group -> Mix Shader <- Transparent BSDF
  -> new Material Output (`create_basic_setup`, `:35`). Forces the scene
  view transform to Standard.
- PAINT_OVER (`paintbrush` icon): EEVEE only. Splices into whatever feeds
  the Material Output: shader socket -> Shader to RGB then the group;
  colour socket -> group directly; sets `Color Alpha` input to 1.0 and
  routes through a new Mix Shader.
- PBR (MATERIAL): finds/creates a Principled, shifts upstream nodes left,
  inserts the group before it, adds channel templates for the checked
  boxes (Color / Metallic / Roughness / Normal; Color + Normal default on).
- NORMAL (NORMALS_VERTEX_FACE): group + Diffuse BSDF + Material Output,
  NORMAL channel template.
- NONE: bare COLOR channel + image layer, group parked to the right.

Material prep: `use_nodes`, optional `BLEND`, optional backface culling +
`show_transparent_back = False`. `invoke` auto-selects PAINT_OVER when
`node_tree_has_complex_setup` (`:60`, anything but a lone Principled)
under EEVEE, leaves EDIT mode, uniquifies the name. `TEMPLATE_ENUM` drops
PAINT_OVER outside EEVEE.

`CHANNEL_TEMPLATE_ENUM` (`data.py:214`): COLOR, METALLIC, ROUGHNESS,
NORMAL. `add_channel` with a template skips the dialog and wires the
channel output to the matching Principled input.

### v2 UI

All paths are relative to `~/paintsystem`. Line numbers without a file
refer to `operators/group_operators.py`. "Scaled ... through
`scale_content`" below means the scale is set only while the
`use_compact_design` preference is off (`operators/common.py:24-30`).

Where the dialog is reached. Every entry calls `paint_system.new_group`
with no preset properties:

- Main panel body with no active group (`panels/main_panels.py:202-207`):
  a `row()` with `scale_x = 2` and `scale_y = 2`, set directly so
  compact design does not shrink it, holding "Add Paint System" (ADD).
  The draw returns after it. Objects that are not meshes return earlier
  (`panels/main_panels.py:175-176`) and never see it. An object without
  a material also lands here, and its header draws nothing because
  `ps_mat_data` is None (`panels/main_panels.py:128-129`).
- Main panel header preset (`panels/main_panels.py:124-137`): an ADD
  button with `text=""`, with or without groups. See PS-031.
- `MAT_PT_PaintSystemMaterialSettings` popover
  (`panels/main_panels.py:107-115`): ADD in the "Paint System Node
  Groups:" box when groups exist. The popover is only opened from the
  legacy UI material row.
- Properties editor, Material tab: `draw_paint_system_material`
  (`panels/extras_panels.py:487-498`) is appended to
  `EEVEE_MATERIAL_PT_context_material` (`panels/extras_panels.py:513`).
  When groups exist it draws a box with "Paint System Node Groups:"
  (`sunflower`) and a `row(align=True)` scaled 1.3 x 1.2 through
  `scale_content`: the groups popover (NODETREE), the group `name`,
  ADD, and `paint_system.delete_group` (REMOVE) called directly instead
  of through the delete menu. That panel's `COMPAT_ENGINES` are EEVEE and
  Workbench (checked in Blender 5.2), so the box is absent under Cycles.
- A template select menu with one `new_group` item per template is
  commented out (`panels/main_panels.py:49-58`).
- The main panel header draws its ADD for any `ps_object` with a
  material, including a grease pencil object on Blender 4.3+
  (`paintsystem/context.py:36-49`). With `add_layers` on, the dialog
  draw then fails in `select_coord_type_ui`, which reads
  `ps_object.data.uv_layers` without a type check
  (`operators/common.py:181, 198`); grease pencil data has no
  `uv_layers` (checked in Blender 5.2).

Operator (`:73-381`): `PAINTSYSTEM_OT_NewGroup`, bl_label "New Group",
docstring "Create a new group in the Paint System", bl_options REGISTER
and UNDO. Its bases are `PSContextMixin`, `PSUVOptionsMixin` and
`MultiMaterialOperator`. It does not use `PSImageCreateMixin`, so the
dialog has no image name, resolution, UDIM or float options. Poll:
`ps_object is not None` (`:144-147`).

Properties (`:85-142`). The operator's own properties are not
SKIP_SAVE, so Blender reuses the last values within a session:

- `template` "Template". Items come from `get_templates` (`:79-83`):
  all of `TEMPLATE_ENUM` (`paintsystem/data.py:91-97`) when "EEVEE" is a
  substring of the render engine, else the same list without
  PAINT_OVER. There is no default, so BASIC applies until one is picked.
  Items with descriptions: BASIC "Blank Canvas" (IMAGE, "Blank canvas
  painting setup"), PAINT_OVER "Paint Over" (custom `paintbrush`,
  "Paint over the existing material"), PBR "PBR" (MATERIAL, "PBR
  painting setup"), NORMAL "Normals Painting" (NORMALS_VERTEX_FACE,
  "Start off with a normal painting setup"), NONE "None" (NONE, "Just
  add node group to material").
- `group_name` "Group Name", default "New Group".
- `use_alpha_blend` "Use Alpha Blend", default False.
- `disable_show_backface` "Disable Show Backface", default True.
- `set_view_transform` "Use Standard View Transform", default True.
- `pbr_add_color` True, `pbr_add_metallic` False, `pbr_add_roughness`
  False, `pbr_add_normal` True.
- `add_layers` "Add Layers", default True.
- From `PSUVOptionsMixin` (`operators/common.py:89-125`), all
  SKIP_SAVE: `use_paint_system_uv` (default True; turning it on sets
  `coord_type` to AUTO, turning it off sets AUTO back to UV),
  `coord_type` (`COORDINATE_TYPE_ENUM`, default UV; picking AUTO turns
  `use_paint_system_uv` on), `uv_map_name` and `checked_coord_type`.
- From `MultiMaterialOperator` (`operators/common.py:32-42`), never
  drawn: `multiple_objects` True, `multiple_materials` False.

`invoke` (`:311-323`), in order:

1. `group_name` becomes "New Group" when the material has no groups.
   Otherwise `get_next_unique_name` checks the last used name against
   the group names: a free name is kept, a taken one becomes its
   non-digit prefix plus " <n>", where n is one more than the highest
   suffix already used with that prefix (`utils/__init__.py:3-37`).
2. `get_coord_type` (`operators/common.py:151-172`) sets `uv_map_name`
   to the mesh's first UV map. If the preference
   `preferred_coord_type` (`panels/preferences_panels.py:49-58`: AUTO,
   UV or UNDETECTED, default UNDETECTED) is AUTO or UV, it sets the UV
   toggle and `coord_type` from it. Otherwise, with an active channel,
   the active group's `coord_type` and `uv_map_name` are reused. On a
   first run neither applies, so the dialog opens with AUTO UV on.
3. PAINT_OVER is preselected when there is an active material, the
   engine name contains "EEVEE" and `node_tree_has_complex_setup`
   (`:60-70`) is true. It collects every
   node upstream of the active Material Output (`utils/nodes.py:9-34`):
   none is false, more than one is true, and a single node is true
   unless it is a Principled BSDF. A Principled fed by an image texture
   therefore counts as complex. Otherwise `template` keeps its last
   value.
4. Edit mode is left for Object mode.
5. `invoke_props_dialog(self, width=300)`. The title is the bl_label
   "New Group".

Dialog (`draw`, `:325-381`), top to bottom. The draw never sets
`use_property_split`:

1. `multiple_objects_ui` (`operators/common.py:83-86`): with more than
   one selected object, a box with "Applying to all selected objects"
   (INFO).
2. When the engine name lacks "EEVEE", the label "Paint Over is not
   supported in this render engine" (ERROR), whatever the template.
3. A `row()` scaled 1.5 x 1.5 through `scale_content`: `template` with
   `text="Template"`, drawn as a dropdown (no `expand`).
4. PBR only (`:337-350`): a box. A row with `alignment = "CENTER"`
   holds "PBR Channels:" (MATERIAL). A `column(align=True)` follows
   with the toggles `pbr_add_color` "Color" (`color_socket`),
   `pbr_add_metallic` "Metallic" (`float_socket`), `pbr_add_roughness`
   "Roughness" (`float_socket`) and `pbr_add_normal` "Normal"
   (`vector_socket`).
5. A `row()` scaled 1.5 x 1.5 through `scale_content`: `add_layers`
   "Add Template Layers" (`layer_add`), for every template.
6. With `add_layers`, a box with `select_coord_type_ui`
   (`operators/common.py:174-200`):
   - A `row(align=True)` with the label "Coordinate System"
     (`transform`) and `use_paint_system_uv` "Use AUTO UV?" drawn as a
     toggle button.
   - AUTO UV on: a box. When the mesh has no "PS_UVMap" UV map
     (`paintsystem/graph/common.py:15`) the box is alert and reads "Will
     create a new UV Map: PS_UVMap" (ERROR). Otherwise it reads "Using
     UV Map: PS_UVMap" (INFO). Nothing else is drawn.
   - AUTO UV off: `coord_type` with `text=""`, a dropdown of
     `COORDINATE_TYPE_ENUM` (`paintsystem/data.py:150-162`): Auto UV,
     UV, Object, Camera, Window, Reflection, Position, Generated,
     Decal, Projection, Parallax. A choice other than UV adds an alert
     box with an aligned column: "Painting in 3D may not work" (ERROR)
     and "Open Blender Image Editor to paint" (BLANK1). UV adds a
     `row(align=True)` with `prop_search` of `uv_map_name` over the
     mesh `uv_layers` (`text=""`), alert while the name is empty.
7. A box with `box.panel("advanced_settings_panel",
   default_closed=True)`. Header: "Advanced Settings:"
   (TOOL_SETTINGS). Body:
   - `split(factor=0.4)` with the label "Group Name:" and `group_name`
     (`text=""`, NODETREE).
   - BASIC only: `use_alpha_blend` "Use Smooth Alpha". When it is on,
     an alert box follows. Its row holds an ERROR icon label and an
     aligned column: "Warning: Smooth Alpha (Alpha Blend)" / "may
     cause transparency artifacts.". Then `disable_show_backface`
     "Use Backface Culling".
   - BASIC only, when the scene view transform is not Standard:
     `set_view_transform` "Use Standard View Transform".

Execution. `MultiMaterialOperator.execute` (`operators/common.py:43-77`)
collects `ps_object` and, with `multiple_objects`, every selected mesh
except one named "PS Camera Plane". It calls `process_material` once per
distinct active material, inside a `temp_override` that makes that
object active and the only one selected. An object without materials
is still processed. It always returns FINISHED. Its warning "Completed
with N error(s)" never appears for this operator: it counts a call as
failed when the return value is falsy, and both {'FINISHED'} and
{'CANCELLED'} are truthy (`operators/common.py:62-75`).
`process_material` (`:149-309`):

1. Without an active material, a new material "<group_name> Material"
   is made and set as the active material (`:152-154`).
2. Material prep (`:157-169`): `use_nodes = True`. `blend_method =
   'HASHED'` below Blender 4.2 is dead code, because the manifest
   requires 4.2.0 (`blender_manifest.toml:20`). `use_alpha_blend` sets
   `blend_method = 'BLEND'` for any template, although only BASIC draws
   it (Blender 5.2 maps this to `surface_render_method` BLENDED).
   `disable_show_backface` sets `show_transparent_back = False` and
   `use_backface_culling = True`, also for every template. BASIC with
   `set_view_transform` sets the scene view transform to Standard.
3. A new ShaderNodeTree goes to `MaterialData.create_new_group`
   (`paintsystem/data.py:2960-2972`). It clears the tree's nodes,
   appends a group through `ListManager` (the group becomes active),
   sets the name and tree, and builds the tree. `Group.template` is set
   to the chosen template (`:175`). It is read later by delete
   (`:431`, PS-042) and by the Normal tip (`panels/common.py:295`).
4. Coordinates (`:179-181`): without `add_layers`, `coord_type` is
   set to UV. `store_coord_type` (`operators/common.py:134-149`) then
   turns AUTO UV on into `coord_type` AUTO with `uv_map_name`
   "PS_UVMap". The toggle is not cleared first, so with `add_layers`
   off and AUTO UV on (the hidden state from `invoke`) the group still
   gets AUTO. It stores the preference `preferred_coord_type` (AUTO, or
   UV when UV is chosen; other types leave it) and copies `coord_type`
   and `uv_map_name` onto the new group.
5. The template branch below, then a panel redraw (`:308`).

Template graphs. The right-most node is the one with the largest x
(`:50-57`). Setting `is_active_output` on a new Material Output clears
the flag on the old one (checked in Blender 5.2). The old output and
the nodes feeding it, such as the default Principled BSDF, stay in the
tree but are no longer connected to the active output.

- BASIC (`:185-201`): channel "Color" (COLOR, `use_alpha=True`). With
  `add_layers`: a "Solid Color" layer (a white RGB node,
  `paintsystem/graph/basic_layers.py:437-440`), then an "Image" layer.
  Layers are inserted at the cursor, so "Image" sits above "Solid
  Color" and is active (`paintsystem/nested_list_manager.py:69-82`).
  `create_basic_setup` (`:35-47`) adds the group node at the right-most
  node + (width + 50, 0), or at the origin in an empty tree, a Mix
  Shader at group + (200, 0) and a Transparent BSDF at group + (0, 100).
  Links: group output 0 "Color" -> Mix input 2 (the second Shader),
  group output 1 "Color Alpha" -> Mix Fac, Transparent -> Mix input 1.
  A new Material Output at Mix + (200, 0) is made active and receives
  Mix -> Surface. The colour reaches the shader socket through
  Blender's implicit colour-to-shader conversion, so the result is
  unlit.
- PAINT_OVER (`:237-279`): outside EEVEE it reports "Paint Over is only
  supported in EEVEE" and cancels. The dialog cannot offer PAINT_OVER
  there, because `get_templates` drops it. If the branch is reached, the
  group created at `:171-175` is left behind with no channels. Channel
  "Color" with alpha, and with
  `add_layers` only an "Image" layer. When the active Material Output's
  Surface input has a link:
  - Shader source (`find_base_socket_type` is NodeSocketShader):
    `create_basic_setup` at the source node + (width + 250, 0), and a
    Shader to RGB at source + (width + 50, 0). Links: source -> Shader
    to RGB -> group input "Color". A commented line would also have
    fed the Shader to RGB alpha into "Color Alpha" (`:268`).
  - Any other socket type: `create_basic_setup` at source + (width +
    50, 0), and source -> group input "Color".
  - In both cases the group input "Color Alpha" is set to 1.0. The
    existing Material Output moves to Mix + (200, 0), is made active,
    and Mix -> Surface replaces the old link.
  When Surface has no link, the channel and layer are made but no group
  node is added. The group's "Color" input feeds the bottom of the
  layer stack (`paintsystem/data.py:2000-2003`), so the layers paint
  over the rendered shader.
- PBR (`:202-235`): the first Principled BSDF in the part of the tree
  connected to the active output is used (`find_node`,
  `utils/nodes.py:79-114`, searching both directions). If there is
  none, a new one is placed at the output + (-200, 0). Every node
  upstream of the Principled moves 200 left, and the group node is
  placed at Principled + (-200, 0). A Principled whose BSDF output has
  no link, such as the new one, is linked to Surface, replacing any
  existing Surface link. Then `create_channel_template` runs for
  each checked box in the order Color, Metallic, Roughness, Normal, and
  the active channel index is set to 0.
- NORMAL (`:281-296`): the group node at the right-most node + (200, 0),
  a Diffuse BSDF at group + (200, 0), a new Material Output at Diffuse
  + (200, 0), made active, and Diffuse -> Surface. Then
  `create_channel_template("NORMAL")`. The old Principled is no longer
  connected to the active output, so the template wires the Diffuse
  node's Normal input.
- NONE (`:297-307`): channel "Color" with alpha, an "Image" layer with
  `add_layers`, and a group node at the right-most node + (200, 0) that
  is not linked to anything.
- Unlike BASIC, NORMAL and NONE do not handle an empty material tree:
  `get_right_most_node` returns None and `.location` raises
  (`:285, 306`).

Image layers from these paths are created by `Channel.create_layer`
(`paintsystem/data.py:2049-2056`) with the `create_ps_image` defaults
(`paintsystem/data.py:541-552`): 2048 x 2048, alpha, transparent black,
no float buffer. With UV coordinates, UDIM tiling is used when the UV
map has tiles other than 1001. AUTO layers get a plain image, and the
layer build creates "PS_UVMap" with Smart UV Project (angle limit 30
degrees, island margin 0.005) when the mesh lacks it
(`paintsystem/data.py:554-589, 836-837`).

Channel templates (`Group.create_channel_template`,
`paintsystem/data.py:2688-2752`). The items of `CHANNEL_TEMPLATE_ENUM`
(`paintsystem/data.py:214-219`) are "Color" (`color_socket`),
"Metallic" and "Roughness" (`float_socket`) and "Normal"
(`vector_socket`). The target node is the first Principled BSDF
connected to the active output, else the first connected Diffuse BSDF.
The group node is looked up connected first, then anywhere
(`paintsystem/data.py:2563-2567`). Each wiring step moves the target
socket's link, or copies its value when unlinked, onto the channel's
group input ("Color" for "Base Color", "Color Alpha" for "Alpha",
otherwise the same name; `transfer_connection`,
`utils/nodes.py:50-72`), then links the group output to the target
socket. Nothing is wired when either node is missing.

- COLOR: channel "Color", COLOR, `use_alpha=True`. Wires "Base Color",
  or "Color" on a Diffuse, and "Alpha" through "Color Alpha". Without
  an Alpha socket the channel's `use_alpha` is turned off. With
  `add_layers`, an "Image" layer.
- METALLIC and ROUGHNESS: channel "Metallic" or "Roughness", FLOAT,
  `use_alpha=False`, `use_max_min=True` (factor 0 to 1), NONCOLOR.
  Wires the socket of the same name. No layers, whatever `add_layers`
  says.
- NORMAL: channel "Normal", VECTOR, `use_alpha=False`,
  `normalize_input=True`, NONCOLOR, `default_value='NORMAL'`, and space
  transforms on input and output. The painting space OBJECT and the
  output space WORLD are the property defaults
  (`paintsystem/data.py:2423-2444`). Wires "Normal". With `add_layers`:
  a Geometry layer "Normal" (OBJECT_NORMAL, `normalize_normal=True`),
  unless an existing link into the target's Normal input was moved onto
  the group, then an "Image" layer above it. This branch returns None;
  the others return the channel.
- `paint_system.add_channel` with a template
  (`operators/channel_operators.py:79-104`) runs `execute` straight
  from `invoke` and uses the default `add_layers=True`. Adding Color or
  Normal from the channels list therefore also adds their layers. The
  add menu lists only the templates whose label is not already a
  channel name (`panels/channels_panels.py:84-88, 240-252`).

Group tree (`Group.update_node_tree`, `paintsystem/data.py:2569-2618`):
it is named "PS <group> (<material>)". The interface lists the outputs,
then the inputs. Each channel gets a socket of its type named after it
and, with `use_alpha`, a 0 to 1 factor "<name> Alpha". A channel with
`use_max_min` gets a factor socket limited to `factor_min` and
`factor_max`, and one whose `default_value` is not NONE (the Normal
template) gets `hide_value` (`paintsystem/data.py:448-499, 2594`).
Inside, each
channel is a group node (tree `.PS <channel>`,
`paintsystem/data.py:1827`) with Alpha defaulting to 1. The group
input's "<name>" and "<name> Alpha" feed its Color and Alpha, and its
outputs feed the group output.

## v3 design

The user picked the layout on 2026-09-23: template cards, preselected
from the material with the reason shown, a plain-words summary of what
changes in the material, then only the chosen template's options, and
a closed "Material & Viewport" section. v2's features stay; the dialog
explains them instead of listing them.

### Rules every template follows

- Nothing is deleted and no link is dropped. When a template needs the
  Material Output's Surface and a link already goes into it (even a
  muted one, or one from a reroute nothing feeds), the template adds a
  new Material Output and makes it active. The old output and the nodes
  that feed it stay, switched off. The summary names the shader that
  stops showing. The new output also takes the old output's Volume,
  Displacement and Thickness sources, muted as they were (an output
  socket can feed several inputs, so no link moves). A link the
  template needs in another place is moved, and the summary says so.
- A muted link feeds nothing. Reading the graph (the recommendation,
  the target node, the summary) skips it, and a reroute that nothing
  feeds counts as nothing. A muted link that moves under the paint stays
  muted, and the socket's own value is copied to the group's input as
  well, since that value is what showed.
- "The material's output" is `get_output_node('EEVEE')`: painting shows
  in Material Preview, which always renders with EEVEE, and Blender
  prefers an output whose target matches exactly over an active ALL
  output (probed 4.2 to 5.3). A preview output (PS-061) is skipped for
  the output it replaced. A new output takes the target of the output
  it replaces. When `get_output_node('CYCLES')` is another node, the
  summary says Cycles keeps using it.
- One tree per material, named after the material, so PS-040 stays
  deferred. The material is the active material of the mesh that
  `get_ps_object` gives, so an empty parented to a mesh adds to the
  mesh's material, as the panel already shows.
- A material without a node tree, one the dialog makes or a node-less
  one before 5.0, gets Blender's default Principled BSDF and Material
  Output. Unlit and Normal remove that Principled, the one removal the
  dialog makes, of a node it made itself. PBR paints into it and Group
  Only leaves it. Paint Over is refused: until the defaults are made,
  nothing feeds Surface.
- Linked data is read again from its library when the file opens, so a
  change to it would not last. Add Paint System is greyed out when the
  data it writes to is linked (the active material, or the object or
  mesh that would get the new material), with "Linked data cannot be
  set up. Make it local first". Add Channel leaves linked materials
  alone.
- `Material.use_nodes` is set only before Blender 5.0. From 5.0 every
  material has a node tree and the property is deprecated (checked in
  5.0, 5.2 and 5.3: reading it warns). Before 5.0, a material with a
  tree and Use Nodes off gets it turned on, and the summary says so.
- Placement: existing nodes move by a delta on `location`, which works
  inside frames. New nodes are placed from absolute positions
  (`location_absolute` from 4.5, the sum of the parent frames' locations
  on 4.2) and sized by `width`, as `dimensions` is zero in background.
  A new chain starts right of the right-most node, at the height of the
  output it replaces, or at the origin in an empty tree.

### Channel templates

`templates.py` (top level, next to `context.py`) holds the channel
templates, the material templates, the recommendation and the summary.
The operators in `ops/node_tree_ops.py` and `ops/channel_ops.py` use it.

| Template | Channel | Type | Options besides `channel_defaults` | Paints into |
| --- | --- | --- | --- | --- |
| COLOR | Color | COLOR | alpha off when no target has an alpha input | Base Color, or Color on a Diffuse BSDF or an unlit Emission; Alpha, or the unlit Mix factor, through "Color Alpha" |
| METALLIC | Metallic | FLOAT | Limit Range 0 to 1 | Metallic |
| ROUGHNESS | Roughness | FLOAT | Limit Range 0 to 1 | Roughness |
| NORMAL | Normal | VECTOR | Normals, Paint In Tangent, the dialog's UV map | Normal |

Connecting a channel to a shader node: the node's socket link moves onto
the group's channel input, or the socket's value is copied there when it
has no link. Then the group's output is linked to the socket. The alpha
goes the same way, once the colour is connected. So right after Add the
material looks as it did, and an Image Texture that fed Base Color is
now the base the layers paint over. A socket the group's channel output
already feeds is left alone and counts as connected. A socket fed by
another of the group's outputs (the user connected another channel
there), or by a node the group feeds, keeps its link, and so does one
whose group input is already taken: the channel is not connected there.

A Diffuse BSDF has no Alpha input, so a Color channel whose targets are
all Diffuse is made without alpha (as v2): with a base alpha of 0, a
half-transparent stroke would otherwise show at full strength. When
another material's target has an alpha input, the channel keeps its
alpha, and the Diffuse material's group gets "Color Alpha" 1 instead,
unless something feeds it. The tree is compiled before the group's
sockets are read, because `suspend_compile` holds new sockets back.

The target shader node is the Principled BSDF nearest the material's
output upstream (reroutes followed), else the nearest Diffuse BSDF, else
the Emission of an unlit shader as Unlit and Paint Over build it: a Mix
Shader with a Transparent BSDF in its first shader input and the
Emission in its second. Its alpha input is the Mix factor. A plain
Emission of the material's own is not a target, like any other shader:
the unlit shape is recognised so that Unlit and Paint Over materials can
be connected again. Nodes upstream of the Paint System group node
are skipped: after Paint Over, the Principled feeds the group, and
linking the group back into it would make a cycle that Blender drops.

### Material templates

- UNLIT "Unlit": a Color channel. Group Color -> Emission, and a Mix
  Shader with the factor from "Color Alpha" between a Transparent BSDF
  and the Emission, into Surface. The Mix factor's own value is 1, so
  turning Use Alpha off, which drops that link, leaves the material
  opaque instead of half transparent. The dialog's Canvas colour (white
  by default, with alpha) goes into the group's Color and "Color Alpha"
  inputs, so the canvas shows before anything is painted, and a clear
  Canvas gives a transparent start. The channel row shows that base, and
  Channel Settings its alpha. v2 added a white Solid Color layer
  instead.
- PBR "PBR": the checked channels (Color, Metallic, Roughness, Normal;
  only Color is checked at first), connected to the target Principled.
  Without one, a new Principled goes into Surface when it is free,
  else into a new active output. The nodes that feed the Principled
  move left to make room for the group node.
- PAINT_OVER "Paint Over": EEVEE only (`BLENDER_EEVEE_NEXT` on 4.2 and
  4.5, `BLENDER_EEVEE` from 5.0), and only when something feeds Surface.
  That source goes into the group's Color input, through a Shader to
  RGB node when it is a shader. The Shader to RGB's Alpha feeds "Color
  Alpha", so the material's own transparency is kept (v2 set 1.0; the
  alpha was checked exact for Principled, Transparent and Emission
  sources in EEVEE on 4.2 and 5.2). A colour or float source gets
  "Color Alpha" 1. Then Emission and Transparent mix as in UNLIT, into
  the same output, which moves right to make room. The source's link to
  Surface moves onto the Shader to RGB (or the group) input.
- NORMAL "Normal": a Normal channel into a grey Diffuse BSDF's Normal,
  into a new active output. The Diffuse shows the painted normals lit.
- GROUP "Group Only": a Color channel. The group node goes to the right
  of the material's nodes and is not connected, and the summary says
  nothing shows until its outputs are connected.

### Recommendation

`recommend(material, scene)` is a plain function that returns the
template and the reason line. The engine is the scene's render engine.

| Material | Template | Reason |
| --- | --- | --- |
| None | UNLIT | Picked: the object has no material yet. |
| No node tree (before 5.0) | UNLIT | Picked: the material has no nodes yet. |
| Surface fed by a Principled BSDF | PBR | Picked: the material has a Principled BSDF. |
| Surface fed by anything else, EEVEE | PAINT_OVER | Picked: the material has its own shader. |
| The same, another engine, a Principled upstream | PBR | Picked: the material has a Principled BSDF. |
| The same, no Principled | GROUP | Picked: no Principled BSDF to paint into. |
| Nothing on Surface | UNLIT | Picked: nothing feeds the Material Output. |

A Principled fed by textures counts as a Principled (v2 counted it as
complex and picked Paint Over). Its textures move under the paint.
Paint Over can run with an EEVEE engine and something on Surface.

### Dialog

`paint_system.setup_material` keeps its id (the panel and about twenty
tests call it) and is relabelled "Add Paint System". Its options are
UNDO only, without REGISTER: the dialog is the options UI, and an
Adjust Last Operation panel would redraw the dialog against the
material after Add, where the recommendation and summary no longer
hold. `invoke` opens `invoke_props_dialog` at width 360 with the title
"Add Paint System" and the confirm text "Add". Top to bottom:

1. Cards: one `prop_enum` button per template, in two rows (Unlit, PBR,
   Paint Over / Normal, Group Only). The icons are Blender icons on the
   enum items, because `prop_enum` takes no `icon_value`; add-on icons
   would need an items callback. Paint Over is disabled when it cannot
   run. Its tooltip says it needs EEVEE and something connected to the
   Material Output.
2. The reason line (INFO), while the chosen card is the recommended
   one.
3. The summary box: one fact per line, built from the template, the
   options and the material. Labels do not wrap in Blender, so lines
   longer than the box are wrapped in Python by their measured width
   (`wrap_text`, with `blf` at the UI style's widget font size). Each
   line is a `SummaryLine`: its text, whether it is a warning (a problem
   to fix before Add, or before painting), and whether it is indented. A
   warning draws an ERROR icon, and its wrapped lines a blank icon so
   the text lines up. The lines, in order, each only when it applies:
   - 'Makes a new material "<name>".' or 'Adds to the material "<name>".'
     A new material is named "<object> Material", with the first free
     ".001" suffix when that name is taken, as Blender would name it.
   - 'Only the active object is set up.' when another selected mesh does
     not use the material. One that uses it shows the paint as well.
   - The template's lines: Unlit 'Adds an unlit shader that shows the
     paint.'; PBR 'Paints into "<Principled>":', or 'Adds a Principled
     BSDF to paint into:' when there is none, then one indented line per
     channel, '<socket>, over "<source>"' when a link moves under the
     paint, else '<socket>', and after Color 'Alpha, over "<source>"'
     when something feeds the Alpha; Paint Over 'Paints over "<source>" as it
     renders.' and 'The paint on top is unlit.', or, when it cannot run,
     "Paint Over needs EEVEE and something connected to the Material
     Output's Surface." (warning); Normal 'Adds a grey Diffuse shader to
     show the normals.'; Group Only 'Adds the Paint System node, not
     connected.' and 'Nothing shows until you connect it.'
   - '"<shader>" stays, but no longer shows.' when a template that adds
     a shader (Unlit, Normal, and PBR without a Principled) replaces an
     output whose Surface shows something, 'The new output keeps the
     Displacement.' (and Volume, Thickness) when it takes them, and
     'Cycles keeps using "<output>".' for a split output, for those
     templates and Paint Over.
   - 'Turns on the material's nodes.' (before 5.0).
   - 'Adds a <size> x <size> image layer to "<channel>".'
   - 'Uses the UV map "<name>".' when a layer or a Normal channel uses
     one; with no UV map, 'The mesh has no UV map. Unwrap it to paint.'
     (warning).
   - 'Turns on smooth transparency.', 'Hides back faces.', 'Switches
     the view to Standard.'
   - 'Pick at least one channel.' (warning) in PBR with none checked.
   - 'Nothing is deleted or disconnected.'
   A source is named by its image for an Image Texture, else by its
   label or name.
4. The chosen template's options: Canvas (Unlit); the Channels
   checkboxes (PBR); "Start With" (Image Layer or Nothing) with the
   resolution buttons (1024, 2048, 4096) while it is Image Layer; the UV Map
   search when the mesh has more than one UV map and a layer or a
   Normal channel uses it. Empty means the active render UV map, and
   the summary names it.
5. "Material & Viewport", a layout panel that starts closed: Smooth
   Transparency (`surface_render_method` BLENDED, with a warning about
   sorting), Backface Culling (`use_backface_culling` on and
   `use_transparency_overlap` off, as v2), and Standard View Transform.
   The last is drawn only when the scene's own view transform is not
   Standard and the colour management has a Standard view. It sets the
   look to None as well, since a Filmic look such as Very High Contrast
   survives the switch. While a channel preview (PS-061) is saved, the
   change goes to the display the preview puts back. When the user
   picked another view during the preview, that view is the one on
   screen and the one the preview leaves, so it switches to Standard
   now as well. It is applied last and set through
   `props/preview.set_enum`.

The properties are SKIP_SAVE, so every opening starts from the
recommendation. `template` is declared before the options its update
sets, because keyword arguments are applied in declaration order: a
caller's explicit option then wins over the template's default. Picking
a template, in the dialog or as a keyword, sets Backface Culling and
Standard View Transform to its defaults: on for Unlit and Paint Over,
whose output is unlit, and off for the others, which keep the
material's own look. With no template given, `execute` uses
`recommend()` and fills in the options the caller did not set the same
way. Start With defaults to Nothing, so a script gets no layer, and
`invoke` sets Image Layer at 2K. `invoke` only runs with a window, so its
presets are kept to those two lines. The tests call it on a stand-in
whose context records the dialog instead of opening it.

`execute` first checks every refusal (Paint Over that cannot run, PBR
with no channel) and cancels with a report before it changes anything.
Then: make the material if there is none; make the tree with the
template's channels (`initialize` without its default Color channel);
make the first channel active and add the first layer to it; compile;
add the group node (`MATERIAL_GROUP_KEY`) and build the template's
graph; apply the material and view settings; make the tree the scene's
active tree and sync the paint canvas (`update_active_image`).

`poll` greys the button out, with a poll message, when the material
already runs its tree. A material whose tree is set but whose group node
is gone (deleted, or the panel's tree field pointed it at another tree)
is reconnected instead, without the dialog: the group node comes back
and each channel that matches a channel template by name and type is
connected, as `link_tree_to_material` does. Unlit and Paint Over
materials connect through their unlit shader (see the target node
above). Paint Over's Shader to RGB, left feeding nothing, feeds the new
group's Color and "Color Alpha" again, and a running channel preview's
output gets the new group's preview output. The report is INFO
'Connected "<tree>" to "<material>" again' when a channel output is
linked, else a WARNING that no shader node was found to connect it to.
Under the panel's material row, a "Connect to the Material" button
shows in that state.

### Add Channel

The "+" next to the channel list opens a menu: the channel templates the
tree does not have yet (by name), then "Custom..." for the name and type
dialog. `paint_system.add_channel` gets a `template` option, CUSTOM by
default, so scripts keep the name and type call. A template creates the
channel with its options and connects it in every material that runs
the tree (each group node found by `find_material_group_node`, as the
preview does), to that material's target node as above. It reports when
no material had a target. It adds no layers. A nested tree has no group
node in a material, so it only gets the channel.

## Known gaps

- Only the active object's material is set up. v2 set up every
  selected object's material. The summary says so when another selected
  mesh does not use the material.
- No UV map is made for a mesh without one (auto UV is PS-009); the
  summary warns instead. No UDIM or float image options.
- The template is not stored on the tree. Undoing a template (PS-042)
  would have to read the graph.
- Turning a channel's Use Alpha back on does not relink its alpha
  output where it was linked before.
- The preview (PS-061) finds the material's output by the active flag,
  so on a material with EEVEE and Cycles outputs its Preview output may
  not show. That is PS-061's to fix.

## Acceptance

- A headless test per template (`tests/test_templates.py`), on the
  factory cube's material, on a material with a textured Base Color, on
  a material with nothing on Surface, on one with its own Emission, on
  one with EEVEE and Cycles outputs, on one with a Displacement, on one
  with its Principled BSDF in a frame and on an object without a
  material: the expected node
  types and links (by socket name, and by identifier for the Mix
  Shader, whose inputs are both "Shader" and whose factor is "Fac" on
  4.x and "Factor" from 5.0), the material's output, every node from
  before still there (but the default Principled Unlit and Normal
  remove), and every link from before still there or moved onto the
  group's input or the Shader to RGB.
- PBR with all four channels connects Base Color, Alpha, Metallic,
  Roughness and Normal, copies their values, leaves Color active with
  an sRGB image layer that is the paint canvas, and a Cycles render of
  the empty stack matches the render from before Add.
- Unlit renders its Canvas colour unlit. Paint Over on an Emission ->
  Output chain feeds the emission through Shader to RGB and keeps a
  Transparent source transparent (graph checks; EEVEE renders are
  probed by hand, as CI may have no GPU). A refused Paint Over leaves
  `bpy.data` unchanged.
- The recommendation table, the summary lines per template and the
  dialog draw (cards, reason, options per template, the closed
  section) are checked headlessly, with the render engine set by each
  test.
- The Add Channel menu lists only the missing templates. Roughness
  connects to the Principled's Roughness with its value copied, in
  every material that runs the tree. After Paint Over, it adds an
  unconnected channel and leaves every link valid. Color on a Diffuse
  target has no alpha, and a red layer at opacity 0.5 over a 0.5 grey
  base gives (0.75, 0.25, 0.25).
- Deleting the group node and running Add Paint System connects the
  tree again, for PBR, Unlit and Paint Over (with its Shader to RGB)
  and during a channel preview, and the panel shows the Connect to the
  Material button only in between. Group Only reports a warning.
- Through `invoke`, on stand-ins: Add Paint System opens its dialog on
  the recommended template with an image layer, Connect to the Material
  and a channel template from the menu skip their dialogs, and
  Custom... opens the name dialog with a free name.
- Muted links: a muted Surface link or a reroute nothing feeds counts
  as nothing (recommendation, refusal, summary); a muted link moved
  under the paint stays muted with its socket's value; a new output
  copies a muted Displacement muted.
- Linked data: Add Paint System is greyed out for a linked material and
  for a linked mesh without a material (until the slot links to the
  object), and Add Channel connects only the local materials.
- A copied material and tree (as appending brings) run their own
  compiled tree, and adding a channel to the original leaves the copy
  alone.
- `tests/test_icons.py` follows a module-level name bound by a relative
  import (such as `SOCKET_ICONS`) to its definition, and checks a
  `blender_icon(...)` given as an EnumProperty item's icon against
  Blender's icon names, since Blender draws an unknown item icon as
  none without an error.
