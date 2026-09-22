# PS-035 Layer Settings sub-panels

Epic D. Size L. Milestone M1 (image, transform), M2 (rest).

## Status

Partly done (M0 slice 4): the Layers panel draws a "Layer Settings"
`layout.panel("layer_settings_panel")`, disabled while the layer is
locked, with the type's `draw_source_settings(context, layout)`: the
image and UV map for image layers, the colour for solid layers, nothing
for folders. Nodes draw the same method between their layer and cache
settings. The name is `draw_source_settings`, pairing with `emit_source`,
because `draw_layer_settings` already draws the lock, opacity and blend
row on nodes. The Image, Transform and Actions sub-panels are open.

## v2 behaviour

`draw_layer_settings` (`panels/layers_panels.py:171-492`),
`layout.enabled = not lock_layer`:

- Per-type box: ADJUSTMENT `template_node_inputs`; NODE_GROUP extra
  inputs; GRADIENT empty select / fix missing; SOLID_COLOR colour;
  RANDOM seed/base/H/S/V; GEOMETRY transform, backface, normalize, AO.
- `image_settings_panel` "Image" (`:304-333`): header has left-aligned
  "Filters" menu (`MAT_MT_ImageFilterMenu`); body: quick edit / reload /
  project apply + `toggle_image_editor` row, `image_node_settings`,
  colour/alpha socket grid, `correct_image_aspect`.
- `gradient_node_settings_panel` + nested Map Range (`:334-352`).
- `texture_node_settings_panel` (`:353-366`),
  `attribute_node_settings_panel` (`:367-377`).
- `layer_transform_settings_panel` "Transform" (`:379-437`): `coord_type`
  enum in the header + `transfer_image_layer_uv`; body per coord type
  (UV `prop_search`, DECAL empty + depth clip, PROJECT view reset / set
  projection / scale / space / falloff, PARALLAX space, uv map, depth),
  nested `mapping_panel`; collapsed + `use_panel_quick_access` -> compact
  row (`:438-460`).
- `layer_actions_settings_panel` "Actions" (`:462-492`): tips box,
  `PAINTSYSTEM_UL_Actions` rows=5, add/delete, bind/frame/marker/type.

### v2 UI

Paths are relative to `~/paintsystem` (commit 8991037). Line numbers
without a file are in `panels/layers_panels.py`.

Placement and open state:

- There is no `Panel` class. `draw_layer_settings(panel, context)`
  draws into the body of `layout.panel("layer_settings_panel")` in
  `MAT_PT_Layers` (`:671-674`). Every sub-panel below is a nested
  `layout.panel` in that body.
- `layout.enabled = not lock_layer` (`:174`) covers everything,
  including the Actions list. In legacy UI it also covers the active
  layer row drawn at the top (`:175-177`), so the lock toggle itself is
  disabled and a locked layer cannot be unlocked from Layer Settings.
  The list row only shows the lock as a label (PS-034 "v2 UI").
- Open by default: `layer_settings_panel` and `node_group_panel`
  (`:294`).
- Closed by default:
  - `image_settings_panel` (`:305`);
  - `image_node_settings_panel` (`panels/common.py:207, 212`; the
    `default_closed` argument defaults to True);
  - `input_sockets_panel` (`:52`);
  - `gradient_node_settings_panel` and `map_range_node_settings_panel`
    (`:338`, `:343`);
  - `texture_node_settings_panel` (`:354`);
  - `attribute_node_settings_panel` (`:368`);
  - `layer_transform_settings_panel`, `proj_node_panel` and
    `mapping_panel` (`:380`, `:417`, `:432`);
  - `layer_actions_settings_panel` (`:462`).
- The idnames are fixed strings. Blender keeps one open state per
  idname in the parent panel, so opening "Image" on one layer leaves it
  open for every image layer.
- Draw order: legacy settings box, per-type box, `node_group_panel`,
  Image, Gradient, Texture, Attribute, Transform (or the quick access
  row), Actions.

Active layer row (`layer_settings_ui`, `panels/common.py:316-377`).
PS-034 draws it above the list; the legacy UI draws it here:

- Nothing is drawn when there is no active layer or it has no node
  tree (`panels/common.py:319-320`).
