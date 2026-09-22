# PS-026 Texture layer redesign

Epic C. Size M. Milestone M2.

## v2 behaviour

`texture_type` (`data.py:138`): Brick, Checker, Gradient, Magic, Noise,
Voronoi, Wave, White Noise (`get_texture_identifier`,
`graph/basic_layers.py:387-398, 562-572`). The raw texture node is the
source; `color_output_name` / `alpha_output_name` enumerate its live
sockets (`data.py:1041-1074`); `update_texture_type` resets them
(`data.py:1189`). Settings panel draws the live node
(`texture_node_settings_panel`, `layers_panels.py:353-366`). Coordinates
via `create_coord_graph`. Problems: socket enums were rebuilt from the
live node and broke on type change; texture parameters were lost on
rebuild; no way to bake a procedural texture down.

### v2 UI

Paths are relative to the v2 repository root (`~/paintsystem`).
Corrected cites for the section above: the output enum getters are
`paintsystem/data.py:1054-1075`, and `update_texture_type` is at
`paintsystem/data.py:1199`.

**Host panel.** Texture settings are drawn inside the "Layer Settings"
sub-panel of `MAT_PT_Layers` (VIEW_3D, UI, category "Paint System";
`panels/layers_panels.py:494-512, 671-674`), below the common
clip/lock/blend/opacity box, the layer list and the warnings box
(`:603-669`). `draw_layer_settings` sets `layout.enabled = not
lock_layer` (`:174`). TEXTURE has no case in the type `match`; its UI
comes from three collapsible sections drawn after it.

**"Texture" section** (`panels/layers_panels.py:353-366`):
`layout.panel("texture_node_settings_panel", default_closed=True)`,
header `label(text="Texture", icon='TEXTURE')`. The body is a box
with a column (`use_property_decorate = False`,
`use_property_split = True`) containing, in order:

1. `draw_input_sockets(col, context, only_output=True)`
   (`:49-57`): a nested `layout.panel("input_sockets_panel",
   default_closed=True)` with header label "Sockets Settings:" and
   icon `float_socket`. Its body is a row with a BLANK1 label, then a
   box with a two-column `grid_flow(columns=2, align=True,
   even_columns=True, row_major=True)` holding the label "Color
   Output" over `color_output_name` (text "") and "Alpha Output" over
   `alpha_output_name` (text ""). The input grid is skipped
   (`panels/common.py:426-450`).
2. `col.prop(layer, "texture_type", text="Texture Type")` (`:362`).
3. If `source_node` exists: `use_property_split = False`, then
   `col.template_node_inputs(texture_node)` (`:363-366`). This draws the
   node's own buttons (for example the Noise/Voronoi dimensions and
   feature, the Wave type) and its unlinked inputs. Vector is hidden
   because it is linked from the coordinate graph.

Because the section starts closed, a new texture layer shows none of
its parameters until the user expands "Texture". The open state of a
`layout.panel` section is stored on the host panel under its idname,
so expanding "Texture" once keeps it open for every texture layer, and
the nested "Sockets Settings:" state is shared with the Image
section, which calls the same `draw_input_sockets`
(`panels/layers_panels.py:327, 361`).

**Output enums.** The layer enums read the live node's enabled output
sockets (`paintsystem/data.py:1054-1075`, `utils/nodes.py:133-152`).
The colour list has no None and sorts an output named "Color" to the
top. The alpha list asks to favour "Alpha", which no texture node has,
so the favour is dropped and None is placed first
(`utils/nodes.py:136-139`). Each item carries an explicit integer
value, and a new layer keeps value 0, which resolves to Color and None:
the texture renders fully opaque (pre-mix "Over Alpha" 1.0,
`paintsystem/graph/common.py:123-127`). `update_texture_type` sets
"Color" and "_NONE_" inside `try/except`; at creation no source node
exists yet, so the assignment fails silently and the value-0 defaults
apply (`paintsystem/data.py:1199-1208`). Socket icons follow the
socket type (`color_socket`, `float_socket`, `vector_socket`).

