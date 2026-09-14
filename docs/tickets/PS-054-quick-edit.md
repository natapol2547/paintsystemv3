# PS-054 Quick edit (external editor) and toggle image editor

Epic F. Size M. Milestone M3.

## v2 behaviour

`operators/quick_edit.py`:

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
- Icons for known editors (`common.py::get_image_editor_icon`, already in
  v3).

## v3 design

Port as is onto `node.external_image` / `node.edit_external_mode`
(PS-019). The only compile interaction: after `project_apply` or reload
the image pixels changed but the graph did not, so no compile is needed;
call `image.update()` only.

## Acceptance

- IMAGE_EDIT round trip with a fake editor script that inverts the PNG:
  reload shows the inverted image on the layer.
- Toggle image editor opens next to the 3D view and closes on second
  press.