- Modern UI (`panels/common.py:347-377`):
  - Wide test: `region.width - 35*2*ui_scale > 170*ui_scale`.
  - The container is `split(factor=0.7)` when wide, else
    `column(align=True)`. It has `scale_x` and `scale_y` 1.3.
  - Row, in order:
    - `is_clip` (SELECT_INTERSECT), plus `lock_alpha` (TEXTURE) for
      IMAGE layers, in one aligned sub-row that is disabled while
      locked;
    - `lock_layer` (`icon_parser('VIEW_LOCKED', 'LOCKED')`), which is
      never disabled;
    - `blend_mode` with text "", disabled while locked.
  - Opacity is a slider on `pre_mix_node.inputs['Opacity']`, disabled
    while locked. When wide it has text "". Otherwise it has text
    "Opacity" and `scale_y` 0.8.
- Legacy UI (`panels/common.py:323-346`): a `column(align=True)`.
  - The first row sets `scale_x` and `scale_y` to 1.2, then calls
    `scale_content(1.7, 1.5)`. It holds the same buttons.
  - The second row uses `scale_content(1.2, 1.5)` and holds an
    "Opacity" slider.
  - It sits in a box at the top of Layer Settings (`:175-177`).
  - PS-039 drops `use_legacy_ui` in v3.
- Blend mode items (`paintsystem/data.py:62-68`) are Blender's MixRGB
  `blend_type` items in order. "Pass Through" is inserted after Mix.
  Separators follow Mix/Pass Through, Add, Linear Light and Divide.
- The code also lists "COLOR_BURN" for a separator. That never
  matches, because Blender's identifier is BURN (checked in 5.2.1). The
  groups are:
  - Mix, Pass Through;
  - Darken, Multiply, Color Burn, Lighten, Screen, Color Dodge, Add;
  - Overlay, Soft Light, Linear Light;
  - Difference, Exclusion, Subtract, Divide;
  - Hue, Saturation, Color, Value.
- Pass Through is offered only on folders
  (`paintsystem/data.py:1361-1368`). A non-folder that holds
  PASSTHROUGH is reset to MIX on rebuild
  (`paintsystem/data.py:851-853`).

Per-type box (`:180-290`), additions:

- In modern UI each per-type box is a new `layout.box()`. Legacy UI
  reuses the settings box (`get_settings_box`,
  `panels/common.py:464-480`).
- IMAGE: no box. The commented-out block at `:181-196` is dead; it
  moved into the Image sub-panel.
- ADJUSTMENT: label "Adjustment Settings:" (SHADERFX), then
  `template_node_inputs(source_node)` (`:197-203`).
- NODE_GROUP: the label "Node Group Settings:" (NODETREE), then one
  `default_value` field per unlinked input except Color and Alpha,
  labelled with the socket name. It is drawn only when there is at
  least one such input (`:204-214`).
- NODE_GROUP also gets `node_group_panel` "Sockets Settings:"
  (`:293-300`), with property split. It draws `draw_socket_grid`
  (`panels/common.py:426-450`): one box with a two-column grid of
  "Color Output" / "Alpha Output" name fields, and a second box with
  "Color Input" / "Alpha Input".
- GRADIENT, only for LINEAR, RADIAL and FAKE_LIGHT, in a property
  split column (`:215-230`):
  - If the empty is in the view layer: an aligned column with "Select
    Gradient Empty" (or "Select Light Empty" for Fake Light,
    OBJECT_ORIGIN, `paint_system.select_empty`) and the `empty_object`
    field.
  - Otherwise: an alert box with "Gradient Empty not found" (ERROR)
    and "Fix Missing Gradient Empty".
  - `select_empty` relinks the empty if needed, switches to Object
    mode and selects it (`operators/layers_operators.py:361-377`).
  - The fix rebuilds every gradient layer in the channel
    (`operators/layers_operators.py:346-358`).
- SOLID_COLOR: "Color" (IMAGE_RGB_ALPHA) on the RGB node's
  `outputs[0].default_value` (`:231-237`).
- RANDOM: drawn only if five named nodes exist (`:239-261`). It shows
  "Random Settings:" (SHADERFX) and "Random Seed", then a property
  split column: "Base Color", "Hue", "Saturation", "Value".
