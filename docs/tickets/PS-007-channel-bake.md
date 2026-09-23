# PS-007 Channel-level bake ("Use Baked")

Epic A. Size M. Milestone M3.

## v2 behaviour

- `bake_channel` / `bake_all_channels` (`operators/bake_operators.py:182,
  293`) bake the channel output into `channel.bake_image` on
  `channel.bake_uv_map`, colour space from `channel.color_space`.
- `use_bake_image` short-circuits the stack to a Texture Image
  (`data.py:1888-1896`); the Layers header shows "Use Baked" (`layers_panels.py:513-525`).
- `bake_channel as_layer=True` inserts the result as a new image layer.
- `delete_bake_image`, `select_all_baked_objects`.
- Menus `MAT_MT_PaintSystemMergeAndExport` (`layers_panels.py:145-168`).

### v2 UI

Entry points:

- "Bake and Export" menu (`MAT_MT_PaintSystemMergeAndExport`, whose
  own bl_label is "Baked and Export", `panels/layers_panels.py:146`;
  contents in PS-031). `toggle_paint_mode_ui` draws it for meshes in an
  aligned row with scale_x 1.5 and scale_y 1.3, text "Bake and
  Export", no icon (`panels/common.py:309-314`). The main
  panel calls that function in the modern UI
  (`panels/main_panels.py:172-173`), the Layers panel in the legacy UI
  (`panels/layers_panels.py:531-533`). Its "Delete Active Channel"
  entry runs Delete Baked Image; it does not delete the channel
  (`panels/layers_panels.py:166`).
- Legacy UI only: the Channels box starts with menu
  `MAT_MT_PaintSystemChannelsMergeAndExport` "Bake and Export"
  (TEXTURE_DATA) (`panels/channels_panels.py:117-118`; bl_label
  "Baked and Export", `:16`). In an aligned
  column: label "Bake", "Bake All Channels" (`channels` icon), "Bake
  Active Channel (<channel>)" (channel socket icon), separator, label
  "Export", "Export All Images" (EXPORT), "Export Active Channel
  (<channel>)" (EXPORT) (`panels/channels_panels.py:15-34`).

Baked-state UI:

- Layers panel header preset: `use_bake_image` "Use Baked"
  (TEXTURE_DATA), for meshes whose active channel has a bake image
  (`panels/layers_panels.py:523-525`).
- With Use Baked on, the Layers panel skips the layer settings box and
  the layer list. After the "Paint System not connected" warning
  (drawn only when the material lacks the group node) it draws one box:
  `image_node_settings(col, image_node, active_channel, "bake_image",
  simple_ui=True, default_closed=True)` on the channel tree's Image
  Texture, then "Apply Image Filters" (IMAGE_DATA, `wm.call_menu`
  `MAT_MT_ImageFilterMenu`) and "Delete" (TRASH,
  `paint_system.delete_bake_image`)
  (`panels/layers_panels.py:604-612, 633-641`). The simple image
  settings put `bake_image` (text ""), `paint_system.export_image`
  (FILE_TICK) and `MAT_MT_ImageMenu` (COLLAPSEMENU) in the header of a
  closed sub-panel. The body lists the UDIM tiles label (UV),
  interpolation, projection, extension, source, "Color Space" and
  "Alpha" (`panels/common.py:207-252`). `MAT_MT_ImageMenu` polls for
  an active layer with an image, not for the bake image
  (`panels/layers_panels.py:711-715`).
- Toggle Paint Mode is disabled while Use Baked is on
  (`panels/common.py:294`). Turning `use_bake_image` on forces Object
  mode (`paintsystem/data.py:2466-2470`).
- Channels panel: a baked channel's row shows "Baked" (TEXTURE_DATA)
  in place of a value. When a bake image exists, Channel Settings
  starts with an aligned row: "Use Baked Image" (TEXTURE_DATA) and an
  icon-only TRASH delete button. When baked it then shows only
  `bake_vector_space` (text "") for VECTOR channels
  (`panels/channels_panels.py:42-50, 159-167`; PS-032).

Bake Channel and Bake All Channels dialogs:

- `paint_system.bake_channel` (bl_label "Bake Channel", poll: active
  channel) and `paint_system.bake_all_channels` ("Bake All Channels",
  poll: active group), both REGISTER/UNDO. Invoke opens
  `invoke_props_dialog(self)` at the default width, titled with the
  bl_label, button OK (`operators/bake_operators.py:81-89, 182-210,
  293-308`). `as_layer` (SKIP_SAVE, default False, `:189-194`) is set
  only by the "Bake Active Channel as Layer" entry
  (`panels/layers_panels.py:159`).
