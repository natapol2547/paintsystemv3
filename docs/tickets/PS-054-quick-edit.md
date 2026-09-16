# PS-054 Toggle image editor

Epic F. Size S. Milestone M3.

## v2 behaviour

`operators/quick_edit.py` sent a layer image out to Photoshop, GIMP or
Krita and read the result back:

- `project_edit` (`:31`): `paint.image_from_view`, finds the new image by
  tagging, saves `//<blend>_<object>.png` (tempdir when unsaved, NNN
  counter), stores on `layer.external_image`, launches
  `image.external_edit`.
- `project_apply` (`:107`): reloads and `paint.project_image` under an
  IMAGE_PAINT override, clears `external_image`.
- `quick_edit` (`:136`): VIEW_CAPTURE -> project_edit; IMAGE_EDIT ->
  saves the layer image to disk if needed (`image_needs_save`, `:19`),
  sets `external_image = image`, launches the editor. Errors without an
  external editor in Preferences. Dialog: `seam_bleed`, `dither`,
  `screen_grab_size`, warning to close the editor before applying.
- `reload_image` (`:302`).
- `toggle_image_editor` (`utils_operators.py:423`): split 55/45, keep the
  editor on the correct side, load the image, PAINT mode, overlays only
  for AUTO/UV layers, frame.
- Icons for known editors (`common.py::get_image_editor_icon`).

## v3 design

The external-editor half is not ported. Blender Extensions terms 3.9
forbid an extension reaching into third-party software, and every path
through `image.external_edit` does that: it reads
`context.preferences.filepaths.image_editor` and launches whatever is
there. The handoff PNGs are a second problem under 5.2, since they are
written next to the blend file or into the system temp directory rather
than into storage the extension owns. `get_image_editor_icon` and the
five editor logos it drew are already removed from v3.

What is ported:

- `toggle_image_editor`: split the area 55/45, keep the editor on the
  correct side, load the active layer's image, PAINT mode, overlays only
  for AUTO/UV layers, frame. Blender's own Image Editor only, so nothing
  in terms 3.9 or 5.2 applies.
- Nothing stores an `external_image`. PS-019 no longer ports
  `external_image` or `edit_external_mode`, PS-038 drops the "Edit
  Externally" button, PS-056 drops `external_image` from the save scan,
  and PS-070 drops it from the v2 migration.

The round trip quick edit wrapped is still wanted, without leaving
Blender: `paint.image_from_view` into a new image layer, edit it with the
Epic J tools and the PS-050 filters, `paint.project_image` back. That is
its own ticket once Epic J lands; nothing here blocks it.

## Acceptance

- Toggle image editor opens next to the 3D view with the active layer's
  image loaded, and closes on a second press.
- `image.external_edit` appears nowhere in the package.