- GEOMETRY (`:262-288`):
  - VECTOR_TRANSFORM: "Vector Transform:" (MESH_DATA) plus node
    inputs.
  - BACKFACING: a nested box with "Material Settings:" (MESH_DATA) and
    the material's "Backface Culling" (CHECKBOX_HLT / CHECKBOX_DEHLT).
  - WORLD_NORMAL, WORLD_TRUE_NORMAL and OBJECT_NORMAL: "Normalize
    Normal" (MESH_DATA).
  - AMBIENT_OCCLUSION, property split: "Samples", "Inside", "Only
    Local", "Color", "Distance".
  - POSITION and OBJECT_POSITION draw an empty box.
- Type switching after creation: the only type field in these
  sub-panels is "Texture Type" on texture layers (`:362`). Gradient,
  adjustment and geometry variants, and the layer type itself, have no
  UI after creation: no panel draws a layer's `type`, `gradient_type`,
  `adjustment_type` or `geometry_type`. The other `*_type` fields
  drawn here are `coord_type` (`:383`), the map range
  `interpolation_type` (`:348`) and `action_type` (`:492`). Coordinate
  type switching is in the Transform header.

Image sub-panel (`:304-333`):

- Header: label "Image" (custom `image`). Then a left-aligned row with
  "Filters", a `wm.call_menu` for `MAT_MT_ImageFilterMenu`. It is drawn
  whether or not the panel is open (`:331-333`).
- Body: `panel.box().column()`. First an aligned row scaled 1.1 / 1.1
  (`:310-321`):
  - internal image: "Edit in Image Editor" (`paint_system.quick_edit`
    "Quick Edit", `operators/quick_edit.py:136-140`). Its icon is
    always the custom `image` icon: `get_image_editor_icon` returns
    that icon when an external image editor path is set and None
    otherwise, and the draw falls back to the same icon
    (`custom_icons.py:48-51`, `:313-314`).
  - external image, `edit_external_mode` IMAGE_EDIT: "Open Image" and
    "Reload Image" (FILE_REFRESH, `operators/quick_edit.py:302-331`).
    "Open Image" passes `get_image_editor_icon` with no fallback, so
    its `icon_value` is None when no editor path is set (`:317`).
  - external image, VIEW_CAPTURE: "Apply Edit"
    (`paint_system.project_apply`, `operators/quick_edit.py:107-133`).
  - Always, last: icon-only `toggle_image_editor` (BLENDER), depressed
    while an Image Editor is open.
- `toggle_image_editor` (`operators/utils_operators.py:423-463`)
  closes an open Image Editor. Otherwise it splits the area and shows
  the layer image in Paint mode, with overlays only for AUTO and UV.
- Then `line_separator` and `image_node_settings(simple_ui=True)`
  (`panels/common.py:207-252`), a nested closed
  `image_node_settings_panel`.
  - Header with an image: an aligned row with the image field,
    icon-only `export_image` (FILE_TICK, "Export Baked Image",
    `operators/bake_operators.py:349-352`), and `MAT_MT_ImageMenu`
    (COLLAPSEMENU).
  - Header with no image: `template_ID` with `image.new` and
    `image.open`.
  - Body, property split: "UDIM tiles: 1001, ..." (UV); unlabelled
    `interpolation`, `projection`, `extension` and `source`; then
    "Color Space" and "Alpha".
- `MAT_MT_ImageMenu` "Image Menu" (`:707-722`): "Resize Image"
  (CON_SIZELIMIT) and "Clear Image" (X). Poll: the layer has an image.
- A `line_separator` follows only while that panel is open.
- `draw_input_sockets(only_output=True)` (`:49-57`): closed
  `input_sockets_panel`, header "Sockets Settings:" (`float_socket`).
  The body is a BLANK1 indent plus the output grid.
- Last row: BLANK1 plus a "Correct Aspect" toggle (CHECKBOX_HLT /
  CHECKBOX_DEHLT) (`:328-330`). The graph adds the correction only for
  non-square images (`paintsystem/graph/basic_layers.py:229-235`).
