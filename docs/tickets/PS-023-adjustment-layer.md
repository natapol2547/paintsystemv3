# PS-023 Adjustment layer

Epic C. Size M. Milestone M2.

## v2 behaviour

`adjustment_type` (`data.py:127`): BRIGHTCONTRAST, GAMMA, HUE_SAT, INVERT,
CURVE_RGB, RGBTOBW, MAP_RANGE, mapped by `get_adjustment_identifier`
(`graph/basic_layers.py:400-410`). The source node takes `group_input.Color`
(the stack below), a Value node of 1 feeds `Fac` where present, RGB to BW
outputs `Val`, Map Range uses `Value`/`Result` (`:452-475`). Always clipped
(`data.py:1912`). Settings drawn with `template_node_inputs` on the live
node (`layers_panels.py:197-203`). Menu "Adjustment" submenu generated
from `ADJUSTMENT_TYPE_ENUM`; layer named after the enum label
(`layers_operators.py:229`).

### v2 UI

Paths are relative to the v2 repository root (`~/paintsystem`).
Corrected cites for the section above: the forced `Clip` input is
`paintsystem/data.py:1910`, and the layer name is taken from the enum
label at `operators/layers_operators.py:249`.

**Host panel.** Everything below is drawn by `MAT_PT_Layers`
(VIEW_3D, UI, category "Paint System", bl_label "Layers", header icon
`layers`, no `bl_parent_id`; `panels/layers_panels.py:494-512`). It is
the second top-level panel of the "Paint System" tab, after
`MAT_PT_PaintSystemMainPanel` (`panels/__init__.py:4-12`). Poll fails
when the group's node tree is multi-user and otherwise needs a Paint
System object and either an active channel or a Grease Pencil object
(`:502-507`). For a mesh with the modern UI and no baked image in use,
the panel draws, in order (`:603-674`):

1. An outer box. If the active layer has a node tree, it first holds a
   nested box with the common layer controls (`layer_settings_ui`,
   `panels/common.py:316-377`): `is_clip` toggle (icon
   SELECT_INTERSECT), `lock_layer` (VIEW_LOCKED/LOCKED), `blend_mode`
   (text ""), and the pre-mix opacity slider. The layout is wide when
   the region width minus 70 px times UI scale exceeds 170 px times UI
   scale: then it is a split at factor 0.7 and the slider has no text.
   Otherwise it is a column and the slider reads "Opacity" in a row
   scaled 0.8 in height. Both use scale 1.3. The clip toggle is shown
   for adjustment layers too (see "List row" below).
2. Inside the same outer box, the "Paint System not connected" / "to
   material output!" warning, with an "Open Shader Editor" button when
   no node editor is open (`:623-631`).
3. Still inside the outer box, the `MAT_PT_UL_LayerList` list (rows
   `min(max(6, n), 7)`, scale_y 1.5 unless compact design is on;
   `panels/common.py:14-20`) and the sidebar (`:644-653`).
4. An alert box with the layer warnings, wrapped at 32 characters,
   icon ERROR on the first line of each warning and BLANK1 on its
   continuation lines (`:657-669`).
5. `layout.panel("layer_settings_panel")` "Layer Settings", open by
   default, which calls `draw_layer_settings` (`:671-674`).

**Adjustment settings box** (`panels/layers_panels.py:197-203`).
`draw_layer_settings` first sets `layout.enabled = not lock_layer`
(`:174`). For ADJUSTMENT it takes `get_settings_box` (a new
`layout.box()` in the modern UI, the box that already holds
`layer_settings_ui` in the legacy UI; `panels/common.py:464-480`),
opens `box.column()`, and, only if `source_node` exists, draws
`col.label(text="Adjustment Settings:", icon='SHADERFX')` followed by
`col.template_node_inputs(adjustment_node)`. No property split. The
template draws the node's own buttons and only its unlinked inputs, so
per type the box shows (sockets checked in Blender 4.2):

- Brightness and Contrast: Bright, Contrast.
- Gamma: Gamma.
- Hue Saturation Value: Hue, Saturation, Value (Fac is linked to the
  constant Value node, so hidden).
- Invert: nothing but the label (Fac and Color are both linked).
- RGB Curves: the curve widget only.
- RGB to BW: nothing but the label.
- Map Range: data type, interpolation and Clamp buttons, then From Min,
  From Max, To Min, To Max (Value is linked).

`adjustment_type` is never drawn in any panel: apart from v1
migration (`operators/versioning_operators.py:123-124`), the add menu
is the only place that sets it, so the type cannot be changed from the
panels after creation (`panels/layers_panels.py:811-819`; no other
`adjustment_type` reference exists under `panels/`).

After the adjustment box, the only other section for this type is the
shared "Actions" sub-panel (closed by default, icon
KEYTYPE_KEYFRAME_VEC; `panels/layers_panels.py:462-492`).

