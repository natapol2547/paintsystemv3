# PS-061 Toggle paint mode and isolate channel

Epic G. Size M. Milestone M1.

## Status

Partly done (M0 slice 6): `ops/paint_ops.py` ports `toggle_paint_mode`
for meshes. It makes the object active and selected, leaves any other mode
for Object mode, and otherwise enters Texture Paint. Entering sets 3D view
shading to Rendered, or Material Preview under Cycles, then calls
`update_active_image` (PS-060). There is no grease pencil variant. The main
panel draws it as in v2: a large toggle, depressed in Texture Paint, and a
save button beside it. Channel isolate is deferred until after the demo.

## v2 behaviour

- `toggle_paint_mode` (`operators/utils_operators.py:27`): OBJECT <->
  TEXTURE_PAINT (grease pencil variant), viewport shading RENDERED
  (MATERIAL under Cycles), `update_active_image`.
- `isolate_active_channel` (`:106`): toggles `ps_mat_data.preview_channel`;
  on enable records the node/socket feeding the Material Output and the
  scene view transform, wires the group's channel output straight to the
  Material Output, sets the view transform to Standard and
  `channel.disable_output_transform`; on disable restores all (`data.py:2522`).
  Forces RENDERED shading.

### v2 UI

All paths are relative to `~/paintsystem`. Line numbers without a file
refer to `panels/common.py`.

Placement:

- `toggle_paint_mode_ui` (`:269-314`) is drawn at the top of the main
  panel body, after the legacy-data warning that returns early, when
  `use_legacy_ui` is off and a channel is active
  (`panels/main_panels.py:152-173`). This comes before the mesh check,
  so a grease pencil object whose material has an active channel gets
  it too. With `use_legacy_ui` on it moves into a box at the top of
  `MAT_PT_Layers` (`panels/layers_panels.py:531-533`), a top-level
  panel in the same View3D sidebar tab (`bl_parent_id` commented out,
  `panels/layers_panels.py:494-500`). Nothing else calls it.
- That panel's poll also passes for a grease pencil object without an
  active channel (`panels/layers_panels.py:507`). With `use_legacy_ui`
  on, the call then raises on the missing material or group (`:283-284`)
  and the panel draw fails.

Paint mode row (`:275-289`):

- A `column(align=True)` holds a `row(align=True)` with `scale_x` and
  `scale_y` 1.7, set directly, so `use_compact_design` does not shrink
  it.
- An inner `row(align=True)` holds `paint_system.toggle_paint_mode`
  "Toggle Paint Mode" (custom `paintbrush`), depressed when
  `context.mode == 'PAINT_TEXTURE'`. For meshes this inner row is
  disabled while the active channel uses its baked image (`:293-294`).
  Grease pencil paint mode is not PAINT_TEXTURE, so the button is never
  depressed there.
- `paint_system.isolate_active_channel` with `text=""`, in the outer
  row, so a baked channel does not disable it. It is depressed while
  `ps_mat_data.preview_channel` is on. The icon is the active channel's
  socket icon while previewing (`get_icon_from_channel`, `:30-36`:
  `color_socket`, `vector_socket` or `float_socket`), otherwise the
  custom `channel` icon.
- `wm.save_mainfile` with `text=""` and the custom `save` icon.
- Below the row, for meshes: the Normal tip box and the "Bake and
  Export" menu (see PS-031). The tip reads "The button above will" /
  "show object normal" and points at the isolate button (`:295-307`).
  It shows only for a NORMAL or PBR group whose active channel is named
  "Normal", and does not check whether the isolate button is drawn.

When the isolate button shows (`:283-287`). A group node whose tree is
the active group's must be found in the part of the material connected
to the active Material Output (`find_node`, `utils/nodes.py:79-114`,
searching both directions). One of these must also hold: the setup is
not basic, the group has more than one channel, or preview is on.
`is_basic_setup` (`:255-266`) collects the nodes upstream of the active
output (`utils/nodes.py:9-34`). It is true for at most one node, and
otherwise only when a Group node, a Mix Shader and a Transparent BSDF
are all present; any group node counts. Per template (PS-041), with one
channel: BASIC and PAINT_OVER hide the button, since PAINT_OVER builds
the same three nodes. PBR and NORMAL show it. NONE hides it, because
its group node is not linked to the output. While previewing, the
group node feeds the output directly, and the `preview_channel` term
keeps the button visible.

Operator `paint_system.isolate_active_channel`
(`operators/utils_operators.py:106-124`): bl_label "Isolate Channel",
description "Isolate the active channel", bl_options REGISTER and UNDO,
no properties and no `invoke`. Poll: `ps_object`, `active_material` and
`active_channel` all exist. `execute` calls
`active_channel.isolate_channel`. It then sets the 3D view shading to
RENDERED only when the shading is neither RENDERED nor MATERIAL, so
Material Preview is kept. This runs when turning isolate off as well as
on.