- Invoke refills `scene.ps_scene_data.temp_materials` (SKIP_SAVE
  collection of `TempMaterial`: `material`, `enabled` default False).
  It adds each material with Paint System groups found on the selected
  and active PS objects, once, and enables only the active material
  (`operators/bake_operators.py:18-31`;
  `paintsystem/data.py:2778-2787, 2905-2910`;
  `paintsystem/context.py:88-93`). `bake_multiple_objects` (default
  True) would, when off, limit this to the active object, but it is
  never drawn (`operators/bake_operators.py:33-38`).
- UV preset: `get_coord_type` starts from the object's first UV map.
  When the `preferred_coord_type` preference is AUTO or UV, that alone
  decides AUTO or not. While it is UNDETECTED (the default), the
  group's `coord_type` decides, and the group's `uv_map_name`, if set,
  replaces the first UV map. When the result is AUTO, invoke sets
  `PS_UVMap` (`operators/common.py:127-172`,
  `operators/bake_operators.py:85-87`,
  `panels/preferences_panels.py:49-58`). An object in Edit mode is
  switched to Object mode, and `use_udim_tiles` is preset on when the
  UV map uses tiles other than 1001 (`operators/common.py:280-286`).
- Bake Channel presets `as_tangent_normal` to
  `channel.bake_vector_space == 'TANGENT'` (`:207-210`).
  `bake_vector_space` defaults to OBJECT
  (`paintsystem/data.py:2487-2497`). Bake All Channels has no such
  preset, so the toggle starts off.
- The name "<group>_<channel>" set in invoke (`:88`) is never used by
  either operator: the name field is hidden and execute renames.

Dialog body, in order (`:201-205, 304-308`):

1. `multi_object_ui` (`:126-141`): a box with the label "Materials to
   Bake (N)" (MATERIAL), N being the enabled count. An aligned column
   has one row per material: `prop(temp_material, "enabled",
   toggle=1)`, text "<name> (Current Material)" for the active
   material and "<name>" otherwise, icon CHECKBOX_HLT when enabled and
   CHECKBOX_DEHLT when not.
2. Inside that box, `other_objects_ui` (`:100-124`). It draws nothing
   unless a material is enabled and the scene has more than one
   (mesh, enabled material) match; a mesh counts once for each enabled
   material it holds. Then a nested box:
   - Unselected holders exist: an alert column with "Detected other
     objects with the material." ("materials" when more than one is
     enabled) (ERROR) and "They will not be baked" (BLANK1). Then,
     not alert, "Select All Objects" (SELECT_EXTEND,
     `paint_system.select_all_baked_objects`) and a closed layout
     panel "See Detected Objects" holding a
     `grid_flow(columns=3, align=True, row_major=True,
     even_columns=True)` with one label per unselected object
     (OBJECT_DATA).
   - Otherwise the label "All objects with the material are selected"
     (CHECKMARK).
3. `image_create_ui(show_name=False, show_float=True)`
   (`operators/common.py:246-266`): a box labelled "Image Resolution"
   (IMAGE_DATA) with `image_resolution` expanded in one aligned row:
   1024, 2048, 4096, 8192, Custom (default 2048, not SKIP_SAVE, so
   Blender remembers the last choice). Custom adds an aligned column
   with Width and Height (default 1024, min 1, PIXEL). "Use UDIM
   Tiles" shows when the operator's `coord_type` is UV and the UV map
   uses tiles other than 1001; with an AUTO preset `coord_type` is
   AUTO, so it is hidden. "Use Float" (default off, SKIP_SAVE) closes
   the box (`operators/common.py:91-103, 210-244`).
4. A box with the label "UV Map" (UV) and
   `prop_search(self, "uv_map_name", ps_object.data, "uv_layers",
   text="")` (`operators/bake_operators.py:150-152`).
5. Active channel VECTOR only: a box with the "As Tangent Normal"
   checkbox. When on, a nested box holds an aligned column with the
   note wrapped at 48 characters: "Deform Modifiers such as Armature
   will be" (INFO) and "disabled" (BLANK1) (`:153-163`).
