# PS-034 Layers panel: list, sidebar, menus, warnings

Epic D. Size L. Milestone M1.

## Status

Partly done (M0 slice 4): `panels/layers_panels.py` has the Layers panel
for the 3D view and the node editor, each a top-level panel in the Paint
System tab as in v2:

- the active layer box with clip, lock alpha, lock, blend mode and
  opacity, split on wide sidebars (`is_clip` and the list's `clipping`
  icon came in slice 5, `lock_alpha` with PS-060 in slice 6);
- the list (PS-011) at `rows=min(max(6, n), 7)` and `scale_y` 1.5;
- the sidebar: Add Layer menu, new folder, remove, move up and down;
  the menu carries `SEARCH_ON_KEY_PRESS` and the "Search..." entry
  Blender's own Add menu uses (`bl_ui/node_add_menu.py`), so a layer
  type can be found by typing;
- the Layer Settings `layout.panel` (PS-035).

The main panel keeps the channel list and moves the compiled shader info
into a collapsed "Compiled Shader" sub-panel, shown only while the
Developer Extras preference is on (PS-039).

Open: `MAT_MT_LayerMenu` (copy, paste and merge are M2), the "not
connected" warning, the warnings box (PS-019), the bake box, the header
preset, the delete confirmation dialog and screenshot parity.

## v2 behaviour

`MAT_PT_Layers` (`panels/layers_panels.py:494-674`), poll: not multiuser,
`ps_object`, active channel. Header icon `layers`; header preset "Use
Baked" (TEXTURE_DATA) when the channel has a bake image.

Body (MESH branch, `:603-674`):

1. Active layer settings box above the list (`layer_settings_ui`,
   `panels/common.py:316-377`): row `is_clip` (SELECT_INTERSECT),
   `lock_alpha` (TEXTURE, image only), `lock_layer` (VIEW_LOCKED),
   `blend_mode`, then opacity slider; responsive `split(factor=0.7)`
   when `region.width - 70*ui_scale > 170*ui_scale`, else stacked with
   an "Opacity" label.
2. "Not connected to material output" warning + "Open Shader Editor".
3. `use_bake_image` -> bake box with `image_node_settings`, "Apply Image
   Filters", "Delete", return.
4. `template_list("MAT_PT_UL_LayerList", ...)` `rows=min(max(6, n), 7)`
   inside `scale_content(row, 1, 1.5)`, with `draw_layer_sidebar`
   (`panels/common.py:483-508`, `scale_x 1.2`): Add Layer menu
   (`layer_add`), new folder (`folder`), `MAT_MT_LayerMenu`
   (DOWNARROW_HLT), separator, delete (`trash`), separator, move up/down
   (TRIA_UP/TRIA_DOWN).
5. Wrapped warnings box (textwrap width 32, ERROR icon on the first line).
6. `layout.panel("layer_settings_panel")` "Layer Settings" (PS-035).

UIList `MAT_PT_UL_LayerList.draw_item` (`:59-108`): indent with
`folder_indent` at the last level and BLANK1 before; row disabled when
the parent folder is disabled; `row.enabled = opacity > 0 and enabled`;
`clipping` icon (scale_x 0.7); type icon via `draw_layer_icon`; name (no
emboss); right side: VIEW_LOCKED, KEYTYPE_KEYFRAME_VEC (actions), LINKED,
warning operator (`error` icon), opacity text `f"{opacity:.1f}"` when
`show_opacity_in_layer_list`, enabled toggle HIDE_OFF/HIDE_ON.

`MAT_MT_LayerMenu` (`:724-785`): Convert to Image Layer, Unlink Layer,
separator, Copy Layer, Copy All Layers, Paste Layer(s), Paste Linked
Layer(s), separator, Merge Up, Merge Down.

### v2 UI

Paths are relative to `~/paintsystem` (commit 8991037). Line numbers
without a file are in `panels/layers_panels.py`.

Placement:

- `MAT_PT_Layers`: `VIEW_3D` / `UI`, `bl_category` "Paint System",
  `bl_label` "Layers" (`:494-499`). `bl_parent_id` is commented out
  (`:500`) and there are no `bl_options`, so it is a top-level panel
  that starts open.