`Channel.isolate_channel` (`paintsystem/data.py:2522-2553`) toggles
`MaterialData.preview_channel` (`paintsystem/data.py:2942-2946`,
default False, saved with the file):

- On: it stores the node name and the socket name of the first link
  into the active Material Output's Surface input, and the scene view
  transform as a string. They go into `original_node_name`,
  `original_socket_name` and `original_view_transform`
  (`paintsystem/data.py:2947-2958`). It links the group node output
  named after the channel (the main output, not "<name> Alpha")
  straight to Surface, with no Emission node. It sets the view
  transform to "Standard" and the channel's `disable_output_transform`
  to True. There is no guard: an unlinked Surface raises on
  `links[0]`, after the flag was already set (`paintsystem/data.py:2530,
  2532`), so preview is left on with stale restore data. When no group
  node is found, the flag and the view transform still change without
  any rewiring.
- Off: it looks up the stored node by name and links its stored output
  back to Surface. A renamed or deleted node leaves the channel link in
  place. It restores the stored view transform, overwriting any change
  made while isolated, and sets `disable_output_transform` to False on
  the channel that is active now.

What `disable_output_transform` changes (`paintsystem/data.py:2450-2456`,
default True "for legacy reasons", its update rebuilds the channel):
`create_channel` passes False, so new channels start with the output
transform on (`paintsystem/data.py:2670, 2680`). The only code that
acts on it is `Channel.update_node_tree`
(`paintsystem/data.py:1872-1885`); `Channel.bake` only saves and
restores it. For a
VECTOR channel with `use_space_transform_output`, it skips the output
`vector_transform` from the painting space (`vector_space`, or
`bake_vector_space` when baked) to `output_vector_space`. The Normal
channel template turns the output transform on, with the defaults
OBJECT to WORLD (`paintsystem/data.py:2423-2444, 2741`). Isolating it
therefore shows object space normals, as the tip says. For COLOR and
FLOAT channels the flag only triggers a rebuild.

Channel switches (`Group.update_channel`,
`paintsystem/data.py:2647-2659`, the update of `Group.active_index`).
While previewing, `isolate_channel` runs twice on the newly active
channel. The first call restores the original link and view transform
and clears the new channel's flag. The second records them again,
wires the new channel's output and sets its flag. The previous
channel's `disable_output_transform` stays True, so a Normal channel
left while isolated keeps outputting object space. After that a baked
channel forces Object mode, and `update_active_image` runs.
`MaterialData.active_index` has no update (`paintsystem/data.py:2936`),
so switching groups does not move the preview.

Other users of the preview state:

- `Channel.bake` turns an active preview off before baking and back on
  afterwards. It also holds `disable_output_transform` True during any
  bake that is not a tangent normal bake
  (`paintsystem/data.py:2147-2163, 2262-2272`). The error path never
  turns the preview back on, so a failed bake leaves isolate off. It
  restores the channel settings in one `try` that stops at the first
  unset variable: after a failed bake that is not a tangent normal bake
  it stops at `orig_tangent_uv_map` or earlier, and
  `disable_output_transform` stays True
  (`paintsystem/data.py:2278-2290`). Both calls go through
  `isolate_channel`, which acts on the active channel, not the channel
  being baked.
- No load handler reads `preview_channel`. A file saved while isolated
  reopens isolated, with the button depressed.
- The BASIC template already sets the view transform to Standard when
  the group is created (PS-041, `operators/group_operators.py:168-169`).

`paint_system.toggle_paint_mode` (`operators/utils_operators.py:27-57`):
bl_label "Toggle Paint Mode", description "Toggle between texture paint
and object mode", bl_options REGISTER and UNDO. Poll: `ps_object.type`
is MESH or GREASEPENCIL; with no `ps_object` it raises instead of
returning False. `execute` makes the object active and
selected. Any mode other than Object goes to Object mode and returns
there, without touching shading or the active image. From Object mode
it enters TEXTURE_PAINT (PAINT_GREASE_PENCIL for grease pencil). If the
object is then in that mode, it sets the shading to RENDERED, or to
MATERIAL when the engine is exactly CYCLES. It calls
`update_active_image` either way.

## v3 design

- `paint_system.toggle_paint_mode` ported as is.
- Isolate is a compile option rather than a material rewrite where
  possible: `tree.preview_channel: StringProperty` (SKIP_SAVE). When set,
  `interface_outputs` adds a `Preview` shader output and the Group Output
  emitter connects an Emission of the previewed channel to it; the
  operator links that output to the Material Output surface socket and
  records the previous link by socket name on the material
  (`ps_preview_restore`). Disable removes the link, restores the
  original and clears the property. View transform handling as v2.
- Because `preview_channel` is SKIP_SAVE, a file saved while isolated
  reloads un-isolated with the restore data still on the material;
  `on_load_post` runs the restore if `ps_preview_restore` is present.

## Acceptance

- Isolate the Roughness channel: viewport shows the grey mask; disable
  restores the Principled link and the view transform.
- Saving while isolated and reloading shows the normal material.