**"Transform" section** (`panels/layers_panels.py:379-460`), shared
with IMAGE layers: `layout.panel("layer_transform_settings_panel",
default_closed=True)`, header label "Transform" with icon `transform`
and `coord_type` (text "") in the header row. The UV-transfer button
in the header is IMAGE-only. The body (property split on) is a box
with per-coordinate content (`:390-428`):

- UV: `prop_search` "UV Map" over the object's UV layers (icon
  GROUP_UVS).
- DECAL: `empty_object` (text ""), "Select Empty" (icon
  OBJECT_ORIGIN), then a 0.35 split with the "Clip" toggle and the
  clip node's "Depth", disabled while Clip is off.
- PROJECT: "View Current Projection" (icon CAMERA_DATA, scale_y 2),
  "Set New Projection View" (icon FILE_REFRESH), then, if the
  projection node exists, "Scale", "Space" and a closed
  `proj_node_panel` whose header is the "Normal Falloff" checkbox and
  whose body is "Degree".
- PARALLAX: "Space" (expanded), a "UV Map" `prop_search` when the
  parallax space is UV, and "Depth".
- AUTO, OBJECT, CAMERA, WINDOW, REFLECTION, POSITION and GENERATED:
  nothing.

Then a nested
`mapping_panel` "Mapping Settings:" (icon `vector_socket`,
closed) with `template_node_inputs` on the mapping node
(`:430-437`). When the section is collapsed and
`use_panel_quick_access` is on, a one-line quick row is drawn under
the header for UV, DECAL, PROJECT and PARALLAX (`:438-460`).

**"Actions" section** follows, as for every layer type
(`panels/layers_panels.py:462-492`).

**List row.** Icon TEXTURE (`panels/common.py:568-569`); no
type-specific warnings (`paintsystem/data.py:1439-1474`).

