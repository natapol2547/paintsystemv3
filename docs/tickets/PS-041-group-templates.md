# PS-041 Group templates and channel templates

Epic E. Size L. Milestone M1.

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

- `ops/templates.py`: a `Template` class per kind with `prepare_material`,
  `create_channels(tree)`, `wire(material, group_node)` and
  `dissolve(material, group_node)` (PS-042). `paint_system.new_group`
  picks the template, creates and initialises the tree as
  `setup_material` does, records
  `tree.template`, and calls the three steps. Existing
  `link_tree_to_material` becomes the NONE/PBR wiring primitive.
- Channel templates: `CHANNEL_TEMPLATES = {COLOR: (type, use_alpha,
  color_space, principled socket), ...}` used by both `new_group` and
  `add_channel`. Wiring to Principled is done by socket name and skips
  linked sockets.
- PAINT_OVER keeps the EEVEE-only rule; the render engine check must
  handle `BLENDER_EEVEE_NEXT` and `BLENDER_EEVEE`.
- The dialog layout (`draw`, `group_operators.py:330-380`) is ported one
  to one: template enum expanded with icons, name, per-template options
  (add solid/image layers, PBR channel checkboxes, blend mode, backface
  culling).
- The initial image layer uses `PSImageCreateMixin` (PS-009) so the
  resolution and UDIM options appear in the same dialog as in v2.

## Acceptance

- Each template on a fresh cube produces the v2 material graph (compare
  node type sets and link endpoints by socket name in a headless test).
- PAINT_OVER on a material with an Emission -> Output chain inserts
  Shader to RGB and renders the painted colour over the emission.
- PBR with all four channels wires Base Color, Metallic, Roughness,
  Normal.
