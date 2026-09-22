# PS-024 Gradient layer and empty gizmo

Epic C. Size L. Milestone M2.

## v2 behaviour

`gradient_type` (`data.py:119`): GRADIENT_MAP, LINEAR, RADIAL, DISTANCE,
FAKE_LIGHT (`graph/basic_layers.py:477-517`). A Colour Ramp is the source,
fed by a Map Range:

- LINEAR: Texture Coordinate (object = empty) Object -> Separate XYZ Z.
- RADIAL: Vector Math LENGTH, Map Range inverted.
- DISTANCE: Camera Data View Distance.
- GRADIENT_MAP: the stack colour below (acts like an adjustment).
- FAKE_LIGHT: Combine XYZ -> Vector Rotate (Euler from `object_rotation`
  driven by the empty) dotted with the normal; `new_gradient_layer`
  forces blend MULTIPLY and the name "Fake Light"
  (`layers_operators.py:306-308`).

Empty gizmos are created by `ensure_empty_object` (`data.py:1476,
889-939`): SINGLE_ARROW for linear, SPHERE for radial, offset arrow for
fake light, drivers for rotation, unused empties unlinked from the PS
collection. UI: empty select / fix missing (`layers_panels.py:215-230`),
`gradient_node_settings_panel` with nested Map Range
(`layers_panels.py:334-352`). Menu: "Gradient" submenu plus top-level
"Fake Light" (icon LIGHT); row icon COLOR or LIGHT.

### v2 UI

Host: `draw_layer_settings` (`panels/layers_panels.py:171-492`) inside
the "Layer Settings" `layout.panel("layer_settings_panel")`, open by
default, at the bottom of `MAT_PT_Layers` (VIEW_3D, UI, category "Paint
System", no `bl_parent_id`, no `bl_options`;
`panels/layers_panels.py:494-507, 671-674`). Disabled while
`lock_layer` is on (`:174`). For a gradient layer the order is: the
per-type box, the "Gradient" sub-panel, the "Actions" sub-panel
(`:462-463`); there is no Transform sub-panel (`:379`). Fake Light's
MULTIPLY blend shows in the clip/lock/blend/opacity row, which the
default UI draws above the layer list (`:604-610`); PS-022 describes
the rest of the host panel.

- Per-type box, only for LINEAR, RADIAL and FAKE_LIGHT
  (`panels/layers_panels.py:215-230`). A new `box()` in the default UI
  (`panels/common.py:464-480`), then `column()` with
  `use_property_split = True`, `use_property_decorate = False`.
  - When `empty_object` is set and its name is in
    `context.view_layer.objects`: `column(align=True)` holding
    `operator("paint_system.select_empty", text="Select Gradient
    Empty", icon='OBJECT_ORIGIN')` (text "Select Light Empty" for
    FAKE_LIGHT) and, below it, `prop(layer, "empty_object", text="")`.
  - Otherwise: `box()` with `alert = True` > `column(align=True)`:
    `label(text="Gradient Empty not found", icon='ERROR')` and
    `operator("paint_system.fix_missing_gradient_empty", text="Fix
    Missing Gradient Empty")` with no icon. This also shows when the
    empty exists but is not linked into the view layer.
  - GRADIENT_MAP and DISTANCE get no box.
