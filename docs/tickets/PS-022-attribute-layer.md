# PS-022 Attribute layer

Epic C. Size S. Milestone M2.

## v2 behaviour

`ShaderNodeAttribute` source (`graph/basic_layers.py:442-450`);
`attribute_type` (GEOMETRY/OBJECT/INSTANCER/VIEW_LAYER) and
`attribute_name` were operator properties written straight onto the node
(`layers_operators.py:194-228`), so they were lost on rebuild and not
copied with the layer. `color_output_name` / `alpha_output_name` choose
Color/Vector/Fac/Alpha outputs. Settings panel `attribute_node_settings_panel`
(`layers_panels.py:367-377`) drew the live node. Menu entry "Attribute
Color" (icon MESH_DATA).

### v2 UI

Host, shared by all layer types: `draw_layer_settings`
(`panels/layers_panels.py:171-492`) draws into the "Layer Settings"
`layout.panel("layer_settings_panel")`, open by default, at the bottom
of `MAT_PT_Layers` (`panels/layers_panels.py:494-507, 671-674`): space
VIEW_3D, region UI, category "Paint System", no `bl_parent_id`, no
`bl_options`; poll needs a paint object and either an active channel
or a Grease Pencil object, and hides the panel when the active group's
tree is used by more than one material
(`panels/layers_panels.py:502-507`, `panels/common.py:197-204`). It is
registered after the main "Paint System" panel and sits below it as a
separate top-level panel (`panels/__init__.py:4-12`). For a mesh the
panel draws, in order: a box with the clip/lock/blend/opacity row
(default UI only; `panels/layers_panels.py:604-610`,
`panels/common.py:347-377`); the "Paint System not connected" / "to
material output!" alert when the group node is missing (`:623-631`);
the layer list and its sidebar (`:644-653`); an alert box with the
active layer's warnings, each wrapped at 32 characters with icon ERROR
on its first line and BLANK1 after (`:659-669`); then Layer Settings
(`:671-674`). When the channel uses its baked image, the row, list and
Layer Settings are replaced by a bake box (`:604, 633-641`). The whole
Layer Settings area is disabled while `lock_layer` is on
(`panels/layers_panels.py:174`). With the `use_legacy_ui` preference
(default off, `panels/preferences_panels.py:80-84`) the
clip/lock/blend/opacity row moves into Layer Settings and the type
content shares its box (`panels/layers_panels.py:175-177`,
`panels/common.py:323-346, 464-480`).

- No per-type box: ATTRIBUTE has no branch in the type `match`
  (`panels/layers_panels.py:180-290`).
- Sub-panel `layout.panel("attribute_node_settings_panel",
  default_closed=True)`, header `label(text="Attribute",
  icon='MESH_DATA')` (`panels/layers_panels.py:367-369`). For this type
  the only other sub-panel is "Actions" (icon KEYTYPE_KEYFRAME_VEC,
  starts closed), below it (`:462-463`); there is no Transform
  sub-panel (`:379`).
- Body, a `box()` > `column()` (`panels/layers_panels.py:370-377`):
  1. `draw_socket_grid(col, layer, include_inputs=False)`
     (`panels/common.py:426-441`): a `box()` holding
     `grid_flow(columns=2, align=True, even_columns=True,
     row_major=True)`; left column `label(text="Color Output")` over
     `prop(layer, "color_output_name", text="")`, right column
     `label(text="Alpha Output")` over
     `prop(layer, "alpha_output_name", text="")`.
  2. If the source node exists: `label(text="Attribute Settings:",
     icon='MESH_DATA')` and `template_node_inputs(source_node)`. The
     Attribute node has no input sockets, so this draws only the node's
     own Type and Name buttons. It is the only place where the user
     chooses the attribute.
- Output enums (`paintsystem/data.py:1054-1099`,
  `utils/nodes.py:133-152`): the items are the node's enabled outputs,
  each with the custom icon `color_socket`, `vector_socket` or
  `float_socket` by socket type (`custom_icons.py:40-46`). Colour lists
  Color, Vector, Fac, Alpha, with no None; alpha lists Alpha, Color,
  Vector, Fac, then "None" (`_NONE_`, icon BLANK1). The items are the
  live socket names, so on Blender 5.0 and later the third one reads
  "Factor" (checked by adding the node in 4.5.13 and 5.0.1). Both
  default to the first item. Changing either rebuilds the layer tree
  (`update=update_node_tree`).
