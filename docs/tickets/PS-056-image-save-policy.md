# PS-056 Image save and pack policy

Epic F. Size S. Milestone M1.

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
