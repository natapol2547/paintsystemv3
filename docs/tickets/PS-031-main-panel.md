# PS-031 Main panel, header presets, groups popover, material settings

Epic D. Size M. Milestone M1.

## v2 behaviour

`MAT_PT_PaintSystemMainPanel` (`panels/main_panels.py:117-262`), category
"Paint System", poll `ps_object is not None`:

- Header: `sunflower` icon label. Header preset (right): when groups
  exist, popover `MAT_PT_PaintSystemGroups` (NODETREE, only if more than
  one group), `paint_system.new_group` (ADD), `wm.call_menu
  MAT_MT_DeleteGroupMenu` (REMOVE); else only the ADD button.
- Body: multiuser warning + "Fix Data Duplication" and return; no group ->
  2x scaled "Add Paint System" button and return; `toggle_paint_mode_ui`
  block (PS-061 / PS-033); `layout.panel("MAT_PT_ChannelsPanel")` (PS-032);
  `layout.panel("MAT_PT_Brush")` and `layout.panel("MAT_PT_BrushColor")`
  (PS-033).
- `MAT_PT_PaintSystemGroups` popover (`:68-81`, ui_units_x 12) with
  `MATERIAL_UL_PaintSystemGroups` (name only, no emboss).
- `MAT_PT_PaintSystemMaterialSettings` popover (`:84-115`): material slot
  menu, name, group popover + ADD + delete menu, `surface_render_method`,
  `use_backface_culling`.
- `MAT_MT_DeleteGroupMenu` (`:264`): alert "Remove Paint System" (TRASH).
- Legacy-data box and legacy UI branches are not ported.

### v2 UI

All paths are relative to `~/paintsystem`. Line numbers without a file
refer to `panels/main_panels.py`.

Placement:

- Panel modules register in this order: `preferences_panels`,
  `main_panels`, `channels_panels`, `extras_panels`, `layers_panels`,
  `quick_tools_panels` (`panels/__init__.py:4-12`). No class sets
  `bl_order`. In the 3D view sidebar tab "Paint System" the main panel
  comes first. `MAT_PT_Layers` follows as a separate top-level panel,
  because its `bl_parent_id` is commented out
  (`panels/layers_panels.py:494-500`).
- `MAT_PT_PaintSystemMainPanel` (`:117-122`): VIEW_3D / UI, label
  "Paint System", category "Paint System", no `bl_options`, so it starts
  open. Poll `ps_object is not None` (`:139-142`). `ps_object` is a
  mesh, a grease pencil object (4.3+), or an empty whose parent is a mesh
  with an active material (`paintsystem/context.py:36-49`). The empty
  test is `hasattr(parent.active_material, 'ps_mat_data')`, which holds
  for any material, so it does not check for Paint System data.

Header:

- `draw_header` (`:144-146`): a label with the custom `sunflower` icon
  and no text.
- `draw_header_preset` (`:124-137`): one `row(align=True)`, all buttons
  `text=""`. Nothing is drawn when `ps_mat_data` is None. With groups:
  popover `MAT_PT_PaintSystemGroups` (NODETREE, only when there is more
  than one group), `paint_system.new_group` (ADD), and `wm.call_menu`
  with `name = "MAT_MT_DeleteGroupMenu"` (REMOVE). Without groups: only
  the ADD button. The header buttons stay visible in every body state,
  including the multi-user warning.

Body (`draw`, `:148-262`), top to bottom. `use_property_split` is on and
decorate is off for the whole panel.

1. v1 data box (`:152-170`), drawn when the active material still has
   v1 `paint_system` groups (`LegacyPaintSystemContextParser`,
   `paintsystem/data.py:3182-3215`). A box holds a column with an inner
   box. The inner box has an alert column with "Legacy Paint System
   Detected" (ERROR) and "Please save as before updating". Below it are
   a row with `wm.save_as_mainfile` "Save As" and an alert row with
   `paint_system.update_paint_system_data` "Update Paint System Data"
   (FILE_REFRESH, `operators/versioning_operators.py:67`). The draw
   returns after the box.
2. Paint mode block (`:172-173`): `toggle_paint_mode_ui` when
   `use_legacy_ui` is off and a channel is active. It is drawn before
   the object type check.
3. Objects that are not meshes stop here (`:175-176`). This makes the
   grease pencil colour branch at `:261-262` unreachable.
4. Material row (`:178-190`), legacy UI only, drawn when at least one
   slot holds a material. A `column(align)` holds a `row(align)` scaled
   1.5 x 1.2 with menu `MAT_MT_PaintSystemMaterialSelectMenu` (text ""
   and MATERIAL, or "Empty Material" and MESH_CIRCLE). When the active
   slot holds a material, the row adds the material `name` (text "") and
   popover `MAT_PT_PaintSystemMaterialSettings` (PREFERENCES)
   (`:185-190`). An "add material slot" button is commented out
   (`:188`).
