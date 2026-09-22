# PS-032 Channels sub-panel, settings and add-channel menu

Epic D. Size M. Milestone M1 (list, add/delete/move), M2 (settings).

## Status

Partly done by PS-005: the list row shows the material group node's
unlinked input, and a closed "Channel Settings" section below the list
draws the PS-005 options. PS-005 also decided that the Add Channel dialog
keeps Name and Type, with the options set from the type, so the CUSTOM
dialog properties below are dropped. Still to do: the add-channel menu
with templates (PS-041), and the PS-006/PS-007 settings.

## v2 behaviour

`panels/channels_panels.py`:

- Closed header shows popover `MAT_PT_ChannelsSelect` (`:93`, ui_units_x
  10) with the active channel name and type icon; open shows
  `draw_channels_panel` (`:114-123`).
- `PAINTSYSTEM_UL_channels.draw_item` (`:39-63`): baked channel shows
  icon + name + "Baked" (TEXTURE_DATA); otherwise `split(factor=0.7)` with
  type icon (icon_only, no emboss), name, and the group node's unlinked
  input `default_value` (colour or float) on the right.
- Side column (`:71-91`): add (`MAT_MT_AddChannelMenu` when templates
  remain, else `add_channel` with CUSTOM), `delete_channel`,
  `move_channel_up/down`. `rows=max(len(channels), 3)`.
- `layout.panel("MAT_PT_ChannelsSettings")` default closed ->
  `draw_channels_settings_panel` (`:156-211`): Use Baked toggle + delete
  bake image; VECTOR baked -> `bake_vector_space`; else property-split
  column `type`, `color_space`, `use_alpha` (+ alpha default value),
  VECTOR block (default value, Vector Transform panel), FLOAT block
  (`use_max_min`, min, max).
- `MAT_MT_AddChannelMenu` (`:231`): channel templates (Color, Metallic,
  Roughness, Normal) not yet present, then "Custom".
- Operators (`operators/channel_operators.py`): `add_channel` (template
  or CUSTOM dialog with live "name will be renamed" warning), `delete_channel`
  with confirmation, `move_channel_up/down`.

### v2 UI

All paths are relative to `~/paintsystem`. Line numbers without a file
refer to `panels/channels_panels.py`.

Where it is drawn:

- The live path is `layout.panel("MAT_PT_ChannelsPanel",
  default_closed=True)` inside `MAT_PT_PaintSystemMainPanel`
  (`panels/main_panels.py:211-224`). It is the first sub-section after
  the paint mode block and before Brush and Color. The header is the
  label "Channels" with the custom `channel` icon.
- The section exists when `poll_channels_panel` (`:108-112`) passes: a
  group is active and its tree is not shared by several materials
  (`check_group_multiuser`, `panels/common.py:197-204`).
- The panel classes `MAT_PT_ChannelsPanel` (`:125-154`) and
  `MAT_PT_ChannelsSettings` (`:213-229`) are commented out of
  `classes` (`:254-261`), so they are dead. Their idnames survive only
  as `layout.panel` keys. The dead classes differ from the live path:
  the header popover shows in both states (`:142-150`), and the settings
  panel is labelled "Channels Settings" and polls for an active channel
  (`:217-225`).

Header, section closed (`panels/main_panels.py:216-224`):

- A header row (align, `scale_x` 1.1, alignment RIGHT) holds
  `popover("MAT_PT_ChannelsSelect")`. The text is the active channel
  name, or "No Channel". The icon is the channel's socket icon
  (`get_icon_from_channel`, `panels/common.py:30-36`: COLOR
  `color_socket`, VECTOR `vector_socket`, FLOAT `float_socket`). With
  the section open the header shows only the label.
- With no active channel the icon call is `get_icon_from_channel(None)`,
  which reads `.type` on None and raises, so the "No Channel" text
  cannot draw (`panels/main_panels.py:222-223`; inferred from code).

`MAT_PT_ChannelsSelect` popover (`:93-106`): VIEW_3D / WINDOW,
INSTANCED, `bl_ui_units_x = 10`. An aligned column holds the label
"Channels" and `draw_channels_list`.

Section body (`draw_channels_panel`, `:114-123`):

1. `layout.box()`.
2. Legacy UI only: `box.menu("MAT_MT_PaintSystemChannelsMergeAndExport",
   text="Bake and Export", icon="TEXTURE_DATA")`.
3. `draw_channels_list(context, box)`.
4. `box.panel("MAT_PT_ChannelsSettings", default_closed=True)` with the
   header label "Channel Settings" and no icon. The body is
   `draw_channels_settings_panel`. There is no guard for a missing
   active channel, unlike the dead class poll at `:222-225`. A group
   with no channels would fail on `active_channel.bake_image` at `:159`
   (inferred from code).

