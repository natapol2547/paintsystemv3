# PS-020 Image layer full port

Epic C. Size L. Milestone M1.

## v2 behaviour

- Graph (`graph/basic_layers.py:423-431`): Texture Image with the layer
  image, interpolation Closest, coords from `create_coord_graph`,
  `color_output_name` / `alpha_output_name` select Color or Alpha outputs
  (e.g. use the image Alpha as colour).
- Creation `paint_system.new_image_layer` (`layers_operators.py:45-142`):
  modes NEW (resolution presets 1024/2048/4096/8192/CUSTOM, `use_float`,
  UDIM when detected), IMPORT (file select, `check_existing`), EXISTING
  (`prop_search`). Unique "Image.NNN" name. Coord/UV dialog from
  `PSUVOptionsMixin`.
- Image settings drawn from the live node (`panels/common.py:207-252`
  `image_node_settings`): interpolation, projection, extension, source,
  colour space, alpha mode, UDIM tile list, export and `MAT_MT_ImageMenu`.
- `correct_image_aspect` (`data.py:1018`). DECAL/PROJECT force extension
  CLIP (`data.py:1155`).
- `create_ps_image` (`data.py:541`): `images.new(alpha=True,
  float_buffer=use_float)`, generated colour transparent, saved at once.

## v3 design

`PaintSystemImageLayerNode` already exists with `image` and `uv_map`.
Extend it:

- Mix in `CoordMixin` (PS-008); drop the bare `uv_map` in favour of the
  mixin's `uv_map_name`.
- Node properties `interpolation`, `projection`, `extension`,
  `color_output` (COLOR/ALPHA), `alpha_output` (ALPHA/COLOR/NONE). These
  are IR-managed values on the Texture Image node. Image-level settings
  (colour space, alpha mode, source, tiles) stay on the image datablock
  and are drawn from it, as in v2.
- Setting `coord_type` to DECAL/PROJECT sets `extension = 'CLIP'` in the
  property update, mirroring v2.
- Operator `paint_system.new_image_layer` with the three modes, using
  `ops/mixins.py` (PS-009) and `create_managed_image`. The dialog layout
  must match v2 (`draw` at `layers_operators.py:117-142`).
- `emit_source` returns the selected outputs; when `alpha_output ==
  'NONE'` alpha is the constant 1.0 (v2 `_NONE_`).
- Row icon: image preview (`image.preview_ensure().icon_id`) when the
  image has pixels, else the `image` custom icon (`panels/common.py:531`).

Improvement over v2: image node settings are node properties, so they
survive recompiles and copy with the layer.

## Acceptance

- All three creation modes produce a layer whose compiled Texture Image
  references the right image; NEW packs the image.
- UDIM creation on a two-tile mesh.
- `color_output = 'ALPHA'` compiles the Alpha socket into the colour path.
- Smoke test extended with interpolation/extension patches (no churn).