6. `advanced_bake_settings_ui` (`:91-98`): a layout panel, closed by
   default, header "Advanced Settings" (IMAGE_DATA). Body: `use_gpu`
   "Use GPU" (default True), then `split(factor=0.4, align=True)` with
   `margin` "Margin" (default 8, 0-100) and `margin_type` with no
   label (Adjacent Faces, the default, or Extend). All three are
   SKIP_SAVE (`:40-64`).

Select All Baked Objects (`:166-179`): bl_label "Select All Baked
Objects". Invoke runs execute, which selects every scene mesh holding
an enabled material. Nothing is deselected.

Bake Channel execute (`:212-290`):

- No enabled material: ERROR "No materials to bake.", CANCELLED.
- WAIT cursor. Each enabled material is baked with its own active
  channel (`parse_material`). The image name becomes
  "<material>_Baked" (`:225`). The dialog's UV map, resolution and As
  Tangent Normal apply to every enabled material.
- Default path (`:254-281`): the channel's existing `bake_image` is
  reused, rescaled with `Image.scale` when the size differs. Use Float
  and UDIM apply only when a new image is created. Colour space is
  Non-Color when `channel.color_space` is NONCOLOR, else sRGB. It sets
  `bake_uv_map`, turns `use_bake_image` off, and bakes with the dialog's
  tangent, GPU and margin settings. Deform modifiers are disabled when
  As Tangent Normal is on for a VECTOR channel. Afterwards
  `bake_vector_space` becomes TANGENT, or the channel's `vector_space`,
  and `use_bake_image` is turned on.
- As layer (`:230-253`): always a new image, colour space sRGB whatever
  the channel, `force_alpha=True`. Then `create_layer(layer_name=
  "<material>_Baked", layer_type="IMAGE", insert_at="START",
  image=..., coord_type='UV', uv_map_name=...)`. "START" matches no
  case in `get_insertion_data`, so the layer goes to the root with
  order 1, the top of the stack, and becomes active
  (`paintsystem/nested_list_manager.py:48-84`,
  `paintsystem/data.py:2022-2069`). `use_bake_image`, `bake_image` and
  `bake_uv_map` are left alone, so a tangent bake takes the previous
  `bake_uv_map` as tangent UV (`paintsystem/data.py:2156-2160`). With
  Use Baked on, the group output being baked is
  the old baked image (`paintsystem/data.py:1891-1895`; inferred from
  code).
- Then `mode_set(mode="OBJECT")` (`:283`), DEFAULT cursor, INFO "Baked
  N materials in X seconds", X rounded to two decimals (`:289`).

Bake All Channels execute (`:310-346`): the same material check,
message and Object mode switch. The resolution is resolved once. For
every channel of each enabled material's active group, the bake image
is reused (rescaled) or created as "<group>_<channel>_Baked", with
colour space from `color_space`. Then `use_bake_image` off,
`bake_uv_map` set, `channel.bake(context, mat, image, uv_map_name)`,
`use_bake_image` on. The call passes no other argument, so the As
Tangent Normal box and Advanced Settings drawn in the dialog have no
effect. `bake_vector_space` is not updated, yet with an output
transform the baked graph converts from it
(`paintsystem/data.py:1872-1885`). A VECTOR channel whose
`vector_space` differs from the stored value is therefore read back
in the wrong space (inferred from code).

`Channel.bake` (`paintsystem/data.py:2107-2290`) and `ps_bake`
(`:1670-1722`):

- If the active object is not a mesh but the PS object is, the PS
  object is selected and made active (`:2139-2143`).
- An active channel preview (isolate) is switched off for the bake and
  back on afterwards (`:2147-2150, 2271-2272`).
- `force_alpha` (default True) sets `use_alpha` for the bake. As
  tangent normal sets `tangent_uv_map` to `bake_uv_map` and
  `output_vector_space` to TANGENT. Otherwise `disable_output_transform`
  is set, so the result stays in the channel's own vector space
  (`:2152-2163`).
- Deform modifiers: `show_render` off for Armature, Cast, Curve,
  Displace, Hook, Laplacian Deform, Lattice, Mesh Deform, Shrinkwrap,
  Simple Deform, Smooth, Corrective Smooth, Laplacian Smooth, Surface
  Deform, Warp and Wave on the PS objects, restored after
  (`:2169-2179, 2274-2277`).