- Sub-panel, drawn for every gradient type when the `source` and
  `map_range` nodes exist (`panels/layers_panels.py:334-352`):
  `layout.panel("gradient_node_settings_panel", default_closed=True)`,
  header `label(text="Gradient", icon='COLOR')` ("Light Gradient" for
  FAKE_LIGHT). Body: `box()`, `template_node_inputs(source)` (the
  Colour Ramp node's own UI), then a nested
  `box.panel("map_range_node_settings_panel", default_closed=True)`
  with header `label(text="Map Range:", icon='SHADERFX')`. Nested body,
  split on, no decorate:
  `prop(map_range, "interpolation_type", text="Interpolation")`;
  `inputs[5]` "Steps" only when the interpolation is STEPPED;
  `inputs[1]` (From Min) "Start Distance"; `inputs[2]` (From Max) "End
  Distance". The labels are the same for every type, including
  GRADIENT_MAP and FAKE_LIGHT. To Min, To Max (`inputs[3]`,
  `inputs[4]`) and the Clamp option are not drawn.
- No control on the layer changes `gradient_type` after creation. The
  only other place is the Adjust Last Operation panel right after
  adding: the operator is `{'REGISTER', 'UNDO'}` with no `draw`, so
  `layer_name`, `gradient_type`, `multiple_objects` and
  `multiple_materials` appear there, and changing one re-runs the
  operator (`operators/layers_operators.py:279-300`,
  `operators/common.py:32-42`).
- List row icon `LIGHT` for FAKE_LIGHT, else `COLOR`
  (`panels/common.py:561-565`); the same icon is used in the Shader
  Editor "Paint System" panel's layer rows
  (`panels/extras_panels.py:459-477`).
- Menus: in `MAT_MT_AddLayerMenu`,
  `menu("MAT_MT_AddGradientLayerMenu", text="Gradient", icon='COLOR')`
  between "Image" and "Texture", and after the separator that follows
  "Geometry", `operator("paint_system.new_gradient_layer", text="Fake
  Light", icon='LIGHT')` with `gradient_type = 'FAKE_LIGHT'`
  (`panels/layers_panels.py:870, 874-877`).
  `MAT_MT_AddGradientLayerMenu` (bl_label "Add Gradient",
  `panels/layers_panels.py:799-808`) calls `draw_enum_operator_menu`
  (`panels/common.py:408-423`) with FAKE_LIGHT skipped: one
  `operator(..., text=<enum name>)` per item, icon `COLOR` on the first
  and `NONE` on the rest: "Gradient Map", "Linear Gradient", "Radial
  Gradient", "Distance Gradient" (`paintsystem/data.py:119-125`).
- `PAINTSYSTEM_OT_NewGradient` (`operators/layers_operators.py:279-309`):
  bl_label "New Gradient Layer", `{'REGISTER', 'UNDO'}`, poll needs an
  active channel. Props `layer_name` (default "Gradient", never read)
  and `gradient_type` (default LINEAR), plus `multiple_objects` (True)
  and `multiple_materials` (False) from `MultiMaterialOperator`
  (`operators/common.py:32-77`), so it also adds the layer to the active
  material of every selected mesh that has a Paint System channel (see
  PS-022). Each of those layers gets its own empty, parented to its own
  mesh, because the override makes that mesh the active object
  (`operators/common.py:67`, `paintsystem/data.py:1476-1490`). No
  `invoke`, no dialog. The name is
  `gradient_type.title()`, which gives "Gradient_Map", "Linear",
  "Radial", "Distance"; FAKE_LIGHT gets "Fake Light" and then
  `blend_mode = "MULTIPLY"` (`:303-308`).
- `PAINTSYSTEM_OT_SelectEmpty` (`operators/layers_operators.py:361-377`):
  bl_label "Select Empty", `{'REGISTER', 'UNDO'}`, no poll. Re-links the
  empty into "Paint System Collection" when it is missing from the
  view layer, switches to OBJECT mode (so it leaves Texture Paint),
  deselects all, makes the empty active and selected.
- `PAINTSYSTEM_OT_FixMissingGradientEmpty`
  (`operators/layers_operators.py:346-358`): bl_label "Fix Missing
  Gradient Empty", `{'REGISTER', 'UNDO'}`, no poll. Calls
  `update_node_tree` on every GRADIENT layer of the active channel, then
  on the active layer. The rebuild does the repair (next list).

Behaviour implied by the UI:

- Empty lifecycle in `Layer.update_node_tree`
  (`paintsystem/data.py:891-904`): for LINEAR/RADIAL/FAKE_LIGHT, if
  `empty_object` is None, `ensure_empty_object` runs and the new empty
  gets its display: LINEAR `SINGLE_ARROW`; RADIAL `SPHERE`; FAKE_LIGHT
  `location += (0, 0, 2)`, `rotation_euler = (3π/4, π/4, 0)`,
  `SINGLE_ARROW`. If the empty is set but not in the view layer, it is
  re-linked. GRADIENT_MAP and DISTANCE never create an empty.
- `ensure_empty_object` (`paintsystem/data.py:1476-1490`): name
  `"{layer name} ({uid[:8]}) Empty"`; reuses an object of that name if
  one exists, else `bpy.data.objects.new(name, None)`. Either way it is
  parented to the paint object (no parent inverse, so it starts at the
  object's origin) and linked into "Paint System Collection", created
  under the scene collection when missing
  (`paintsystem/data.py:333-340, 805-808`). The empty is not renamed
  when the layer is renamed (the name is only set at `:1479, 1503`).
- `empty_object` is `PointerProperty(type=Object)` with no poll and
  `update=update_node_tree` (`paintsystem/data.py:1187-1191`), so any
  object can be picked. Clearing the field rebuilds at once, and the
  rebuild fills it again: `ensure_empty_object` takes the object that
  still has the layer's empty name, or creates a new one. For
  FAKE_LIGHT that path also adds another +2 Z to the reused empty and
  resets its rotation (`paintsystem/data.py:893-902, 1480-1489`).
  The "Gradient Empty not found" box therefore only appears when the
  empty was removed or unlinked without a rebuild. `Layer.gradient_type`
  defaults to GRADIENT_MAP (`:1192-1198`); the operator default is
  LINEAR.
- Copy duplicates the empty under the new uid (`paintsystem/data.py:
  1501-1504`), so copy/paste never shares one. Deleting the layer calls
  `bpy.data.objects.remove` on whatever object the field holds, unless
  the layer data is linked elsewhere and is transferred instead
  (`paintsystem/data.py:1562-1569`). Because the field has no poll, two
  layers can point at the same object, or at a user's own scene object;
  deleting either layer deletes that object.
- Graph (`graph/basic_layers.py:477-517`): `source` is a ValToRGB with
  Blender's default black-to-white ramp; `source.Color` feeds `mix_rgb`
  B and `post_mix` Over Color, `source.Alpha` feeds `pre_mix` Over
  Alpha, so ramp stop alpha controls coverage. `map_range.Result` goes
  to `source.Fac` for every type.
  - LINEAR: Texture Coordinate (`object = empty`) Object > Separate XYZ
    Z > Map Range Value; Map Range keeps Blender's defaults (0 to 1).
  - RADIAL: same coordinate > Vector Math LENGTH > Map Range, with From
    Min 1 and From Max 0 on first build.
  - DISTANCE: Camera Data View Distance > Map Range.
  - GRADIENT_MAP: group input Color (the stack below) > Map Range.
  - FAKE_LIGHT: `combine_xyz` (0, 0, Z) > Vector Rotate (EULER_XYZ)
    Vector; `object_rotation` Combine XYZ > Vector Rotate Rotation;
    rotated vector DOT Geometry Normal > Map Range. Z is -1 when the
    picked object is an EMPTY and +1 for any other type, forced on
    every rebuild (`:504`). The line reads `empty_object.type`
    directly and would raise on None, but the only caller,
    `update_node_tree`, always runs `ensure_empty_object` first for
    FAKE_LIGHT, so v2 never builds without an object
    (`paintsystem/data.py:892-894, 916`).
- FAKE_LIGHT drivers (`paintsystem/data.py:919-938`): after each
  compile, `object_rotation` inputs X/Y/Z get an AVERAGE driver with
  one TRANSFORMS variable "rotation_euler" (ROT_X/ROT_Y/ROT_Z) on the
  empty; old drivers are removed first. The target's transform space
  is left at Blender's default, WORLD_SPACE, so the paint object's own
  rotation is included.
- User edits to the ramp and Map Range survive rebuilds: the builder
  captures ColorRamp elements and input values before recompiling
  (`graph/nodetree_builder.py:47-73, 277, 957-980, 1040-1091`), except
  the forced FAKE_LIGHT Z.
- Clip is forced only for ADJUSTMENT layers
  (`paintsystem/data.py:1907-1913`); a GRADIENT_MAP layer is not
  clipped unless the user turns Clip on.
- `modifies_color_data` is True for GRADIENT_MAP and for any layer whose
  blend is not MIX, so it includes Fake Light (MULTIPLY)
  (`paintsystem/data.py:1604-1606`). Merge Down is unavailable when the
  layer below is one, Merge Up when the active layer is one
  (`operators/bake_operators.py:708-723, 830-845`).
- The Layer Menu offers "Convert to Image Layer" for every gradient
  type (`panels/layers_panels.py:733-739`; operator in PS-018). It
  disables every other non-folder layer for the bake
  (`operators/bake_operators.py:653-656`), and a disabled layer passes
  its input straight through (`graph/common.py:137-139`). A
  GRADIENT_MAP layer is therefore baked from the channel's input
  colour, not from the layers below it.

## v3 design

- `PaintSystemGradientLayerNode` with `gradient_type`, `empty_object`.
  GRADIENT_MAP sets `is_clip` like an adjustment.
- Colour Ramp and Map Range are artifact-owned parameter nodes (PS-003);
  the settings panel draws `template_color_ramp` and the Map Range inputs
  from `ctx.param_node`.
- FAKE_LIGHT rotation uses IR drivers (PS-004) on the Combine XYZ inputs.
- Empty management goes through PS-064 (`ensure_empty(node, kind)`), called
  from the add operator and from the `gradient_type` update, never from
  `emit`. `emit` only references `empty_object`; a missing empty compiles
  with object None and raises a warning (PS-019).
- Operators: `new_gradient_layer` (names from the type title, Fake Light
  special case), `select_empty`, `fix_missing_gradient_empty` (recreates
  empties for all gradient nodes in the tree).

## Acceptance

- Linear gradient across the cube matches v2 direction and falloff when
  the empty is placed identically.
- Fake light updates in the viewport when the empty rotates, without a
  recompile.
- Deleting the empty and pressing Fix Missing restores it.
