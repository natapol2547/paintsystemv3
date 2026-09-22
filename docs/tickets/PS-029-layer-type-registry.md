# PS-029 Layer type registry: icons, Add Layer menu, operators

Epic C. Size M. Milestone M1.

## Status

Partly done (M0 slice 4) for Folder, Solid Color and Image:

- `nodes/layers/registry.py`: `layer_types()`, `layer_type(ps_type)` and
  `layer_type_items()`. Layer node classes declare `ps_type` (v2 ids
  `FOLDER`, `SOLID_COLOR`, `IMAGE`), `ps_label`, `ps_description`,
  `ps_icon`, `ps_menu_section` and `ps_add_options`.
- `ps_icon` is a tuple of names tried in order by `common.icon_kwargs`,
  since Blender renames icons (`SEQUENCE_COLOR_03` became
  `STRIP_COLOR_03`) and an unknown name makes the layout call raise.
- `create(tree, target=None, **options)` takes no context; the operator
  syncs the canvas afterwards. Options are the add operator's properties
  named in `ps_add_options` (image: `resolution`), which the operator asks
  for in a dialog.
- One `paint_system.add_layer` operator takes `layer_type` from the
  registry. `GROUP` is no longer offered there; the group layer node stays
  in the node editor's Layers category, which is derived from the
  registry.
- `draw_row_icon(layout)` per type replaces `draw_layer_icon`: folder
  expand toggle, solid colour swatch, image icon. Image previews are not
  shown yet.
- `PAINTSYSTEM_MT_add_layer`: Folder, separator, Solid Color, Image. The
  search entry, the image sub-menu and the other types are open, as is the
  menu fixture test.

## v2 behaviour

`LAYER_TYPE_ENUM` (`data.py:99`), `draw_layer_icon`
(`panels/common.py:531-573`), `MAT_MT_AddLayerMenu` and sub-menus
(`layers_panels.py:788-884`) generated from the type enums via
`draw_enum_operator_menu` (`panels/common.py:408-423`), one `new_*_layer`
operator per type. Menu layout: optional "Search...", Folder, separator,
Solid Color, Image submenu (New/Import/Use Existing), Gradient,
Texture, Adjustment, Geometry submenus, separator, Fake Light, Attribute
Color, Random Color, Custom Layer.

### v2 UI

Paths are relative to `~/paintsystem` (commit 8991037). Line numbers
without a file are in `panels/layers_panels.py`.

Where the menu is reached:

- The only caller of `MAT_MT_AddLayerMenu` is the sidebar button, a
  `wm.call_menu` with the custom `layer_add` icon
  (`panels/common.py:491`).
- `bl_label` is "Add Layer". `bl_options = {'SEARCH_ON_KEY_PRESS'}`,
  so typing while the menu is open starts a search in it (`:843-846`).
- All entries are drawn in one `layout.column()` (`:850`).

Entry detail, in the order listed above (`:848-884`):

- "Search..." (VIEWZOOM) runs `WM_OT_search_single_menu` with
  `menu_idname = "MAT_MT_AddLayerMenu"`, followed by a separator.
  - It is drawn only when the layout's operator context on entry is
    `EXEC_REGION_WIN` (`:852-859`). A `wm.call_menu` popup starts in
    that context, so the entry does show from the sidebar button. This
    is the same pattern as Blender's `NODE_MT_add` (Blender 5.2
    `scripts/startup/bl_ui/space_node.py:310-313`).
  - The context is then forced to `INVOKE_REGION_WIN`, so the
    operators below open their dialogs (`:861`).
- "Folder" (custom `folder`): `paint_system.new_folder_layer`.
- "Solid Color": `new_solid_color_layer`, icon
  `icon_parser('STRIP_COLOR_03', 'SEQUENCE_COLOR_03')`.
- "Image" (custom `image`): sub-menu `MAT_MT_AddImageLayerMenu`.
- "Gradient" (COLOR), "Texture" (TEXTURE), "Adjustment" (SHADERFX) and
  "Geometry" (MESH_DATA): sub-menus.
- "Fake Light" (LIGHT): `new_gradient_layer` with
  `gradient_type = 'FAKE_LIGHT'`.
- "Attribute Color" (MESH_DATA): `new_attribute_layer`.
- "Random Color" (SEQ_HISTOGRAM): `new_random_color_layer`.
- "Custom Layer" (NODETREE): `new_custom_node_group_layer`.

Sub-menus:

- `draw_enum_operator_menu` gives the given icon only to enum index 0.
  All other items get no icon (`panels/common.py:419-423`).
- "Add Image" (`:788-796`), all `new_image_layer`:
  - "New Image Layer" (custom `image`), `image_add_type` NEW;
  - "Import Image Layer", IMPORT, no icon;
  - "Use Existing Image Layer", EXISTING, no icon.
- "Add Gradient" (`:799-808`, enum `paintsystem/data.py:119-125`):
  Gradient Map (COLOR), Linear Gradient, Radial Gradient, Distance
  Gradient. FAKE_LIGHT is skipped here and offered at the top level.
