# PS-038 Quick Tools panels

Epic D. Size S. Milestone M2.

## v2 behaviour

`panels/quick_tools_panels.py`, sidebar category "Quick Tools":

- Display (`:8`): wireframe toggle, `toggle_transform_gizmos`
  (`utils_operators.py:355`, stores gizmo flags on the WindowManager in
  paint modes).
- Mesh (`:46`): primitive add row, normals check / `recalculate_normals`
  / `flip_normals` (`utils_operators.py:216-260`), non-uniform scale alert
  box with Apply Transform, Set Origin.
- Paint (`:120`, poll `obj.mode == 'TEXTURE_PAINT'`): "Edit Externally"
  and `add_preset_brushes` (`utils_operators.py:60`). Only the preset
  brushes are ported; "Edit Externally" goes with the external-editor
  round trip PS-054 drops.

### v2 UI

All paths are relative to `~/paintsystem`. Line numbers without a file
refer to `panels/quick_tools_panels.py`.

Corrections to the summary above:

- The Display panel has no `toggle_transform_gizmos` button. It draws
  the viewport's own gizmo properties directly (`:32-43`). No panel,
  menu or keymap references `paint_system.toggle_transform_gizmos`
  (`operators/utils_operators.py:355-407`), so it is dead from the UI.
- The Paint panel holds only "Edit Externally" (`:138-142`). "Add Preset
  Brushes" is drawn in the Brush sub-section of the main panel instead
  (`panels/extras_panels.py:78-85`). See PS-031.

Placement:

- Three top-level panels: VIEW_3D / UI, category "Quick Tools", a
  separate sidebar tab from "Paint System". They are registered in the
  order Display, Mesh, Paint (`:145-149`), with no `bl_order` and no
  `bl_options`, so all three start open. Each has a
  `bl_parent_id = 'MAT_PT_PaintSystemQuickTools'` line commented out
  (`:14, 52, 126`).
- The quick tools module is registered last among the panel modules
  (`panels/__init__.py:4-12`).
- Display and Mesh have no poll, so they show for any object in any
  mode, with or without Paint System data. Paint polls
  `active_object.mode == 'TEXTURE_PAINT'` (`:128-132`).

Display, `MAT_PT_PaintSystemQuickToolsDisplay` (`:8-43`). `draw_header`
adds a HIDE_OFF icon label with no text (`:16-18`), in front of the
bl_label "Display":

- One box.
- With an active object: a row through `scale_content` (1.2 x 1.2) with
  `obj.show_wire` "Toggle Wireframe" (MOD_WIREFRAME). This is the
  per-object wireframe display, not the overlay.
- A second row with `space.show_gizmo` "Toggle Gizmo" (GIZMO), then a
  nested `row(align=True)` with three icon toggles, all `text=""`:
  `show_gizmo_object_translate` (EMPTY_ARROWS),
  `show_gizmo_object_rotate` (FILE_REFRESH) and
  `show_gizmo_object_scale` (MOD_MESHDEFORM). `space` is
  `context.area.spaces[0]`. The row sets its scale to 1 when compact
  design is off (`:33-35`), which has no visible effect.

Mesh, `MAT_PT_PaintSystemQuickToolsMesh` (`:46-117`), header icon
MESH_CUBE. Three boxes:

