# PS-093 Selection tools

Epic J. Size M. Milestone M3.

## Goal

Rect, ellipse, lasso, polygon lasso and magic wand selection tools in
the image editor and the 3D view, plus select all, none, invert, and
select by face or UV island.

## Reference

Pixel Art Studio (`ps_select.py`, `core/algo.py:185-412`,
`core/floodfill.py`, GPL-3.0-or-later):

- Rect is a span list; ellipse is a midpoint (Bresenham) ellipse with
  even/odd padding; lasso is an even-odd scanline polygon fill sampled
  at pixel centres. All blit through `spans_mask`.
- Lasso points are captured in region space and converted to texels on
  release.
- Wand is `flood_mask` on the active layer or the composite
  (`sample_merged`), with a per-channel tolerance.
- Operators share a base that snapshots, runs, and commits one undo
  entry.
- Their tools are a state enum inside one 9300-line modal; v3 uses
  `WorkSpaceTool` instead so Blender's toolbar, keymaps and tool
  settings work normally.

## v3 design

- `tools/select.py` registers `WorkSpaceTool` subclasses for
  `IMAGE_EDITOR` (paint and view modes) and `VIEW_3D` (texture paint
  mode): `paint_system.select_box`, `select_ellipse`, `select_lasso`,
  `select_polygon`, `select_wand`. Each is a modal operator with
  `mode` (`REPLACE`, `ADD`, `SUBTRACT`, `INTERSECT`) driven by Shift,
  Ctrl and Shift+Ctrl in the keymap, matching Blender's mesh select
  tools.
- 2D rasterisation is numpy: port `polygon_spans`, `ellipse_spans`,
  `spans_mask` from Pixel Art Studio into `selection/raster.py`.
  Region-to-texel conversion uses `region.view2d.region_to_view` and
  the image tile bounds.
- 3D shapes go through PS-092: the modal draws the marquee in screen
  space, and on release builds a stencil and calls `project_stencil`.
- Wand: match mask on the GPU (PS-095's `match_pass`) from the active
  layer, or from the channel bake when "Sample Merged" and the bake
  exists (PS-007), else the layer. Connectivity on the CPU with the
  row-span flood from PS-095. In 3D the seed comes from a raycast under
  the cursor.
- `paint_system.select_all(action='SELECT'|'DESELECT'|'INVERT')`,
  `select_faces(mode='SELECTED'|'ISLAND'|'MATERIAL')` rasterising UV
  polygons of the chosen faces with `polygon_spans`.
- Marquee overlay lives in `selection/overlay.py` (PS-091) as a small
  state bag the modal writes and the draw handler reads.
- Header and the Layers panel show the active selection tool settings:
  tolerance, contiguous, sample merged, feather (deferred to the
  selection filter ticket).

## Acceptance

- Each tool works in both editors; Shift adds, Ctrl subtracts.
- Lasso with 200 points on a 4K image rasterises in under 30 ms.
- Wand with tolerance 0 on a flat colour region selects exactly that
  region; tolerance 0.1 grows it to near colours.
- Select by UV island selects every texel inside the island's UV
  polygons and nothing else.