- Order in the tab: submodules register as preferences, main,
  channels, extras, layers, quick tools (`panels/__init__.py:4-12`).
  The only other `VIEW_3D` / `UI` panel in the "Paint System" tab is
  `MAT_PT_PaintSystemMainPanel` (`panels/main_panels.py:117-146`), so
  Layers is the second panel. `MAT_PT_ChannelsPanel`,
  `MAT_PT_ChannelsSettings`, `MAT_PT_Brush` and `MAT_PT_BrushColor` are
  commented out of registration (`panels/channels_panels.py:254-261`,
  `panels/extras_panels.py:500-507`). The other registered `VIEW_3D`
  panels are `INSTANCED` popovers in the `WINDOW` region
  (`panels/main_panels.py:68-73, 84-89`,
  `panels/channels_panels.py:93-98`,
  `panels/extras_panels.py:25-31, 144-149, 309-315`). Quick Tools
  panels use their own "Quick Tools" tab
  (`panels/quick_tools_panels.py:8-13`). The node editor panel
  `NODE_PT_paint_system_shader_editor` is also registered
  (`panels/extras_panels.py:408-414, 506`).
- Poll detail (`:502-507`): the panel hides when
  `check_group_multiuser` counts more than one entry in
  `ps_mat_data.groups`, over all materials, whose `node_tree` is the
  active group's tree (`panels/common.py:197-204`). It also shows for
  Grease Pencil objects that have no active channel.
- The "Use Baked" header preset is drawn only for MESH objects
  (`:523-525`).
- v2 has no Layers panel in the node editor. The shader editor only
  has `NODE_PT_paint_system_shader_editor`
  (`panels/extras_panels.py:408-485`, see PS-037): a groups list and,
  per channel, a closed sub-panel of plain layer rows with node
  inspect buttons. It is mostly read-only, but its rows reuse
  `draw_layer_icon`, so the folder expand toggle and the Solid Color
  swatch are live there on visible layers
  (`panels/extras_panels.py:474-475`).

Body, additions to the steps above:

- Legacy UI (`use_legacy_ui`, default False,
  `panels/preferences_panels.py:80-84`) first draws a box with
  `toggle_paint_mode_ui` (`:531-533`).
- Modern UI: the active-layer row and the list share one outer box,
  `layout.row().box()`. The row is a nested `box.box()`, drawn only
  when the active layer has a node tree (`:604-610`,
  `panels/common.py:319-320`). Legacy UI draws a plain `layout.box()`
  and moves the row into the top of Layer Settings (`:611-612`,
  `:175-177`). The row's contents and scales are in PS-035 "v2 UI".
- Not-connected warning (`:623-631`): `draw_warning_box` is an alert
  box with an aligned column (`panels/common.py:511-528`). The lines
  are "Paint System not connected" (ERROR) and "to material output!"
  (BLANK1). The "Open Shader Editor" (NODETREE) button runs
  `paint_system.focus_ps_node`. It appears only when no `NODE_EDITOR`
  area is open.
- The warning's test is `find_node` on the material tree
  (`utils/nodes.py:79-114`). It starts at `get_material_output` and
  follows links in both directions. So it checks that the group node
  is in the same connected part of the graph as the output. It does
  not check that a channel socket is linked.
- `get_material_output` returns the active Material Output, else a
  Group Output. Otherwise it returns the last node of the tree,
  because the loop variable is reused. It calls `nodes.new` for a
  Material Output only when the tree has no nodes
  (`utils/nodes.py:36-48`). That call is reachable from the panel
  draw; whether Blender allows the write there was not checked.
- Bake box (`:633-641`), shown while the channel's `use_bake_image` is
  on: a `layout.box()` column with these items:
  - the `image_node_settings` sub-panel for the bake image, simple UI,
    closed (`panels/common.py:207-252`, described in PS-035);
  - "Apply Image Filters" (IMAGE_DATA), a `wm.call_menu` for
    `MAT_MT_ImageFilterMenu`;
  - "Delete" (TRASH), which runs `paint_system.delete_bake_image`
    ("Delete Baked Image", `operators/bake_operators.py:511`).
