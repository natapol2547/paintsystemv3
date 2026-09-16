# PS-056 Image save and pack policy

Epic F. Size S. Milestone M1.

## Status

Done for the demo (M0 slice 6).

- `common.py::save_image(image)` follows the v2 rules. It skips an image
  without unsaved changes. It packs an image that is already packed or has
  no file path. Otherwise it writes the image to its file. When that write
  raises, it logs a warning, clears `filepath_raw` and packs. A failed pack
  is logged instead of raised, and only `RuntimeError` is caught (v2 caught
  everything and left the fallback pack unguarded).
- `on_save_pre` calls it for every image a Paint System node points at,
  found through the node's `Image` pointer properties (today `image` and
  `cache_image`), plus every `ps_managed` image. Channel bake images and
  mask images are picked up the same way once they are node properties.
  Images inside property groups or collections on a node are not scanned
  yet.
- Not ported: v2's `refresh_image`, which reloads the active layer image
  after load (added in v2 commit e03c011, "Fix packed files bug", without
  a recorded reason). `tests/test_images.py` reads the pixels of packed
  and on-disk images back after reopening on every Blender in the matrix
  without it, and a reload would decode the image again on every file
  open. Revisit if a packed image shows stale pixels in the viewport after
  load.
- `tests/test_images.py` covers the acceptance below plus: a packed image
  with a path stays packed and its file is not written, a generated image
  the addon did not create is packed, an image no Paint System node uses
  and an image without changes are left alone.
- Open (PS-096 finding, 2026-09-16): a float image with alpha below 1,
  packed and reloaded, has its colour channels multiplied by alpha again
  (0.8 reloads as 0.4 at alpha 0.5) on 5.2 and 4.2; byte images reload
  unchanged. Before Epic J writes float layers, the pack path for float
  images needs a fix (alpha mode on pack, or save to EXR instead) and a
  test that packs and reloads a half-transparent float image.

## v2 behaviour

`save_image` (`paintsystem/image.py:108`): pack when already packed or no
filepath, else `save()`; on exception clear `filepath_raw` and pack.
`save_handler` (`handlers.py:117`, `save_pre`) walks every layer and bake
image and saves each dirty one. `refresh_image` (`:137`) reloads the active
image after load.

## v3 design

`handlers/node_tree_handlers.py::on_save_pre` currently packs dirty
`ps_managed` images. Extend:

- `common/image.py::save_image(image)` with the v2 rules.
- `on_save_pre` collects images referenced by any Paint System node
  (`image`, `cache_image`, mask images, `external_image`) and channel bake
  images, not only managed ones, so imported images on disk are saved too.
- `on_load_post` reloads the active layer image (v2 `refresh_image`).

## Acceptance

- Paint, save, reopen: strokes persist for packed and on-disk images.
- An image with a missing directory falls back to packing without
  raising.