5. Multi-user warning (`:193-200`). `check_group_multiuser`
   (`panels/common.py:197-204`) counts the groups in all materials whose
   `node_tree` is the active group's tree, and triggers above one. It
   draws `draw_warning_box` (`panels/common.py:511-528`, an alert box
   with an aligned column) with "Duplicated Paint System Data" (ERROR).
   Under it is a row scaled 1.5 x 1.5 with
   `paint_system.duplicate_paint_system_data` "Fix Data Duplication".
   The draw then returns. That operator
   (`operators/utils_operators.py:288-352`, bl_label "Duplicate Paint
   System Data") is a `MultiMaterialOperator` but overrides `execute`,
   so it only acts on the active material. It gives every group and
   channel a new node tree, duplicates the data of non-linked layers,
   rebuilds, and reconnects the group node sockets. `MAT_PT_Layers`
   polls false in the same state (`panels/layers_panels.py:505-506`).
6. No active group (`:202-207`): a row with `scale_x = 2` and
   `scale_y = 2`, set directly rather than through `scale_content`, with
   `paint_system.new_group` "Add Paint System" (ADD). The draw returns.
7. Channels section (`:211-224`), when `poll_channels_panel` passes. See
   PS-032.
8. Brush section (`:225-235`), when `poll_brush_settings` passes
   (`panels/extras_panels.py:61-63`). It uses
   `layout.panel("MAT_PT_Brush", default_closed=True)` with the header
   label "Brush" and the `brush` icon. With `show_tooltips` on, the
   header also gets popover `MAT_PT_BrushTooltips` (text "", INFO_LARGE
   on 4.3+, else INFO). The body is `draw_brush_settings`
   (`panels/extras_panels.py:65-98`).
   - `MAT_PT_BrushTooltips` (`panels/extras_panels.py:25-59`): VIEW_3D /
     WINDOW, INSTANCED, `bl_ui_units_x = 8`. A column of shortcut rows,
     each drawn as one label per key icon with the text on the last:
     "Toggle Erase Alpha", "Eyedropper", and "Scale Brush Size" (only
     when a "Radial Control" keymap item is found). Then a separator,
     `paint_system.open_paint_system_preferences` "Preferences"
     (PREFERENCES), and `wm.url_open` "Suggest more!" (URL) to the v2
     GitHub issues page.
9. Color section (`:236-262`), when `poll_brush_color_settings` passes
   (`panels/extras_panels.py:160-187`). It uses
   `layout.panel("MAT_PT_BrushColor", default_closed=True)` with the
   header label "Color" and the `color` icon.
   - Open: a header row (align, `scale_x` 1.1, alignment RIGHT) holds
     popover `MAT_PT_BrushColorSettings` "Settings" (SETTINGS). The body
     is `draw_brush_color_settings`.
   - Closed: a header row with alignment RIGHT holds
     `split(factor=0.5, align=True)` with the unified primary and
     secondary colour swatches (`UnifiedPaintPanel.prop_unified_color`,
     text ""), then `paint.brush_colors_flip` (FILE_REFRESH).

All three `layout.panel` sections start closed. The `MAT_PT_Brush` and
`MAT_PT_BrushColor` classes are still defined
(`panels/extras_panels.py:100-141, 276-306`), but they are commented
out of the `classes` tuple (`panels/extras_panels.py:500-507`), so they
are never registered. Only their idnames are used, as the keys for the
open state.

Paint mode block (`toggle_paint_mode_ui`, `panels/common.py:269-314`).
Line numbers in this list refer to `panels/common.py`:

- A `column(align)` holds a `row(align)` scaled 1.7 x 1.7. An inner row
  holds `paint_system.toggle_paint_mode` "Toggle Paint Mode" with the
  `paintbrush` icon, depressed when `context.mode == 'PAINT_TEXTURE'`.
  For meshes only this inner row is disabled while the active channel
  uses its baked image (`:294`).
- `paint_system.isolate_active_channel` (text ""), depressed while
  `preview_channel` is on. While previewing, the icon is the active
  channel's socket icon; otherwise it is `channel`. The button needs a
  group node that feeds the Material Output (`find_node` defaults to
  `connected_to_output=True`, `utils/nodes.py:79`), and one of these:
  the setup is not the basic one, the group has more than one channel,
  or preview is on (`:283-287`). `is_basic_setup` (`:255-266`) checks
  that the output chain holds a group node, a Mix Shader and a
  Transparent BSDF. It also returns True when at most one node is
  connected to the output (`:259-260`).
- `wm.save_mainfile` (text "", `save` icon).
- Meshes only: a Normal tip box (`:295-307`). It shows when
  `show_tooltips` is on, `hide_norm_paint_tips` is off, the template is
  NORMAL or PBR, and the active channel is named "Normal". The box has
  `scale_x = 1.4` (`:300`). It holds a row with an aligned column of the
  labels "The button above will" / "show object normal", an `arrow_up`
  icon label, and `paint_system.hide_painting_tips` (X) with
  `attribute_name = 'hide_norm_paint_tips'`. A scaled row created at
  `:296-298` is left empty.
- Meshes only: a row scaled 1.5 x 1.3 with menu
  `MAT_MT_PaintSystemMergeAndExport` "Bake and Export" (`:309-314`).

`MAT_MT_PaintSystemMergeAndExport` (`panels/layers_panels.py:145-168`,
bl_label "Baked and Export"), in order:

- If the active channel has a bake image: `use_bake_image` "Use Baked
  Image" (CHECKBOX_HLT or CHECKBOX_DEHLT), then a separator.
- Label "Bake". `paint_system.bake_channel` "Bake Active Channel" (the
  channel's socket icon). `paint_system.bake_channel` "Bake Active
  Channel as Layer" (`image` icon, `as_layer = True`). Without legacy UI:
  `paint_system.bake_all_channels` "Bake All Channels" (`channels`
  icon).
- Separator, label "Export Baked Images". If a bake image exists:
  `paint_system.export_image` "Export Active Channel" (EXPORT) and
  `paint_system.delete_bake_image` "Delete Active Channel" (TRASH).
  Without legacy UI: `paint_system.export_all_images` "Export All
  Channels" (EXPORT).

Popovers and menus:

- `MAT_PT_PaintSystemGroups` (`:68-81`): VIEW_3D / WINDOW, INSTANCED,
  `bl_ui_units_x = 12`. It draws a label "Groups", applies
  `scale_content`, then
  `template_list("MATERIAL_UL_PaintSystemGroups", "", ps_mat_data,
  "groups", ps_mat_data, "active_index")` with the default row count.
  The row (`:60-65`) is only `prop(item, "name", text="",
  emboss=False)`. It has no icon, and double-click renames the group.
- `MAT_MT_DeleteGroupMenu` (`:264-274`): bl_label "Delete Group". It
  forces the INVOKE operator context (`ensure_invoke_context`,
  `panels/common.py:453-461`), sets `layout.alert`, and holds one item:
  `paint_system.delete_group` "Remove Paint System" (TRASH). That
  operator (`operators/group_operators.py:405-463`, bl_label "Delete
  Paint System") opens a props dialog titled "Delete Group", width 300.
  The dialog has a box with an alert column: "Danger Zone!" (ERROR) and
  "Are you sure you want to delete Paint System?" (BLANK1). Its
  `bake_channels` property is never drawn and does nothing
  (`operators/group_operators.py:428-429`). See PS-042.
- `MAT_PT_PaintSystemMaterialSettings` (`:84-115`): VIEW_3D / WINDOW,
  INSTANCED, 12 units, property split on, decorate off. It is opened
  only from the legacy UI material row (`:190`), so its non-legacy
  branch (`:99-104`, material menu and name) never draws. Body:
  `surface_render_method` "Render Method" and `use_backface_culling`
  "Backface Culling". When groups exist, a box follows with the label
  "Paint System Node Groups:" (`sunflower`). Its `row(align)` is scaled
  1.3 x 1.2 and holds the groups popover (NODETREE), the active group
  `name`, `new_group` (ADD), and the delete-group menu (REMOVE).
- `MAT_MT_PaintSystemMaterialSelectMenu` (`:29-46`): one
  `paint_system.select_material_index` per slot. The text is the
  material name or "Empty Material", the icon MATERIAL or MESH_CIRCLE,
  and the active slot is depressed. The operator only sets
  `active_material_index` on meshes
  (`operators/utils_operators.py:71-88`). A template select menu at
  `:49-58` is commented out.

"Add Paint System" dialog (`paint_system.new_group`,
`operators/group_operators.py:73-381`, bl_label "New Group", a
`MultiMaterialOperator`, so it runs on every selected mesh's active
material, `operators/common.py:32-77`). Line numbers in this part refer
to `operators/group_operators.py`. `invoke` (`:311-323`) makes the name
unique, reads the coordinate type, picks PAINT_OVER when the node tree
is complex under EEVEE, leaves Edit mode, and opens a 300-wide props
dialog. `draw` (`:325-381`), in order:

- "Applying to all selected objects" (INFO) in a box when more than one
  object is selected (`operators/common.py:83-86`).
- "Paint Over is not supported in this render engine" (ERROR) whenever
  the engine is not EEVEE, whatever the chosen template (`:329-331`).
- A row scaled 1.5 x 1.5 with `template` "Template" as a dropdown. The
  items (`paintsystem/data.py:91-97`) are "Blank Canvas" (BASIC, IMAGE),
  "Paint Over" (PAINT_OVER, custom `paintbrush`), "PBR" (MATERIAL),
  "Normals Painting" (NORMAL, NORMALS_VERTEX_FACE) and "None" (NONE,
  NONE). The items callback drops PAINT_OVER when the engine is not
  EEVEE (`:79-83`), so the warning above shows while the option is
  already gone.
- PBR only: a box with a centred "PBR Channels:" (MATERIAL) and an
  aligned column of toggles: "Color" (`color_socket`), "Metallic" and
  "Roughness" (`float_socket`), "Normal" (`vector_socket`). Color and
  Normal default on, Metallic and Roughness off (`:114-136`).
- A row scaled 1.5 x 1.5 with `add_layers` "Add Template Layers"
  (`layer_add`, default on, `:138-142`).
- With `add_layers`: a box with `select_coord_type_ui`
  (`operators/common.py:174-200`). It draws a row with "Coordinate
  System" (`transform`) and the toggle "Use AUTO UV?". With AUTO UV, an
  info box reads "Will create a new UV Map: <name>" (alert, ERROR) or
  "Using UV Map: <name>" (INFO). Otherwise a `coord_type` dropdown
  follows. A non-UV choice adds an alert box "Painting in 3D may not
  work" (ERROR) / "Open Blender Image Editor to paint" (BLANK1). A UV
  choice adds a `prop_search` over the mesh UV maps, alert when empty.
- A box with `box.panel("advanced_settings_panel",
  default_closed=True)`, header "Advanced Settings:" (TOOL_SETTINGS).
  Inside is `split(factor=0.4)` with "Group Name:" and the name field
  (NODETREE). BASIC also adds "Use Smooth Alpha" (`use_alpha_blend`,
  default off). When it is on, an alert box follows with a row holding
  an ERROR icon label and an aligned column "Warning: Smooth Alpha
  (Alpha Blend)" / "may cause transparency artifacts." (`:369-376`).
  Then "Use Backface Culling" (`disable_show_backface`, default on).
  BASIC with a view transform other than Standard adds "Use Standard
  View Transform" (`set_view_transform`, default on, `:379-381`).

Legacy UI switch (`use_legacy_ui`, default False,
`panels/preferences_panels.py:80-84`, `preferences.py:17`):

- The paint mode block leaves the main panel (`:172`). It is drawn in a
  box at the top of `MAT_PT_Layers` instead
  (`panels/layers_panels.py:531-533`).
- The material row and the Material Settings popover appear
  (`:178-190`).
- The channels box gains a "Bake and Export" menu
  (`panels/channels_panels.py:117-118`).
- `MAT_MT_PaintSystemMergeAndExport` hides "Bake All Channels" and
  "Export All Channels" (`panels/layers_panels.py:160, 167`).
- The layer sidebar drops the New Folder button and uses plain
  separators (`panels/common.py:490-508`). `layer_settings_ui` draws a
  different layout (`panels/common.py:323-346`), and it moves from a box
  above the layer list (`panels/layers_panels.py:605-610`) into a box at
  the top of the layer settings (`panels/layers_panels.py:175-177`).

Other preferences that change this panel:

- `use_compact_design` turns off every `scale_content` call
  (`panels/common.py:14-20`), but not the fixed 2 x 2 and 1.7 x 1.7
  scales.
- `show_tooltips` controls the Brush tooltip popover and the Normal tip.
- `developer_mode` is described as "Enable developer mode for verbose
  logging" (`panels/preferences_panels.py:92-96`). It only switches the
  log level (`utils/logging.py:29`). No v2 panel draws a compiled-info
  block.

## v3 design

- Rewrite `panels/main_panels.py::PAINTSYSTEM_PT_main_3dview` to the v2
  structure, using `PSContext` (PS-030), `material_settings.trees`
  (PS-040) for the groups list, and the same icons.
- "Fix Data Duplication" becomes "Make Single User" (PS-043); the
  condition is `material.users > 1` and the tree shared by several
  materials.
- Sub-panels use `layout.panel()` with the v2 idnames so open/closed
  state is remembered per file exactly like v2.
- The node editor panel keeps the compiled-info block for developers,
  gated by the `developer_mode` preference.

## Acceptance

- Side-by-side screenshot with v2 on a fresh cube: before setup, after
  setup with one group, with two groups. Same widgets in the same
  positions.
- Sub-panel open/closed state persists across file save/load.