Channel list (`draw_channels_list`, `:71-91`):

- `row.template_list("PAINTSYSTEM_UL_channels", "", active_group,
  "channels", active_group, "active_index",
  rows=max(len(channels), 3))`.
- A side `column(align=True)` without separators, all buttons `text=""`:
  - Add: `wm.call_menu` with `name = "MAT_MT_AddChannelMenu"` (ADD)
    while any channel template is unused. Otherwise
    `paint_system.add_channel` with `template = "CUSTOM"` (ADD), which
    opens the custom dialog directly. A template counts as used when a
    channel has the template's display name ("Color", "Metallic",
    "Roughness", "Normal"; `:84`), whatever that channel's type is.
  - `paint_system.delete_channel` (REMOVE),
    `paint_system.move_channel_up` (TRIA_UP),
    `paint_system.move_channel_down` (TRIA_DOWN).

List row (`PAINTSYSTEM_UL_channels.draw_item`, `:39-63`):

- Baked channel (`use_bake_image`): a row with an aligned sub-row
  holding a label with the socket icon and `name` (text "", no emboss).
  A second sub-row holds the label "Baked" (TEXTURE_DATA). No value is
  drawn.
- Otherwise: `split(factor=0.7)`. The left row (align) holds
  `prop(channel, "type", text="", icon_only=True, emboss=False)`. This
  is the type enum drawn as an icon dropdown, so the channel type can be
  changed from the row. Its items are Color (`color_socket`), Vector
  (`vector_socket`) and Value (`float_socket`)
  (`paintsystem/data.py:113-117`). The `name` follows (text "", no
  emboss).
- The right part holds the group node input named after the channel,
  when that input exists, is enabled and has no links. The group node
  comes from `get_group_node`, which prefers a connected node
  (`paintsystem/data.py:2563-2567`). By base socket type:
  `NodeSocketColor` draws `default_value` with icon COLOR,
  `NodeSocketFloat` draws `default_value`, and vector inputs draw
  nothing.

Channel settings (`draw_channels_settings_panel`, `:156-211`), in
order:

1. If a bake image exists: a `row(align)` with `use_bake_image` "Use
   Baked Image" (TEXTURE_DATA) and `paint_system.delete_bake_image`
   (TRASH, text "").
2. If the baked image is in use, only VECTOR draws `bake_vector_space`
   (text "": "World Space", "Object Space", "Tangent Space", default
   OBJECT, `paintsystem/data.py:2487-2497`), and the draw returns.
3. An aligned column with property split: "Type", "Color Space"
   ("Color" or "Non-Color", `paintsystem/data.py:192-195`), "Use
   Alpha". When `use_alpha` is on, the group node input "<name> Alpha"
   follows as `default_value` "Alpha", if it is enabled and unlinked.
4. VECTOR:
   - "Default Value" (None, Normal, World Position, Object Position;
     `paintsystem/data.py:2390-2401`).
   - A box with split off and `box.panel("vector_space_settings_panel")`.
     This panel has no `default_closed`, so it starts open. The header
     is "Vector Transform". Inside:
     - A `row(align)` with the toggles "Transform Input" and "Transform
       Output".
     - Property split on. `vector_type` "Vector Type", expanded (Point,
       Vector, Normal, default VECTOR).
     - A row with "Input Space" (World, Object), enabled only with
       Transform Input.
     - A row with "Layer Space" (`vector_space`: World, Object,
       Tangent). It is enabled with either toggle on. The
       `normalize_input` icon toggle (NORMALS_VERTEX_FACE, text "")
       follows unless the space is Tangent.
     - A row with "Output Space" (World, Object, Tangent), enabled only
       with Transform Output.
     - When the layer or output space is Tangent: `prop_search` of
       `tangent_uv_map` over the mesh UV maps, "Tangent UV" (GROUP_UVS).
       The property defaults to "UVMap" (`paintsystem/data.py:2445-2449`).
   - The space enums use the icons WORLD, OBJECT_DATA and MESH_DATA
     (`paintsystem/data.py:2413-2444`).
