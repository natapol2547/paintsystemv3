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