- `MAT_MT_ImageFilterMenu` "Image Filter Menu" (`:686-705`):
  - poll: a bake image or a layer image exists (`:677-684`);
  - operator context `INVOKE_REGION_WIN`;
  - entries: Brush Painter (BRUSH_DATA), Gaussian Blur (FILTER),
    Invert Colors (MOD_MASK), "Fill Image" (SNAP_FACE);
  - "Sharpen Image" is registered but not in the menu
    (`operators/image_operators.py:465-470`).

Gradient, Texture and Attribute sub-panels:

- Gradient (`:334-352`) is drawn only when both the gradient node and
  the `map_range` node exist.
  - Header: "Gradient", or "Light Gradient" for Fake Light (COLOR).
  - Body: a box with `template_node_inputs(gradient_node)`.
  - Nested closed "Map Range:" (SHADERFX), property split:
    "Interpolation"; "Steps" (input 5) only for STEPPED; "Start
    Distance" (input 1); "End Distance" (input 2).
- Texture (`:353-366`): header "Texture" (TEXTURE). Body: a box column
  with property split.
  - `draw_input_sockets` (outputs only);
  - "Texture Type";
  - `template_node_inputs(texture_node)` with split turned off.
- Attribute (`:367-377`): header "Attribute" (MESH_DATA). Body: a box
  with the output grid, then "Attribute Settings:" (MESH_DATA) and
  `template_node_inputs(attribute_node)`.

Transform sub-panel (`:379-437`):

- Drawn for IMAGE and TEXTURE. The test is the literal
  `active_layer.type in ('IMAGE', 'TEXTURE')` (`:379`). It matches
  `uses_coord_type` (`paintsystem/data.py:1435-1437`) but does not
  call it.
- Header: label "Transform" (custom `transform`), then an aligned row
  (`:380-385`):
  - `coord_type` dropdown with text "";
  - for an IMAGE layer with an image, icon-only
    `paint_system.transfer_image_layer_uv` (UV_DATA).
- "Transfer Image Layer UV" (`operators/bake_operators.py:542-606`):
  its own `invoke` is commented out
  (`operators/bake_operators.py:553-554`), so it uses the
  `BakeOperator` dialog (`operators/bake_operators.py:81-89`). Its
  `draw` (`operators/bake_operators.py:556-564`) shows, in order:
  - "Baking material: {name}" (MATERIAL);
  - `other_objects_ui` (`operators/bake_operators.py:100-124`), only
    when more than one object uses the material. If some are not
    selected: an alert "Detected other objects with the material(s)."
    (ERROR) and "They will not be baked" (BLANK1), a "Select All
    Objects" (SELECT_EXTEND) button, and a closed "See Detected
    Objects" panel with a 3-column grid of names. Otherwise: "All
    objects with the material are selected" (CHECKMARK).
  - a "UV Map" (UV) box with a UV `prop_search`;
  - a closed "Advanced Settings" (IMAGE_DATA) panel with "Use GPU" and
    a 0.4 split of "Margin" and the margin type
    (`operators/bake_operators.py:91-98`).
- `coord_type` items (`paintsystem/data.py:150-162`): Auto UV, UV,
  Object, Camera, Window, Reflection, Position, Generated, Decal,
  Projection, Parallax. The default is UV
  (`paintsystem/data.py:1169-1175`).
- Side effects of changing `coord_type`
  (`paintsystem/data.py:1159-1168, 880-885`):
  - DECAL or PROJECT sets the image node's extension to CLIP;
  - PROJECT with no projection node captures the current view;
  - DECAL creates a SINGLE_ARROW empty if missing, or relinks it into
    the view layer.
- Body: property split and a box. For AUTO, OBJECT, CAMERA, WINDOW,
  REFLECTION, POSITION and GENERATED the box holds only the mapping
  sub-panel (`:390`).
- UV: `prop_search` "UV Map" over the object's `uv_layers`
  (GROUP_UVS).
- DECAL (`:395-406`), without property split:
  - an aligned column with the empty field and "Select Empty"
    (OBJECT_ORIGIN);
  - a 0.35 split with a "Clip" toggle (CHECKBOX icons) and "Depth"
    (`decal_depth_clip` input 2). Depth is enabled only while clipping.