- The draw returns after the bake box. The list, warnings box and
  Layer Settings are not drawn while the baked image is in use. In
  modern UI there is no outer box in this state, so the not-connected
  warning draws straight into the panel (`:535`, `:604`).
- List row (`:644-653`): `row = box.row()`. The list goes in
  `row.column()` and the sidebar in `row.column(align=True)`.
  `scale_content(row, 1, 1.5)` scales both. It does nothing when the
  `use_compact_design` preference is on (`panels/common.py:14-20`).
- `template_list("MAT_PT_UL_LayerList", "", active_channel, "layers",
  active_channel, "active_index", ...)`: `active_index` indexes the raw
  `layers` collection. Display order comes from `filter_items`.
- With no active layer the draw stops after the list (`:655-657`).
- Warnings box (`:659-669`): `layout.box()` with `alert = True` and an
  aligned column. Each warning is wrapped with
  `textwrap.TextWrapper(width=32)`.
- The warning texts are built in `get_layer_warnings`
  (`paintsystem/data.py:1439-1474`, PS-019):
  - "Last layer in folder. Blending may not work. Use folder with
    Passthrough blend mode."
  - "No layer below. Blending may not work."
  - "No layer below. Adjustment effects may not work."
  - "Input Alpha of {channel} channel is 0. Blending may not work."
- Layer Settings: `layout.panel("layer_settings_panel")` has no
  `default_closed`, so it starts open. The header label is "Layer
  Settings" (`:671-674`).
- Grease Pencil objects get a separate body (`:536-600`, PS-082):
  - Blender's `template_grease_pencil_layer_tree`;
  - a sidebar with layer add (ADD), group add (NEWFOLDER), remove
    (REMOVE), `GREASE_PENCIL_MT_grease_pencil_add_layer_extra`
    (DOWNARROW_HLT) and move (TRIA_UP / TRIA_DOWN);
  - a "Layer Settings" panel with masks, lock, blend, opacity and "Use
    Lights" (LIGHT);
  - a closed "Onion Skinning" panel.

UIList `MAT_PT_UL_LayerList` (`:58-142`), additions:

- Nothing is drawn for an item whose `get_layer_data()` is None, such
  as a linked entry whose source is gone (`:61-63`,
  `paintsystem/data.py:1524-1536`).
- A linked entry shows and edits the source layer's name, opacity and
  toggles, because the row uses `linked_item` (`:84-107`).
- `main_row.enabled = False` when the direct parent is disabled
  (`:73-76`). That makes the whole row non-interactive: rename, eye
  toggle and folder toggle. Only the direct parent is checked, not
  grandparents.
- `row.enabled = opacity > 0 and enabled` covers only the sub-row with
  the indent, clip icon and type icon (`:78-89`). The name and the
  right-side icons are not dimmed.
- Because that is `enabled`, not `active`, the type-icon slot is also
  non-interactive on a hidden or 0-opacity layer. A hidden folder's
  `is_expanded` toggle and a hidden Solid Color swatch cannot be used
  (`panels/common.py:531-573`). v3 uses `.active` for both rules, which
  only greys the row out.
- `main_row.separator()` sits between the type icon and the name
  (`:90`).
- The lock icon is `icon_parser('VIEW_LOCKED', 'LOCKED')`. The actions
  marker shows when `len(actions) > 0` (`:95-98`).