5. FLOAT: a box with a column (split off) holding `use_max_min` ("Use
   Max Min"). When it is on, `factor_min` ("Factor Min") and
   `factor_max` ("Factor Max") follow.

Legacy menu `MAT_MT_PaintSystemChannelsMergeAndExport` (`:15-34`,
bl_label "Baked and Export"): an aligned column with the label "Bake",
"Bake All Channels" (`channels`), and "Bake Active Channel (<name>)"
(socket icon). Then a separator, the label "Export", "Export All
Images" (EXPORT), and "Export Active Channel (<name>)" (EXPORT). The
last one gets `image_name` only when a bake image exists.

`MAT_MT_AddChannelMenu` (`:231-252`): bl_label "Add Channel", poll
`active_group`. It forces the INVOKE context
(`panels/common.py:453-461`). In order:

- "Custom Channel" (`channels` icon, `template = "CUSTOM"`).
- A separator.
- When any template is unused: the label "Templates", then one
  `add_channel` per unused template, text = display name, icon = its
  socket icon (`paintsystem/data.py:214-219`).

"Custom Channel" is therefore first, and the templates follow. That is
the reverse of the order given above.

Operators (`operators/channel_operators.py`). All four are
`MultiMaterialOperator`s, so they run on the active material of every
selected mesh (`operators/common.py:32-77`, `multiple_objects` defaults
to True).

- `paint_system.add_channel` (`:12-124`, bl_label "Add Channel").
  `template` (default CUSTOM, SKIP_SAVE) accepts the four templates plus
  CUSTOM (`:10`). A template executes at once from `invoke`. CUSTOM
  makes the name unique and opens `invoke_props_dialog` with the
  default width (`:99-104`).
  - Dialog (`:106-124`), in order: "Name" (default "New Channel"),
    "Type" (Color, Vector, Value), "Color Space", "Expose Alpha Socket"
    (default off), and "Normalize" for VECTOR only.
  - If the typed name collides, an alert box (alignment CENTER) shows
    "Name will be changed to '<unique>'" (ERROR).
  - FLOAT only: "Use Max Min", and when it is on, "Factor Min" and
    "Factor Max".
  - A custom channel is created with `vector_space="OBJECT"` (`:92`).
- What the templates create (`create_channel_template`,
  `paintsystem/data.py:2688-2752`). The target is the first Principled
  BSDF, or else a Diffuse BSDF. The group template is not checked.
  - Color: COLOR with alpha. It is wired to "Base Color" (or "Color")
    and "Alpha", and alpha is turned off when the target has no Alpha
    input. It adds an Image layer.
  - Metallic and Roughness: FLOAT, `use_max_min`, Non-Color, no alpha,
    no layers.
  - Normal: VECTOR, `normalize_input`, Non-Color, default value Normal,
    both transforms on. It adds a Geometry "Normal" layer when no
    existing normal link was moved, then an Image layer.
- `paint_system.delete_channel` (`:127-151`, bl_label "Delete
  Channel"): `invoke_props_dialog` with title "Delete Channel", width
  300, and one label "Are you sure you want to delete '<name>'
  Channel?". Its poll tests `ps_mat_data.active_index`, the group index,
  not the channel index (`:133-137`). `Group.delete_channel`
  (`paintsystem/data.py:2754-2762`) removes the collection item by name,
  selects the previous index, and rebuilds the group tree.
- `paint_system.move_channel_up` / `move_channel_down` (`:154-197`,
  both bl_label "Move Channel"): they poll on `ListManager` possible
  moves, then rebuild the group tree.

Behaviour the UI implies:

- Clicking a row sets `active_index`, which runs `update_channel`
  (`paintsystem/data.py:2647-2659`). With channel preview on, it calls
  `isolate_channel` twice on the new channel ("to ensure it's updated",
  per the code comment). If the new channel uses its baked
  image, it forces Object mode. Then it updates the paint canvas.
- Renaming a channel from the row runs `update_channel_name`
  (`paintsystem/data.py:2006-2020`). It renames the channel tree to
  ".PS_Channel (<typed name>)", makes the name unique within the group,
  and rebuilds the group. Because templates match by name, renaming the
  "Color" channel makes the Color template available again.
- Changing the channel type from the row runs `update_type`, which
  rebuilds the channel and the group (`paintsystem/data.py:2329-2337`).
- `color_space` has no update callback
  (`paintsystem/data.py:2339-2344`). Its only readers set the colour
  space of the bake target image at bake time, whether that image is
  new or reused (`operators/bake_operators.py:262, 334`).
- `use_alpha` defaults to True on the property
  (`paintsystem/data.py:2345-2349`), but to False in the custom dialog.

## v3 design

- Replace the existing channel UIList in `panels/main_panels.py` with the
  v2 draw, reading the default value from the material's group node input
  socket (found through `MATERIAL_GROUP_KEY`) when unlinked.
- `ops/channel_ops.py` already has add/remove/move; extend `add_channel`
  with `template` (PS-041 channel templates) and the CUSTOM dialog
  properties from PS-005.
- Settings sub-panel draws PS-005/PS-006/PS-007 properties with the v2
  layout.

## Acceptance

- Screenshot parity with v2 for a Color + Roughness setup.
- Adding "Metallic" from the menu creates a FLOAT channel wired to the
  Principled Metallic input when the template group is PBR (PS-041).