- PROJECT (`:407-420`, PS-065):
  - "View Current Projection" (CAMERA_DATA) in an aligned column at
    `scale_y` 2. It runs `projection_view_reset` ("Projection View
    Reset"), which snaps the 3D view to the stored projection
    (`operators/layers_operators.py:1078-1100`).
  - "Set New Projection View" (FILE_REFRESH) opens a confirm dialog:
    an alert box with "Override the projection view?" (ERROR) and
    "Projection will be overridden with the current view"
    (`operators/layers_operators.py:1049-1075`).
  - If the projection node exists: "Scale", then "Space" (World with
    WORLD, Object with OBJECT_DATA, `paintsystem/data.py:1333-1342`).
  - Then a nested closed `proj_node_panel` with the "Normal Falloff"
    checkbox (node input Enable) in its header and "Degree" (Falloff)
    in its body.
- PARALLAX (`:421-428`): "Space" expanded (`parallax_space`, UV /
  Object), "UV Map" `prop_search` only for UV, then "Depth".
- Mapping (`:430-437`): nested closed `mapping_panel` "Mapping
  Settings:" (`vector_socket`), without property split, with
  `template_node_inputs(mapping)`.
- The graph adds a Mapping node for every coordinate type. For PROJECT
  and DECAL it adds (0.5, 0.5) after the mapping
  (`paintsystem/graph/basic_layers.py:219-243`).

Quick access (`:438-460`):

- Drawn straight into the Layer Settings body, under the collapsed
  Transform header, when `use_panel_quick_access` is on. That
  preference defaults to False (`panels/preferences_panels.py:86-90`).
- Each row starts with a BLANK1 label:
  - UV: an aligned row with the "UV Map" `prop_search`;
  - DECAL: "Select Empty" (OBJECT_ORIGIN);
  - PROJECT: "View Current Projection" (CAMERA_DATA) and icon-only
    "Set New Projection View" (FILE_REFRESH);
  - PARALLAX: a 0.35 split with `parallax_space` (text "") and
    "Depth".
- Other coordinate types draw nothing.

Actions sub-panel (`:461-492`, PS-063):

- Drawn for every layer type, including folders.
- Header "Actions" (KEYTYPE_KEYFRAME_VEC). Body: property split,
  alignment LEFT.
- Tips box when `show_tooltips` is on (default True,
  `panels/preferences_panels.py:13-17`) and there are no actions:
  "Actions can control layer visibility" (INFO) and "with frame number
  or marker" (BLANK1).
- "Action Order:" label. Then a row with
  `template_list("PAINTSYSTEM_UL_Actions", "", layer, "actions", layer,
  "active_action_index", rows=5)` and an aligned column holding
  `add_action` (ADD) and `delete_action` (REMOVE).
- With at least one action, under the list:
  - "Bind to" (`action_bind`);
  - "Frame", or a "Once reach" `prop_search` over the scene's timeline
    markers (MARKER_HLT);
  - "Action" (`action_type`: Enable Layer / Disable Layer,
    `paintsystem/data.py:182-190`).
- List row (`:899-919`): icon-only `action_bind` with emboss off
  (KEYTYPE_KEYFRAME_VEC or MARKER_HLT). Then a label such as "Frame 12
  (Enable)" or "Marker {name or None} (Disable)".
- `filter_items` sorts rows by frame. A marker uses its frame, and a
  missing marker sorts as frame 0 (`paintsystem/data.py:3032-3045`).
- "Add Action" dialog (`operators/layers_operators.py:935-993`):
  "Bind to", "Once reached" (the action type), then "Frame" (defaults
  to the current frame) or a "Marker" `prop_search`.
- v2 has no masks sub-panel (see PS-029 "v2 UI").

## v3 design

- Each layer node class implements `draw_layer_settings(layout, context)`
  for its per-type box and declares which shared sub-panels it uses:
  `CoordMixin.draw_transform_settings` (PS-008), image settings (PS-020),
  actions (PS-063), masks (PS-015, new).
- `panels/layers_panels.py::draw_layer_settings` orchestrates: type box,
  then sub-panels in the v2 order, keeping the v2 `layout.panel` idnames.
- Parameter nodes (PS-003) are drawn through `ctx.param_node`.

## Acceptance

- Screenshot parity for an image layer (Image + Transform open) and a
  gradient layer (Gradient + Map Range open).
- Quick access row appears when the Transform panel is collapsed and the
  preference is on.
