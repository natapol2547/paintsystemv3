# PS-027 Geometry layer

Epic C. Size S. Milestone M2.

## v2 behaviour

`geometry_type` (`data.py:171`): WORLD_NORMAL, WORLD_TRUE_NORMAL, POSITION,
BACKFACING (Geometry node), OBJECT_NORMAL, OBJECT_POSITION (Texture
Coordinate), VECTOR_TRANSFORM (takes the stack colour), AMBIENT_OCCLUSION
(`graph/basic_layers.py:574-614`). `normalize_normal` remaps -1..1 to
0..1. Settings box exposes vector transform spaces, backface, AO samples
etc. from the live nodes (`layers_panels.py:262-288`). Menu "Geometry"
submenu; row icon MESH_DATA. `new_geometry_layer` passes
`normalize_normal` when the channel is VECTOR with `normalize_input`
(`layers_operators.py:335`).

### v2 UI

Host: `draw_layer_settings` (`panels/layers_panels.py:171-492`) inside
the "Layer Settings" `layout.panel("layer_settings_panel")`, open by
default, at the bottom of `MAT_PT_Layers` (VIEW_3D, UI, category "Paint
System", no `bl_parent_id`, no `bl_options`;
`panels/layers_panels.py:494-507, 671-674`). Disabled while
`lock_layer` is on (`:174`). For this type the only content is the
per-type box followed by the "Actions" sub-panel (`:462-463`); there is
no node sub-panel and no Transform sub-panel (`:379`). PS-022 describes
the rest of the host panel, including the clip/lock/blend/opacity row
that the default UI draws above the layer list (`:604-610`).

- Per-type box (`panels/layers_panels.py:262-288`): a new `box()` in the
  default UI (`panels/common.py:464-480`) > `column()`, then by
  `geometry_type`:
  - VECTOR_TRANSFORM: `label(text="Vector Transform:",
    icon='MESH_DATA')` and `template_node_inputs(source_node)`, only
    when `source_node` is found. This branch is dead: the node is added
    as "geometry" (`graph/basic_layers.py:613`), and `source_node`
    looks only for "source" and has no GEOMETRY fallback
    (`paintsystem/data.py:957-985`). The box is always empty, so v2
    users never saw the transform type or spaces; the paragraph above
    is wrong on this point.
  - BACKFACING: a nested `box()` > `column()` with
    `label(text="Material Settings:", icon='MESH_DATA')` and
    `prop(active_material, "use_backface_culling", text="Backface
    Culling")`, icon `CHECKBOX_HLT` when on and `CHECKBOX_DEHLT` when
    off. This edits the material, not the layer.
  - WORLD_NORMAL, WORLD_TRUE_NORMAL, OBJECT_NORMAL:
    `prop(layer, "normalize_normal", text="Normalize Normal",
    icon='MESH_DATA')`, full width.
  - AMBIENT_OCCLUSION, when the "geometry" node is found: split on, no
    decorate; `samples` "Samples", `inside` "Inside", `only_local`
    "Only Local", `inputs["Color"]` "Color", `inputs["Distance"]`
    "Distance". The Normal input is linked, not drawn. The builder
    sets none of these, so a new layer has Blender's defaults: 16
    samples, Inside and Only Local off, Color white, Distance 1
    (checked by adding the node in Blender 5.2).
  - POSITION and OBJECT_POSITION: nothing; the box is empty.
- No control on the layer changes `geometry_type` after creation. The
  only other place is the Adjust Last Operation panel right after
  adding: the operator is `{'REGISTER', 'UNDO'}` with no `draw`, so
  `geometry_type`, `multiple_objects` and `multiple_materials` appear
  there, and changing one re-runs the operator
  (`operators/layers_operators.py:312-322`,
  `operators/common.py:32-42`).
- List row icon `MESH_DATA` (`panels/common.py:570-571`), the same as
  attribute layers; the same icon is used in the Shader Editor "Paint
  System" panel's layer rows (`panels/extras_panels.py:459-477`).