1. Add Mesh (`:67-84`): a centred label "Add Mesh:" (PLUS). Below it is
   a centred row scaled 1.5 x 1.5 of icon buttons, all `text=""`:
   - `"primitive_plane_add"` (IMAGE_PLANE) with `op.align = 'VIEW'`
     (`:74-76`). The idname lacks the `mesh.` prefix, so it is invalid.
     `UILayout.operator` returns None for an unknown operator and draws
     no button. This was checked in Blender 5.2.1 and 4.2.23 ("unknown
     operator"). `op.align` then raises AttributeError ("'NoneType'
     object has no attribute 'align'"), so the Mesh panel draw stops at
     `:76`. The intended button is a view-aligned plane. The bug came in
     with v2 commit d3d514a ("remove: PAINTSYSTEM_OT_AddCameraPlane"),
     which replaced `paint_system.add_camera_plane` with this call.
   - `mesh.primitive_plane_add` (MESH_PLANE),
     `mesh.primitive_cube_add` (MESH_CUBE),
     `mesh.primitive_circle_add` (MESH_CIRCLE) and
     `mesh.primitive_uv_sphere_add` (MESH_UVSPHERE), all with default
     options. Because of the error above, v2 never draws these four
     buttons or the Normals and Transforms boxes below. The layout is
     what the code describes, not what v2 shows.
2. Normals (`:86-98`): a centred label "Normals:" (NORMALS_FACE).
   - A row scaled 1.5 x 1.5 with `overlay.show_face_orientation`
     "Toggle Check Normals". The icon is HIDE_OFF when it is on and
     HIDE_ON when it is off.
   - An unscaled row with `paint_system.recalculate_normals`
     "Recalculate" (FILE_REFRESH) and `paint_system.flip_normals` "Flip"
     (DECORATE_OVERRIDE).
3. Transforms (`:100-117`): a centred label "Transforms:"
   (EMPTY_ARROWS).
   - An alert box with an aligned column: "Object is not uniform!"
     (ERROR) and "Apply Transform -> Scale" (BLANK1). Its condition is
     `scale[0] != 1 or scale[1] != 1 or scale[0] != 1` (`:104`). It
     tests for "scale is not 1" rather than non-uniform scale, and it
     checks X twice and never Z.
   - A row scaled 1.5 x 1.5 with `menu("VIEW3D_MT_object_apply",
     text="Apply Transform", icon="LOOP_BACK")`.
   - A row scaled 1.5 x 1.5 with `operator_menu_enum("object.origin_set",
     "type", text="Set Origin", icon="EMPTY_AXIS")`.

Paint, `MAT_PT_PaintSystemQuickToolsPaint` (`:120-142`), header icon
BRUSHES_ALL:

- A row scaled 1.5 x 1.5 with `paint_system.quick_edit` "Edit
  Externally" (IMAGE). The operator (`operators/quick_edit.py:136-299`,
  bl_label "Quick Edit") sets a save directory in `invoke` and opens
  `invoke_props_dialog` (`:221-228`), drawn at `:230-299`. Nothing else
  is in the panel. When PS-054 drops this operator the panel has no
  content left.

Operators the panels reach:

- `paint_system.recalculate_normals`
  (`operators/utils_operators.py:238-257`, bl_label "Recalculate
  Normals", REGISTER and UNDO) and
  `paint_system.flip_normals` (`:216-235`, bl_label "Flip Normals").
  - Poll: `context.object` is a mesh.
  - Both remember `obj.mode`, enter Edit mode, and select all. They then
    run `mesh.normals_make_consistent(inside=False)` or
    `mesh.flip_normals()` and return to the remembered mode.
  - They act on the active object only, not on every selected mesh. The
    Edit mode selection is left fully selected.
  - No other v2 UI draws them, so with the Mesh panel draw error above
    both are unreachable from the v2 UI.
- The Add Mesh buttons, Apply Transform, Set Origin and the display
  toggles are Blender built-ins. They do not need Paint System code.
- `paint_system.toggle_transform_gizmos` (dead, see above,
  `operators/utils_operators.py:355-407`):
  - In a "paint-like" mode it stores the three gizmo flags in
    `wm["ps_gizmo_*"]` and turns them all off. Otherwise it sets all
    three to the inverse of "any is on".
  - Nothing reads the stored keys.
  - Its mode set (`operators/utils_operators.py:375-384`) compares
    `obj.mode` with `context.mode` style names such as
    `'PAINT_TEXTURE'`. Of its seven names only SCULPT and
    PAINT_GREASE_PENCIL are valid `Object.mode` values (checked against
    the Blender 5.2 enum), so Texture Paint (`'TEXTURE_PAINT'`), Vertex
    Paint and Weight Paint never take the paint branch.
- `paint_system.add_preset_brushes` (`operators/utils_operators.py:60-68`,
  bl_label "Import Paint System Brushes"). The button text is "Add
  Preset Brushes" (IMPORT). It is drawn at the end of the Brush
  sub-section while no brush name starts with `PS_`. The operator
  appends the `PS_*` brushes from `operators/brushes/brushes.blend` and
  marks them as assets on 4.3+ (`operators/brushes/__init__.py:20-39`).

## v3 design

Port the three panels and their operators into `panels/quick_tools.py`
and `ops/utils_ops.py` without behaviour changes. They do not touch the
node tree.

## Acceptance

- Panels appear under "Quick Tools" with the v2 widgets; each button runs.