- "Add Texture" (`:822-830`, enum `paintsystem/data.py:138-148`):
  Brick Texture (TEXTURE), Checker Texture, Gradient Texture, Magic
  Texture, Noise Texture, Voronoi Texture, Wave Texture, White Noise
  Texture. Gabor is commented out.
- "Add Adjustment" (`:811-819`, enum `paintsystem/data.py:127-136`):
  Brightness and Contrast (SHADERFX), Gamma, Hue Saturation Value,
  Invert, RGB Curves, RGB to BW, Map Range.
- "Add Geometry" (`:833-841`, enum `paintsystem/data.py:171-180`):
  World Space Normal (MESH_DATA), World Space True Normal, World Space
  Position, Object Space Normal, Object Space Position, Backfacing,
  Vector Transform, Ambient Occlusion.

Add operators (`operators/layers_operators.py`):

- Common traits:
  - all are `MultiMaterialOperator`s with `{'REGISTER', 'UNDO'}`;
  - the poll requires an active channel;
  - each inserts at the cursor and makes the new layer active (PS-034
    "v2 UI").
- "New Folder" (`:144-166`): name "Folder", no dialog.
- "New Solid Color Layer" (`:169-191`): name "Solid Color", no dialog.
  The RGB node starts white
  (`paintsystem/graph/basic_layers.py:437-440`).
- "New Image Layer" (`:45-141`): properties `image_name`,
  `image_add_type` (NEW / IMPORT / EXISTING), `filepath` and
  `filter_glob` (jpg, jpeg, png, tif, tiff, bmp).
- NEW opens `invoke_props_dialog` (`:118-141`):
  - The name comes from `get_next_unique_name("Image")` over the
    channel's layer names: "Image", then "Image 1", "Image 2"
    (`utils/__init__.py:3-38`).
  - When more than one object is selected, a box reads "Applying to
    all selected objects" (INFO) (`operators/common.py:83-86`).
  - Then the "Layer Name" field.
  - Then a box "Image Resolution" (IMAGE_DATA) with 1024 / 2048 / 4096
    / 8192 / Custom expanded (default 2048), and Width / Height for
    Custom.
  - "Use UDIM Tiles" appears only when the coord type is UV and that
    UV map spans tiles other than 1001. "Use Float" follows
    (`operators/common.py:246-266`).
  - Last, the coordinate box.
- IMPORT opens the file browser with no dialog. The layer is named
  after the loaded image (`:93-98`, `:124-126`).
- EXISTING opens a dialog with the multi-object box, an "Image"
  `prop_search` over `bpy.data.images` and the coordinate box
  (`:131-141`). The layer is named after that image (`:99-107`,
  `:127-128`).
- NEW creates the image in the operator (`create_image`,
  `operators/common.py:268-278`) with the dialog's size and "Use
  Float". It gets UDIM tiles only for UV when "Use UDIM Tiles" is on
  and the UV map needs them. `create_ps_image` starts it transparent,
  `(0, 0, 0, 0)` (`paintsystem/data.py:541-552`).
- `create_layer` also has a fallback that creates a 2048 image when an
  IMAGE layer arrives without one, adding UDIM tiles whenever the UV
  map needs them (`paintsystem/data.py:2049-2056`). The add operators
  always pass an image, so they do not reach it.
- The image node uses Closest interpolation
  (`paintsystem/graph/basic_layers.py:429`).
- Coordinate box (`select_coord_type_ui`,
  `operators/common.py:174-200`):
  - a row with "Coordinate System" (custom `transform`) and a "Use
    AUTO UV?" toggle;
  - toggle on: a box with "Using UV Map: {name}" (INFO), or an alert
    "Will create a new UV Map: {name}" (ERROR);
  - toggle off: the `coord_type` dropdown. For UV it adds a UV-map
    `prop_search`, which turns alert when empty. For any other type it
    adds an alert box "Painting in 3D may not work" (ERROR) / "Open
    Blender Image Editor to paint" (BLANK1).
  - Defaults come from the `preferred_coord_type` setting or the
    group's last coord type. Running the operator always writes the
    coord type and UV map back to the group. It updates
    `preferred_coord_type` only for AUTO or UV
    (`operators/common.py:134-172`).
- "New Texture Layer" (`:578-619`): a dialog with the multi-object
  box and the coordinate box, without the painting warning. The layer
  is named after the enum label.
- "New Gradient Layer" (`:279-309`): no dialog.
  - The name is `gradient_type.title()`, giving "Linear", "Radial",
    "Distance" and "Gradient_Map", or "Fake Light".
  - Fake Light sets the blend mode to MULTIPLY.
  - LINEAR, RADIAL and FAKE_LIGHT create an empty on the first build
    (`paintsystem/data.py:891-904`): SINGLE_ARROW, SPHERE, or
    SINGLE_ARROW raised 2 units and tilted.
- "New Adjustment Layer" (`:229-251`): no dialog, named after the enum
  label.
- "New Geometry Layer" (`:312-343`): no dialog, named after the enum
  label. On VECTOR channels `normalize_normal` copies the channel's
  `normalize_input`.