- Source: with `use_group_tree` (the default, used by both bake
  operators) the material's Paint System group node outputs
  "<channel>" and "<channel> Alpha", taken from the first group that
  has a channel of that name. Otherwise, or when the material has no
  such group node, a temporary group node running the channel tree
  (`:2198-2218`). If `use_alpha` was off and the group's colour input
  is linked, the group node's "<channel> Alpha" input is set to 1.0
  and never restored (`:2206-2208`).
- Two EMIT bakes through the material's Surface input: colour into the
  image, alpha into a Non-Color copy. The copy's red channel is written
  into the image's alpha per UDIM tile, the image is packed or saved
  (`paintsystem/image.py:108-119`), the copy removed and the Surface
  link restored (`:2221-2260`).
- `ps_bake` bakes the selected and active PS objects that hold the
  material, adding UDIM tiles first. It switches to Cycles, 1 sample,
  no denoising or adaptive sampling, view transform Standard, device
  GPU or CPU. A temporary Image Texture is made active, the bake runs
  with `use_clear=True` and retries on CPU if it raises. Engine,
  device, samples and view transform are restored (`:1670-1722`), but
  only on success: if the CPU retry also raises, the temporary node
  stays and the scene keeps Cycles, 1 sample and Standard
  (`:1707-1720`).
- Errors are only logged (`:2278-2290`). The temporary nodes stay, the
  Surface link is not restored, the channel preview is not turned back
  on, and the operator still reports success. For a non-tangent bake
  the restore block stops at the unset `orig_tangent_uv_map`, so
  `disable_output_transform` stays True (`:2280-2286`).

Node graph with a bake image (`paintsystem/data.py:1872-1895`):

- Whenever `bake_image` is set, the channel tree gains a UV Map node
  (`uv_map = bake_uv_map`) feeding an Image Texture (`bake_image`,
  interpolation Closest).
- With `use_bake_image`: Image Color goes to the channel output (Group
  Output Color, or for a VECTOR channel with output transform the
  input of the vector transform, which converts from
  `bake_vector_space` with `bake_uv_map` as tangent UV). Image Alpha
  goes straight to Group Output Alpha, bypassing the end alpha clamp.
  No layer is built. Without it the two nodes stay unlinked.

Delete Baked Image (`operators/bake_operators.py:509-539`):

- bl_label "Delete Baked Image", REGISTER/UNDO, no poll. Invoke opens
  `invoke_props_dialog` at the default width. The body is one label,
  "Click OK to delete the baked image.", with no icon.
- Execute: without an active channel it cancels silently. Without a
  bake image: ERROR "No baked image found.". Otherwise it removes the
  datablock with `bpy.data.images.remove` and sets `bake_image` None,
  `use_bake_image` False and `bake_uv_map` "" (the property default
  is "UVMap", `paintsystem/data.py:2477-2481`).

## v3 design

Channel bake is the node cache applied at the Group Output:

- `PaintSystemChannel` gains `bake_image`, `bake_uv_map`, `use_bake_image`,
  `bake_hash`, `bake_stale` (SKIP_SAVE), `bake_vector_space`.
- `build_ir` accepts `bake_target=('channel', name)`; the Group Output
  emitter, when `use_bake_image` and `bake_hash` matches the channel's
  upstream subtree hash, emits an Image Texture instead of walking the
  stack.
- `compiler/bake.py::bake_channel(context, tree, channel, objects, ...)`
  reuses `bake_node_cache` with the channel's output refs. Multi-object:
  bake each selected object that uses a material linked to the tree into
  the same image (same as v2 `select_all_baked_objects` semantics).
- "Bake as Layer" = bake into a fresh managed image, insert an image layer
  at the top, leave `use_bake_image` off.
- A vector channel bakes its layer values, as a layer cache does
  (PS-006): a float Non-Color image in the channel's Paint In space, with
  normals as normal map colours. It reads back through the same
  conversion, and Bake as Layer can put it on top of the stack as it is.
  Whether to offer v2's `bake_vector_space`, a bake in another space, is
  decided here.

## Acceptance

- Test: bake a 2-layer colour channel at 64x64, enable Use Baked, artifact
  has no blend groups for that channel, pixel matches the live render.
- Editing a layer marks `bake_stale` and the header shows the warning icon
  (same as node cache).
- Menu entries present and functional: Bake Active Channel, Bake Active
  Channel as Layer, Bake All Channels, Export Active Channel, Delete
  Active Channel, Export All Channels.