**List row.** Icon SHADERFX (`panels/common.py:553-554`). The
clipping icon is drawn only when `is_clip` is True
(`panels/layers_panels.py:85-88`). A new adjustment has `is_clip`
False (`paintsystem/data.py:1266-1274`) while its layer group node
receives `Clip = True` (`paintsystem/data.py:1910`), so by default the
row shows no clip marker. The user can still turn `is_clip` on from
the toggle above. The row then shows the marker, and the channel graph
inserts the `.PS Alpha Over` clip wrapper and routes the adjustment
into it (`paintsystem/data.py:1915-1930`). The rest of the row is the
same as for other types, right-aligned: lock icon when locked,
keyframe icon when actions exist, LINKED icon for linked layers, the
warning button when warnings exist, the optional opacity text and the
eye toggle (`panels/layers_panels.py:93-107`).
The row's warning button opens `paint_system.show_layer_warnings`,
an `invoke_props_dialog` of width 260
(`operators/layers_operators.py:1014-1046`,
`panels/layers_panels.py:101-103`). The adjustment warning is
"No layer below. Adjustment effects may not work.", raised when no
sibling layer is below and either the channel socket on the material
group node is unconnected or the layer is not the last one
(`paintsystem/data.py:1459-1468`).

**Add menu and operator.** The sidebar "layer_add" button calls
`MAT_MT_AddLayerMenu` through `wm.call_menu` (`panels/common.py:491`).
That menu ("Add Layer", `SEARCH_ON_KEY_PRESS`) has an "Adjustment"
submenu entry with icon SHADERFX after Texture
(`panels/layers_panels.py:843-884`, `:872`). The submenu
`MAT_MT_AddAdjustmentLayerMenu` ("Add Adjustment", `:811-819`) lists
one operator button per enum item in enum order: "Brightness and
Contrast" with icon SHADERFX, then "Gamma", "Hue Saturation Value",
"Invert", "RGB Curves", "RGB to BW", "Map Range" with icon NONE
(`panels/common.py:408-423`, `paintsystem/data.py:127-136`).
`paint_system.new_adjustment_layer` ("New Adjustment Layer",
REGISTER/UNDO, poll: active channel; its own property is
`adjustment_type`, plus the inherited `multiple_objects` and
`multiple_materials`) has no `invoke`, so it runs without a dialog
(`operators/layers_operators.py:229-251`). As a `MultiMaterialOperator`
it runs on the Paint System object plus the active material of every
selected mesh except "PS Camera Plane" (`operators/common.py:32-78`).
The base class has a "Completed with N error(s)" warning, but it never
fires here: `process_material` returns `{'CANCELLED'}` or
`{'FINISHED'}`, and both are truthy sets, so the error count stays 0
(`operators/common.py:62-75`, `operators/layers_operators.py:245-251`).
The layer is inserted at the cursor and made active
(`paintsystem/data.py:2022-2069`). The operator has no `draw` method,
so Blender's generic redo panel would list Multiple Objects, Multiple
Materials and Adjustment Type after a run; this was not checked in a
running UI.

**Layer menu.** "Convert to Image Layer" is hidden for ADJUSTMENT
(`panels/layers_panels.py:733-739`), but only by the menu: the
operator's own poll rejects IMAGE layers only
(`operators/bake_operators.py:609-678`). Merge Up and Merge Down stay
available, because `modifies_color_data` is False for an adjustment
with blend MIX (`paintsystem/data.py:1604-1606`). Both polls test the
lower layer of the pair: Merge Down tests the layer below the active
one, and Merge Up tests the active layer
(`operators/bake_operators.py:707-723, 829-845`).

**Graph details not covered above.** The builder registers only a
colour output, so the pre-mix "Over Alpha" keeps its default 1.0
(`paintsystem/graph/basic_layers.py:462`,
`paintsystem/graph/common.py:123-127`). The Fac driver is a
`ShaderNodeValue` with identifier "value" and output 1
(`paintsystem/graph/basic_layers.py:470-473`). The alpha-over clip
wrapper is only inserted when the user turns `is_clip` on
(`paintsystem/data.py:1915-1930`). Same-type rebuilds reuse the
"source" node and restore its captured values, so curves and sliders
survive (`paintsystem/graph/nodetree_builder.py:508-522, 949-980`).
The enum identifiers equal Blender's `Node.type` strings; v1 migration
relies on this by reading `node.type` from the legacy node labelled
"Adjustment" and copying its node state
(`operators/versioning_operators.py:40-46, 123-124, 179-181`).

## v3 design

- `PaintSystemAdjustmentLayerNode` with `adjustment_type`; `is_clip` forced
  True and hidden; `modifies_color_data = True` (blocks merge, PS-018).
- `emit_source`: the adjustment node is an artifact-owned parameter node
  (PS-003) so curves and sliders are edited on it directly. Its colour
  input is IR-linked to the incoming stack colour (the clip base source
  via PS-013), which the builder must link even on a user-owned node:
  links are always IR-managed, only values and properties are not.
- Settings panel: `ctx.param_node(node, 'adjust')` drawn with
  `template_node_inputs`, falling back to "Compile to edit" before the
  first compile.
- Changing `adjustment_type` changes `bl_idname`, which the builder
  handles by recreating the node (identifier stays); previous parameter
  state is lost, as in v2.
- Alpha: v2 output alpha was the stack alpha (clip). Same here through
  the clip path.

## Acceptance

- Invert over a red solid renders cyan; opacity 0.5 renders the midpoint.
- Curve edits survive recompile and undo.
- Node cache invalidates after a curve edit on the next compile.