- List row icon `MESH_DATA` (`panels/common.py:559-560`). The rest of
  the row is generic (`panels/layers_panels.py:58-108`): folder indent
  icons, the custom `clipping` icon when Clip is on, the type icon, the
  name (no emboss), then right-aligned lock, KEYTYPE_KEYFRAME_VEC (has
  actions), LINKED, the `show_layer_warnings` button (custom `error`
  icon), optional opacity text and the HIDE_OFF/HIDE_ON toggle. The
  same type icon is used in the Shader Editor "Paint System" panel's
  layer rows (`panels/extras_panels.py:459-477`).
- Menu: in `MAT_MT_AddLayerMenu` (`bl_options =
  {'SEARCH_ON_KEY_PRESS'}`, `panels/layers_panels.py:843-846`), after
  the separator that follows the type submenus and after "Fake Light",
  `operator("paint_system.new_attribute_layer", text="Attribute Color",
  icon='MESH_DATA')` (`panels/layers_panels.py:874-879`). The menu opens
  from the first button of the list sidebar (`wm.call_menu`, custom icon
  `layer_add`, `panels/common.py:491`).
- Operator `PAINTSYSTEM_OT_NewAttribute`
  (`operators/layers_operators.py:194-226`): bl_label "New Attribute
  Layer", `{'REGISTER', 'UNDO'}`, poll needs an active channel. Props:
  `attribute_name` (string), `attribute_type` (`ATTRIBUTE_TYPE_ENUM`,
  default GEOMETRY), `layer_name` (default "Attribute"), plus
  `multiple_objects` (default True) and `multiple_materials` (default
  False) from `MultiMaterialOperator` (`operators/common.py:32-77`). No
  `invoke` and no dialog: it runs at once and adds the layer to the
  active material of the paint object and of every other selected mesh
  except "PS Camera Plane" (`operators/common.py:46-72`). A mesh whose
  active material has no Paint System group and channel is skipped; its
  `CANCELLED` result is not counted as an error
  (`operators/layers_operators.py:41-43, 222-223`).
  `process_material` passes only the name and type to `create_layer`
  (`operators/layers_operators.py:221-226`); `attribute_name` and
  `attribute_type` are never read, so they only appear, without effect,
  in the Adjust Last Operation panel.

Behaviour implied by the UI:

- Correction to the paragraph above: at this commit the operator does
  not write the attribute onto the node, and the node settings are not
  lost. A rebuild reuses the existing node, matched by its label
  (`graph/nodetree_builder.py:348-360, 508-511`); on a recompile it also
  captures the node's BOOLEAN/INT/FLOAT/STRING/ENUM properties (Type
  and Name among them) and socket defaults, then re-applies them
  (`graph/nodetree_builder.py:47-105, 277, 957-980, 1040-1091`). Paste
  copies the whole layer node tree (`paintsystem/data.py:1492-1495,
  1520-1522`; `operators/layers_operators.py:902-903`). What is true is
  that nothing on `Layer` stores the attribute; it exists only on the
  live node.
- A new layer's Attribute node keeps Blender's defaults (type Geometry,
  empty name) (`graph/basic_layers.py:449`).
- Graph: colour from `source.<color_output_name>` to `mix_rgb` B and
  `post_mix` Over Color; alpha from `source.<alpha_output_name>` to
  `pre_mix` Over Alpha. With alpha `_NONE_` nothing is linked and
  `pre_mix` Over Alpha stays at 1.0 (`graph/basic_layers.py:413-421,
  442-450`; `graph/common.py:123-131`). On the first build the node does
  not exist yet, so the names fall back to "Color" and "Alpha".
- No warning for a missing attribute. `get_layer_warnings` only warns
  about a non-MIX blend or an adjustment layer with no layer below, and
  about a zero channel input alpha (`paintsystem/data.py:1439-1474`).
- `modifies_color_data` is True for ATTRIBUTE
  (`paintsystem/data.py:1604-1606`): Merge Down is unavailable when the
  layer below is an attribute layer, and Merge Up when the active layer
  is one (`operators/bake_operators.py:708-723, 830-845`). The Layer
  Menu (sidebar DOWNARROW_HLT button, `panels/common.py:495-496`)
  offers "Convert to Image Layer" for this type
  (`panels/layers_panels.py:733-739`); that operator is covered by
  PS-018.

## v3 design

- `PaintSystemAttributeLayerNode` with `attribute_type`, `attribute_name`
  (with a search over the active object's colour attributes and UV maps
  when type is GEOMETRY), `color_output` (COLOR/VECTOR/FAC),
  `alpha_output` (ALPHA/FAC/NONE).
- `emit_source` emits one Attribute node.
- Warning when `attribute_name` is not found on the active object.
- Operator `paint_system.new_attribute_layer`.

## Acceptance

- Colour attribute on the cube shows through; renaming the attribute
  property patches in place; copy/paste keeps the name.