**Add menu and dialog.** `MAT_MT_AddLayerMenu` has a "Texture"
submenu entry with icon TEXTURE between Gradient and Adjustment
(`panels/layers_panels.py:871`). `MAT_MT_AddTextureLayerMenu` ("Add
Texture", `:822-830`) lists "Brick Texture" (icon TEXTURE), then
"Checker Texture", "Gradient Texture", "Magic Texture", "Noise
Texture", "Voronoi Texture", "Wave Texture", "White Noise Texture"
(icon NONE; Gabor is commented out;
`paintsystem/data.py:138-148`, `panels/common.py:408-423`). Each
entry calls `paint_system.new_texture_layer` ("New Texture Layer",
REGISTER/UNDO, poll: active channel;
`operators/layers_operators.py:578-619`). `invoke` loads the
preferred coordinate type and opens `invoke_props_dialog`
(`:595-597`). The dialog draws (`:599-603`):

1. "Applying to all selected objects" (icon INFO) in a box when more
   than one object is selected (`operators/common.py:83-86`).
2. A box with `select_coord_type_ui(show_warning=False)`
   (`operators/common.py:174-200`): a row with the label "Coordinate
   System" (icon `transform`) and the toggle "Use AUTO UV?". With AUTO
   on, an info box reads "Will create a new UV Map: PS_UVMap" (alert,
   icon ERROR) or "Using UV Map: PS_UVMap" (icon INFO). With AUTO
   off, `coord_type` (text "") and, for UV, a `prop_search` of the UV
   map (text ""); the code sets `alert` on that row after the search
   field when the name is empty (`operators/common.py:196-200`).

The texture type is fixed by the menu entry, not shown in the dialog.
On execute the operator stores the chosen coordinates for later
layers: it always writes the group's `coord_type`/`uv_map_name`, but
writes `ps_settings.preferred_coord_type` only when the choice is
AUTO or UV (`operators/common.py:134-149`). Because `get_coord_type`
prefers `preferred_coord_type` whenever it is set, a later dialog
opens with the last AUTO or UV choice, not with an OBJECT or other
choice made since (`operators/common.py:151-172`). It then names the
layer after the enum label ("Noise Texture"), and creates
it with `texture_type`, `coord_type` and `uv_map_name`
(`operators/layers_operators.py:605-619`). It runs once for the Paint
System object and once per other selected mesh's active material
(`operators/common.py:43-78`).

**Layer menu.** "Convert to Image Layer" (icon `image`) is shown for
TEXTURE (`panels/layers_panels.py:733-739`). It calls
`paint_system.convert_to_image_layer`, whose `bl_label` is the
copy-pasted "Transfer Image Layer UV" and whose `bl_description` is
"Transfer the UV of the image layer", so the dialog title and the
tooltip both describe the wrong operator
(`operators/bake_operators.py:609-678`, `:611-612`). Its poll rejects
only IMAGE layers (`:615-618`). Its dialog shows "Baking
material: {name}" (icon MATERIAL), the other-objects and image
creation options, a "UV Map" box with a UV `prop_search`, and the
advanced bake settings (`:626-635`). Execute
disables every other layer, forces blend MIX and clip off, bakes into
a new image, restores those, then sets `coord_type` to UV, sets the
image and switches `type` to IMAGE (`:637-678`). So v2 could bake a
texture down, but only destructively. Merge Up and Merge Down are
allowed for a texture with blend MIX (`paintsystem/data.py:1604-1606`).
Each merge poll tests only the lower layer of the pair, so a texture
with another blend mode still passes when it is the upper layer
(`operators/bake_operators.py:707-723, 829-845`).

**Rebuild behaviour.** A rebuild with the same texture type reuses the
"source" node and restores its captured properties and socket
defaults, so parameters survive
(`paintsystem/graph/nodetree_builder.py:508-522, 949-980`). Changing
`texture_type` changes the node's `bl_idname`; `_remove_unused_nodes`
deletes the old node before the state is captured, so parameters reset
to Blender defaults (`:927-946`). The selected outputs are stored as
integer item values. When a node's enabled outputs change (Voronoi
feature or dimensions, for example), the stored value can point at a
different socket, which is how the live-node enums break. At
creation, `create_layer` turns `auto_update_node_tree` off and sets
the keyword properties in order, but `update_texture_type` turns it
back on and rebuilds, so the tree is first built before `coord_type`
and `uv_map_name` are applied; the final rebuild in `create_layer`
picks them up (`paintsystem/data.py:1199-1208, 2041-2045,
2066-2067`). Coordinates come from `PSNodeTreeBuilder.create_coord_graph`
(`paintsystem/graph/basic_layers.py:139-243, 571`). The standalone
`create_coord_graph` in `paintsystem/graph/common.py:146-197` is
imported but never called (dead code).

## v3 design

- `PaintSystemTextureLayerNode(CoordMixin, PaintSystemLayerNode)` with
  `texture_type`, `color_output` and `alpha_output` enums generated from a
  static table per type (Color/Fac/Distance/Position for Voronoi, etc.),
  not from a live node.
- The texture node is an artifact-owned parameter node (PS-003) so all its
  enum properties and socket values are edited directly with
  `template_node_inputs` and the node's `draw_buttons`. The Vector input
  is IR-linked from `emit_coords`.
- Changing `texture_type` recreates the parameter node and resets the
  output enums to the first valid entries.
- "Bake to Image" button in the settings panel uses the node cache
  (`paint_system.bake_cache`) and a "Convert to Image Layer" entry uses
  PS-018. Procedural layers default to `cache_enabled = False`, but the
  cache status icon in the row makes baking discoverable, which is the
  hybrid workflow this rewrite is for.
- Menu "Texture" submenu generated from the type table; row icon TEXTURE.

## Acceptance

- Each texture type compiles and renders; switching types does not leave
  orphan nodes in the artifact.
- Voronoi `Distance` selected as alpha produces a mask.
- Baking a noise texture at 512 and enabling the cache removes the
  texture node from the artifact and keeps the look.