- The warning button runs `paint_system.show_layer_warnings` ("Layer
  Warnings") with `layer_id`
  (`operators/layers_operators.py:1014-1046`).
  It opens `invoke_props_dialog(width=260)`: a box where each warning
  is split into 6-word lines, ERROR on the first line and BLANK1 on the
  rest. The panel box wraps at 32 characters instead.
- The opacity label is the 0-1 value with one decimal.
  `show_opacity_in_layer_list` defaults to True
  (`panels/preferences_panels.py:29-33`).
- `draw_custom_properties` (`:108`, `:140-142`) is dead code: no layer
  has a `custom_int` property.
- `filter_items` (`:110-138`): every item starts visible. An item is
  hidden when any ancestor has `is_expanded` False (default True,
  `paintsystem/data.py:1260-1265`).
- The new order is each item's index in `flattened_unlinked_layers`.
  That is a depth-first walk that sorts siblings by `order`
  (`paintsystem/nested_list_manager.py:176-189`).
- The "Filtering by name" comment is wrong. Typing in the list's name
  filter field has no effect.
- Per-row cost: `get_layer_warnings` flattens the channel and runs
  `find_node` over the material for every row (`:70`,
  `paintsystem/data.py:1439-1456`). `is_layer_linked` counts uids
  across every layer of every material
  (`paintsystem/data.py:3025-3030`).
- Clicking a row sets `active_index`. Its update,
  `update_active_image` (`paintsystem/data.py:252-277, 2328`), does
  this (PS-060):
  - switches `image_paint.mode` from MATERIAL to IMAGE;
  - sets the canvas to the layer image, or None when the layer is
    locked, missing, or the bake image is in use;
  - activates the layer's UV map for UV, or the Paint System UV map for
    AUTO;
  - in Texture Paint, sets `brush.use_alpha = not lock_alpha`
    (`paintsystem/data.py:240-250`).
- Renaming a row renames the layer's node tree to
  `.PS {name} ({uid[:8]})`. For an image layer it also renames the
  image (`paintsystem/data.py:820-823, 876-890`).

Sidebar (`panels/common.py:483-508`), additions:

- In modern UI both separators are `line_separator`: a LINE separator
  on Blender 4.2 and later, otherwise a plain one
  (`panels/common.py:379-383`). Legacy UI uses plain separators and has
  no folder button (`panels/common.py:492-506`).
- The folder button runs `paint_system.new_folder_layer` ("New
  Folder", name "Folder") without a dialog
  (`operators/layers_operators.py:144-166`).
- Every add operator inserts at the cursor
  (`paintsystem/data.py:2022-2069`,
  `paintsystem/nested_list_manager.py:48-84`):
  - with a folder active, at the top of that folder;
  - otherwise at the active item's `order` under the same parent,
    which puts it directly above the active item;
  - with no active item, at the top of the root level
    (`paintsystem/nested_list_manager.py:53-54`).
  - The new layer becomes active.
- Delete runs `paint_system.delete_item` ("Remove Item",
  `operators/layers_operators.py:622-655`). Poll: an unlinked active
  layer exists.
  - `invoke_props_dialog` shows "Delete '{name}' ?" (ERROR) and "Click
    OK to delete, or cancel to keep the layer".
  - It removes the item and its children
    (`paintsystem/data.py:2083-2099`).
  - The new active item is the one now at the deleted item's order
    under the same parent. If there is none, the loop leaves
    `active_index` on the last entry of the raw `layers` collection,
    which is not necessarily the last displayed row
    (`paintsystem/data.py:2093-2098`).
- Move runs `paint_system.move_up` / `move_down` ("Move Item Up" /
  "Move Item Down", `operators/layers_operators.py:658-817`). Poll:
  `get_movement_options` is not empty.
  - When the only option is SKIP, the item moves at once. `invoke`
    calls `process_material` directly
    (`operators/layers_operators.py:697-699, 777-779`), so this path
    skips `MultiMaterialOperator.execute` and moves only in the active
    material.
  - Otherwise `popup_menu` titled "Move Options" lists one entry per
    option. Each entry re-runs the same operator with `action` set
    (`paintsystem/data.py:2499-2520`). Popup menu entries run in
    `EXEC_REGION_WIN`, so they call `execute` and reach every selected
    object.
  - Entry labels (`paintsystem/nested_list_manager.py:241-307`):
    "Move out of '{folder}'", "Move into '{folder}'", "Skip over".
    MOVE_ADJACENT also uses "Move into", naming the neighbour's parent,
    so it reads "Move into 'root'" when that neighbour is at root level
    (`paintsystem/nested_list_manager.py:269-273, 300-304, 309-310`).
  - The UP branch's `item.order == 0` MOVE_OUT test
    (`paintsystem/nested_list_manager.py:279-283`) is dead in practice.
    `normalize_orders` numbers siblings from 1 (same file, `204-217`).
    The one path that sets order 0, MOVE_INTO_TOP (`362-368`), leaves
    the item first in its folder, where the "parent is above" case has
    already returned (`259-263`).
  - Actions: UP has MOVE_INTO, MOVE_ADJACENT, MOVE_OUT, SKIP. DOWN has
    MOVE_OUT_BOTTOM, MOVE_INTO_TOP, MOVE_ADJACENT, SKIP (PS-012).
- Add, delete and move are `MultiMaterialOperator`s
  (`operators/common.py:32-77`, PS-066). `multiple_objects` defaults to
  True, so `execute` runs `process_material` on the active material of
  every selected mesh object except "PS Camera Plane". The single-SKIP
  move above is the exception. The delete dialog and the move popup
  name only the active object's layer.

`MAT_MT_LayerMenu` (`:724-785`), additions:

- `bl_label` is "Layer Menu".
- "Convert to Image Layer" (custom `image` icon) shows when the active
  layer type is not IMAGE or ADJUSTMENT. "Unlink Layer" (UNLINKED)
  shows when the layer is linked. The first separator is drawn only
  when either of them is shown (`:732-750`).
- Icons: Copy Layer and Copy All Layers use COPYDOWN. Paste Layer(s)
  uses PASTEDOWN with `linked=False`. Paste Linked Layer(s) uses LINKED
  with `linked=True`. Merge Up uses TRIA_UP_BAR and Merge Down uses
  TRIA_DOWN_BAR.
- `paint_system.convert_to_image_layer` has `bl_label` "Transfer Image
  Layer UV" and `bl_description` "Transfer the UV of the image layer",
  both copied from the transfer operator
  (`operators/bake_operators.py:609-612`). Its poll requires only a
  type other than IMAGE (`operators/bake_operators.py:615-618`), so
  the entry also shows, and passes the poll, for FOLDER layers. It
  opens the bake dialog through `invoke_props_dialog`
  (`operators/bake_operators.py:81-89`, PS-018).
- Copy (`operators/layers_operators.py:820-864`, PS-017):
  - the clipboard is scene data, `ps_scene_data.clipboard_layers`
    (material plus uid);
  - Copy Layer (poll: active layer) clears it and stores the unlinked
    layer;
  - Copy All Layers stores every layer of the channel in display order.
- Paste (`operators/layers_operators.py:867-912`), poll: the clipboard
  is not empty.
  - The first layer goes in at the cursor and the rest after it, with
    parent links rebuilt. With a folder active, they go into it.
  - A plain paste copies the layer data.
  - A linked paste creates BLANK (or FOLDER) entries with
    `linked_layer_uid` and `linked_material` (PS-016).
  - Copy and paste are plain operators and act on the active material
    only.
- Unlink Layer calls `unlink_layer_data` on the entry
  (`operators/layers_operators.py:915-932`,
  `paintsystem/data.py:1509`).
- Merge Down / Merge Up (`operators/bake_operators.py:692-813,
  814-932`), poll:
  - neither the layer nor its neighbour in flattened order is a
    folder;
  - both share a parent and are enabled;
  - the lower layer of the pair does not modify colour data, meaning
    it is not an Attribute layer, not a Gradient Map, and its blend
    mode is MIX (`paintsystem/data.py:1604-1606`). For Merge Down that
    is the layer below (`operators/bake_operators.py:722`). For Merge
    Up it is the active layer itself
    (`operators/bake_operators.py:844`).
  - The dialog includes "This operation will convert the current layer"
    (INFO) and "into an image layer."
    (`operators/bake_operators.py:748-749, 869-870`).

## v3 design

- `panels/layers_panels.py` implements the panel over `tree.layer_rows`
  (PS-011) and the registry (PS-029). Opacity binds to `node.opacity`
  (in v2 it was the Pre Mix socket).
- "Not connected" check: the material's group node output for the active
  channel is linked to something reaching the Material Output.
- Sidebar operators: `paint_system.new_folder_layer`, `delete_layer`
  (confirmation dialog like v2 `delete_item`), `move_up`, `move_down`
  (PS-012).
- Warnings from `node.warnings(context)` (PS-019).
- Add "Duplicate Layer" to the layer menu (PS-018).

## Acceptance

- Screenshot parity with v2 for: flat stack, nested folders collapsed and
  expanded, clipped layer, locked layer, warning layer.
- All sidebar and menu entries map to working operators; missing
  features (M2 items) are greyed with `layout.enabled = False`, not
  absent, so the layout is stable from M1.
