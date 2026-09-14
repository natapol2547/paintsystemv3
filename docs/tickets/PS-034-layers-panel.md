# PS-034 Layers panel: list, sidebar, menus, warnings

Epic D. Size L. Milestone M1.

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
