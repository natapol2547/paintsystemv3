# PS-009 `PS_UVMap` auto UV and UDIM tile detection

Epic A. Size S. Milestone M1.

## v2 behaviour

- `ensure_paint_system_uv_map(obj)` (`paintsystem/data.py:554`) creates a UV
  layer named `PS_UVMap` (`DEFAULT_PS_UV_MAP_NAME`, `graph/common.py:15`) by
  smart projecting with angle limit 30 degrees and island margin 0.005 when
  missing. Called from `Layer.update_node_tree` (`data.py:836`) for AUTO
  layers.
- `PSUVOptionsMixin` (`operators/common.py:89-200`): `use_paint_system_uv`
  and `coord_type` update each other; `get_coord_type` seeds from
  `preferred_coord_type`, then the group's last choice, then the first UV
  layer; `store_coord_type` writes back.
- `get_udim_tiles(obj, uv_map)` (`data.py:501`) derives tile numbers
  `1000 + row*10 + col` from UV coordinates; `PSImageCreateMixin.create_image`
  (`operators/common.py:268`) offers UDIM only for `coord_type == 'UV'` when
  tiles other than 1001 exist.

## v3 design

- `common/uv.py`: `ensure_paint_system_uv_map(obj)`, `get_udim_tiles(obj,
  uv_map)`, `DEFAULT_PS_UV_MAP_NAME`.
- Where to call `ensure_paint_system_uv_map`: not from `emit` (compile
  must not touch meshes). Call it from the add-layer operators and from
  `update_active_image` (PS-060) when the active layer is AUTO and the
  object lacks the map. Objects that gain the material later get the map
  the first time they become the paint object.
- `PSUVOptionsMixin` and `PSImageCreateMixin` port to `ops/mixins.py`, with
  the tree (not the group) storing the last used `coord_type` and
  `uv_map_name`.
- `create_managed_image` grows `tiles: list[int] | None` and creates UDIM
  tiles with `image.tiles.new`.

## Selections on UDIM layers

PS-091 clips native strokes through Blender's Stencil Mask, which takes
one image and samples a tiled one from tile 1001 only. A selection on a
UDIM layer therefore reports "UDIM layers are not supported yet" and
blocks painting. The rasteriser and the overlay already take a tile, so
what is missing is per-tile clipping. It needs its own design once UDIM
layers can be created here.

## Acceptance

- Adding an AUTO image layer on a cube with no UVs creates `PS_UVMap` and
  the compiled UV Map node references it.
- Test with UVs spanning 1001 and 1002 creates a tiled image with both
  tiles filled.