- "New Attribute Layer" (`:194-226`): no dialog, name "Attribute". Its
  `attribute_name` and `attribute_type` properties are never read.
- "New Random Color Layer" (`:380-402`): no dialog, name "Random
  Color".
- "New Custom Node Group Layer" (`:405-575`) opens a dialog:
  - "Select node tree:" (NODETREE), or "No supported node trees found"
    (ERROR) when none qualify.
  - A tree dropdown scaled 1.5. It lists shader node groups whose
    names do not start with ".PS", "Paint System" or "PS ".
  - An alert "Node has unsupported sockets (Shader)" when an interface
    socket is not Color, Float or Vector.
  - A "Socket Connection" (NODETREE) box with side-by-side "Input" and
    "Output" boxes, each with Color and Alpha dropdowns. The input
    dropdowns and the Alpha output include None.
  - `invoke` and a tree change run `auto_select_sockets`
    (`:441-473`, `:537-539`). It pre-selects inputs named Color and
    Alpha and an output named Alpha, and sets each to None when no
    such socket exists. The Color output has no None item and is not
    auto-selected; that code is commented out.
  - Execute fails with "Node tree must have at least one output
    socket" only when both outputs are None or empty (`:520-522`).
    Because the Color output cannot be None, that means a tree with
    no outputs. The layer is named after the tree.
  - Unlike the image and texture dialogs, this one has no
    multi-object box, though the operator still runs on every
    selected object.
- `new_shader_layer` "New Shader Layer" (`:254-276`) is registered
  (`:1195`) but dead: no menu entry, and "SHADER" is not in
  `LAYER_TYPE_ENUM`.

Per-type registration facts:

- `LAYER_TYPE_ENUM` (`paintsystem/data.py:99-111`), in order: FOLDER,
  IMAGE, SOLID_COLOR, ATTRIBUTE, ADJUSTMENT, NODE_GROUP, GRADIENT,
  RANDOM, TEXTURE, GEOMETRY, BLANK.
- BLANK is the type of linked entries
  (`operators/layers_operators.py:900`). Node-tree builds skip it
  (`paintsystem/data.py:848-849`).
- Menu labels differ from the enum labels: NODE_GROUP is "Custom
  Layer", RANDOM is "Random Color", ATTRIBUTE is "Attribute Color".
- Graph dispatch is `create_layer_graph`
  (`paintsystem/graph/basic_layers.py:632-657`). Graph versions
  (`paintsystem/graph/basic_layers.py:10-19`): IMAGE 6, RANDOM 5, all
  others 4.
- Row icons (`draw_layer_icon`, `panels/common.py:531-573`), additions
  to the summary above:
  - IMAGE uses the custom `image` icon when there is no image or no
    painted preview. Otherwise it uses `image.preview.icon_id`.
  - `is_image_painted` reads the preview pixels
    (`panels/common.py:388-406`). A dirty image calls
    `asset_generate_preview()` during draw.
  - FOLDER is an `is_expanded` toggle, `folder_open` / `folder`,
    emboss off.
  - SOLID_COLOR is an inline, editable colour field on the RGB node
    output (IMAGE_RGB_ALPHA).
  - ADJUSTMENT SHADERFX; NODE_GROUP NODETREE; ATTRIBUTE and GEOMETRY
    MESH_DATA; GRADIENT LIGHT for Fake Light, else COLOR; RANDOM
    SEQ_HISTOGRAM; TEXTURE TEXTURE; any other type BLANK1.
  - SHADER SHADING_RENDERED is unreachable.
- Masks: `MAT_MT_AddMaskMenu` "Add Mask" (`:887-896`) has Value Mask
  (VALUE), Image Mask (IMAGE), Attribute Mask (ATTRIBUTE) and Texture
  Mask (TEXTURE). It is registered but never drawn.
- The mask operators only append to `layer.masks`
  (`operators/layers_operators.py:1121-1186`). No code in
  `paintsystem/graph/` reads masks.

## v3 design

- `nodes/layers/registry.py`: each layer node class declares
  `ps_type` (v2 enum id, used by migration), `ps_label`, `ps_icon`
  (custom icon name or Blender icon), `ps_menu_section`, and optionally
  `ps_variants` (the sub-menu enum with icons). `registry.layer_types()`
  returns classes in menu order.
- `draw_layer_icon(layout, node)` implemented from the registry with the
  two dynamic cases (image preview, solid colour swatch, folder toggle).
- `MAT_MT_AddLayerMenu` and sub-menus built from the registry; the shared
  `paint_system.new_layer` operator takes `layer_type` and `variant` and
  dispatches to the class's `create(context, tree, **kwargs)` classmethod.
  Types with a dialog (image, custom group, texture coords) keep their
  dedicated operators and are listed by the registry.
- Node editor categories are derived from the same registry so the node
  editor "Add" menu and the sidebar menu never drift.

## Acceptance

- Menu renders identical entries and order to v2 (screenshot comparison
  by the user; automated: the item list from `registry.menu_items()`
  matches a fixture).
- Every registered type has an icon and a working create path.