- Menus: in `MAT_MT_AddLayerMenu`,
  `menu("MAT_MT_AddGeometryLayerMenu", text="Geometry",
  icon='MESH_DATA')` after "Adjustment", followed by a separator
  (`panels/layers_panels.py:873-874`). `MAT_MT_AddGeometryLayerMenu`
  (bl_label "Add Geometry", `panels/layers_panels.py:833-841`) calls
  `draw_enum_operator_menu` (`panels/common.py:408-423`): one
  `operator("paint_system.new_geometry_layer", text=<enum name>)` per
  item with `geometry_type` set, icon `MESH_DATA` on the first and
  `NONE` on the rest, in enum order: "World Space Normal", "World Space
  True Normal", "World Space Position", "Object Space Normal", "Object
  Space Position", "Backfacing", "Vector Transform", "Ambient
  Occlusion" (`paintsystem/data.py:171-180`).
- `PAINTSYSTEM_OT_NewGeometry` (`operators/layers_operators.py:312-343`):
  bl_label "New Geometry Layer", `{'REGISTER', 'UNDO'}`, poll needs an
  active channel. Prop `geometry_type` (default WORLD_NORMAL), plus
  `multiple_objects` (True) and `multiple_materials` (False) from
  `MultiMaterialOperator` (`operators/common.py:32-77`), so it also adds
  the layer to the active material of every selected mesh that has a
  Paint System channel (see PS-022). No `invoke`, no dialog. The layer
  name is the enum display name (`:334`). `normalize_normal` is set
  from each target channel's `normalize_input` when that channel is
  VECTOR, else False (`:335-342`).

Behaviour implied by the UI:

- `Layer.geometry_type` has no explicit default and
  `Layer.normalize_normal` defaults to False; both rebuild the layer on
  change (`paintsystem/data.py:1215-1226`).
- Graph (`graph/basic_layers.py:574-614`): colour only, from
  `geometry.<output>` (or `normalize.Vector`) to `mix_rgb` B and
  `post_mix` Over Color. No alpha socket is given, so `pre_mix` Over
  Alpha stays at 1.0 (`graph/common.py:123-131`).
  - `normalize` is a hidden Vector Math MULTIPLY_ADD with (0.5, 0.5,
    0.5) and (0.5, 0.5, 0.5), added only for the three normal types
    when `normalize_normal` is on.
  - VECTOR_TRANSFORM: group input Color (the stack below) > Vector
    Transform Vector; the node keeps Blender's defaults (type Vector,
    World to Object, checked in Blender 5.2) because nothing sets or
    shows them.
  - AMBIENT_OCCLUSION: a second Geometry node "obj_geometry" Normal >
    AO Normal; the output is AO Color.
- AO settings are node values that the builder captures and re-applies
  on rebuild (`graph/nodetree_builder.py:47-73, 76-105, 277, 957-980,
  1040-1091`); nothing is stored on `Layer`.
- Clip is forced only for ADJUSTMENT layers
  (`paintsystem/data.py:1907-1913`); a VECTOR_TRANSFORM layer is not
  clipped unless the user turns Clip on.
- The NORMAL channel template creates a GEOMETRY layer "Normal" with
  OBJECT_NORMAL and `normalize_normal=True` only when `add_layers` is
  on and no existing Normal link was moved into the group; it then
  adds an Image layer "Image" in the same channel
  (`paintsystem/data.py:2739-2750`). The template runs from the PBR
  template when `pbr_add_normal` is on and from the NORMAL group
  template (`operators/group_operators.py:231-233, 281-296`). PS-032
  covers the channel templates.
- The Layer Menu offers "Convert to Image Layer" for this type
  (`panels/layers_panels.py:733-739`; operator in PS-018). It disables
  every other non-folder layer for the bake
  (`operators/bake_operators.py:653-656`), and a disabled layer passes
  its input straight through (`graph/common.py:137-139`). A
  VECTOR_TRANSFORM layer is therefore baked from the channel's input,
  not from the layers below it.

## v3 design

- `PaintSystemGeometryLayerNode` with `geometry_type`, `normalize_normal`,
  and explicit props for the few per-type settings: `transform_from`,
  `transform_to`, `transform_type` (Vector Transform), `ao_samples`,
  `ao_inside`, `ao_only_local`, `ao_distance`. Ambient Occlusion is a
  parameter node (PS-003) because its Normal input and Color input are
  user-editable in v2; the others are plain IR nodes.
- VECTOR_TRANSFORM behaves like an adjustment (`is_clip` forced).

## Acceptance

- World normal on a sphere baked at 64x64 gives the expected hemispheres;
  `normalize_normal` shifts the range.
