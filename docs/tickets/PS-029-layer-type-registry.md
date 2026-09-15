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
