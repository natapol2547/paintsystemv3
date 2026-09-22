# PS-055 Export image and export all

Epic F. Size S. Milestone M3.

## v2 behaviour

`export_image` (`operators/bake_operators.py:349`, `image.save_as` INVOKE),
`export_all_images` (`:375`): directory picker, requires at least one
bake image, filenames from the image name with optional space-to-
underscore, `.<UDIM>` marker for tiled images, `.png`, `as_copy`,
creates the directory, reports per-channel failures. Entries in the bake
menus and `image_node_settings`.

### v2 UI

All paths are relative to `~/paintsystem`. Line numbers without a file
refer to `operators/bake_operators.py`.

Entry points:

- The "Bake and Export" menu, `MAT_MT_PaintSystemMergeAndExport`
  (`panels/layers_panels.py:145-168`; PS-031 lists it in full). It is
  opened from the paint mode block, `toggle_paint_mode_ui`, for a mesh
  only (`panels/common.py:293, 309-314`). That block is drawn in the
  main panel without legacy UI (`panels/main_panels.py:172-173`) and
  in a box at the top of `MAT_PT_Layers` with it
  (`panels/layers_panels.py:531-533`). Under the
  label "Export Baked Images" it holds `paint_system.export_image`
  "Export Active Channel" (EXPORT, `image_name` set to the bake image)
  and `paint_system.delete_bake_image` "Delete Active Channel" (TRASH),
  both only when the active channel has a bake image. Without legacy
  UI, `paint_system.export_all_images` "Export All Channels" (EXPORT)
  follows.
- Legacy UI only: the channels box menu "Bake and Export"
  (TEXTURE_DATA, `panels/channels_panels.py:116-118`). The box is drawn
  inside the main panel's "Channels" section,
  `layout.panel("MAT_PT_ChannelsPanel", default_closed=True)`, so the
  menu shows only while that section is open
  (`panels/main_panels.py:211-215`). It is
  `MAT_MT_PaintSystemChannelsMergeAndExport`
  (`panels/channels_panels.py:15-34`, bl_label "Baked and Export"). An
  aligned column holds the label "Bake", "Bake All Channels" (custom
  `channels`), "Bake Active Channel (<channel>)" (channel icon), a
  separator, the label "Export", "Export All Images" (EXPORT) and
  "Export Active Channel (<channel>)" (EXPORT). The last is drawn even
  without a bake image, with no `image_name`, and then fails with
  "Baked Image not found."
- `image_node_settings` (`panels/common.py:207-237`). With
  `simple_ui` and an image set, the header of its
  `image_node_settings_panel` section holds an aligned row with the
  image field, `paint_system.export_image` (text "", FILE_TICK) for
  the node's image, and the menu `MAT_MT_ImageMenu` (COLLAPSEMENU).
  With no image the header is a `template_ID` with New and Open
  instead, and no export button. Both callers pass `simple_ui=True`:
  the layer
  settings "Image" section, where the button exports the layer's own
  image (`panels/layers_panels.py:324`, drawn only while that section
  is open), and the bake box (`panels/layers_panels.py:638`). The
  other branch, with "Export As..." (FILE_TICK,
  `panels/common.py:232-236`), is never drawn.

Export Baked Image (`paint_system.export_image`, `:349-372`): no
`bl_options`, no poll, no `invoke`. `image_name` is SKIP_SAVE. It
cancels without an active channel and reports "Baked Image not found."
(ERROR) when no image has that name. Otherwise it runs Blender's
`image.save_as` with INVOKE_DEFAULT under an `edit_image` override, so
the user gets Blender's own Save As Image browser and its format
options. It works on any image, not only bakes.

Export All Images (`paint_system.export_all_images`, `:375-506`), no
`bl_options`:

- Properties (`:380-397`): `directory` (DIR_PATH, SKIP_SAVE),
  `as_copy` "As Copy" (default off), `replace_whitespaces` "Replace
  Whitespaces" (default on).
- Poll: an active group (`:399-402`).
- `invoke` (`:404-414`) counts the active group's channels that have a
  bake image. With none it reports "No baked images found." (ERROR)
  and cancels before any browser opens. Otherwise `fileselect_add`
  opens Blender's file browser.
- Side panel (`draw`, `:416-446`), drawn by the file browser, no
  property split:
  - A box with the label "Images to export:" (IMAGE_DATA).
  - Inside it, a second box with an aligned column: one row per
    channel of the active group, in list order, each with the
    channel's socket icon (`panels/common.py:30-36`). A channel with a
    bake image reads "<channel>: <image>.png". The image name has its
    spaces replaced by underscores while Replace Whitespaces is on,
    and gains ".<UDIM>" before ".png" when the image has more than one
    tile. A channel without one reads "<channel>: No baked image" in a
    disabled row.
  - Back in the outer box: "No baked images found" (ERROR) when no row
    had an image, which `invoke` already rules out, else "Total: N
    images", with no singular form.
  - The "As Copy" and "Replace Whitespaces" checkboxes. The rows above
    follow Replace Whitespaces as it is toggled.
- `execute` (`:448-506`) cancels with "No active group found." or "No
  directory selected." (ERROR). It creates a missing directory, or
  cancels with "Failed to create directory: <error>" (ERROR). For each
  channel with a bake
  image it calls `image.save(filepath=..., save_copy=as_copy)` with the
  name the side panel showed (`:480-488`). The extension is always
  `.png`, whatever the image's file format. With As Copy off, the
  default, `Image.save` also points the bake image's filepath at the
  exported file. A failure reports "Failed to export <channel>:
  <error>" (WARNING) and the loop continues. At the end it reports
  "Exported N images to <directory>" (INFO) when any file was written
  and "Failed to export N images" (WARNING) when any failed; both can
  appear. When nothing was written it reports "No baked images found
  to export." (ERROR), after the failure warning if there was one, and
  cancels (`:496-506`).

## v3 design

Port over `channel.bake_image` (PS-007) and layer images. Add an "Export
layers" option that exports every image layer of the active channel with
`<channel>_<layer>` names, which v2 users asked for on the issue
tracker.

## Acceptance

- Export all on a two-channel baked material writes two files with the
  expected names; UDIM images write one file per tile.
