# PS-055 Export image and export all

Epic F. Size S. Milestone M3.

## v2 behaviour

`export_image` (`operators/bake_operators.py:349`, `image.save_as` INVOKE),
`export_all_images` (`:375`): directory picker, requires at least one
bake image, filenames from the image name with optional space-to-
underscore, `.<UDIM>` marker for tiled images, `.png`, `as_copy`,
creates the directory, reports per-channel failures. Entries in the bake
menus and `image_node_settings`.

## v3 design

Port over `channel.bake_image` (PS-007) and layer images. Add an "Export
layers" option that exports every image layer of the active channel with
`<channel>_<layer>` names, which v2 users asked for on the issue
tracker.

## Acceptance

- Export all on a two-channel baked material writes two files with the
  expected names; UDIM images write one file per tile.
