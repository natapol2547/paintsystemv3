# PS-095 Fill tool with seam-aware flood

Epic J. Size M. Milestone M3.

## Goal

A bucket fill for the active image layer that respects the selection,
supports tolerance and contiguous/global modes, and in the 3D view
continues across UV seams.

## Reference

Pixel Art Studio (`core/floodfill.py`, `core/surface_fill.py`,
`ps_paint.py:1365-1456`, GPL-3.0-or-later):

- `match_mask`: per-channel Chebyshev distance on linear RGBA against
  the seed colour, and any fully transparent texel matches a
  transparent seed.
- `flood_mask`: global fill is the match mask; contiguous fill is a
  4-connected row-span scanline flood (`connected_mask`).
- `SurfaceFill`: per-face UV domains and a portal table. For each mesh
  edge shared by two faces, the edge is subdivided at texel crossings
  and each sub-segment maps to a texel pair on both sides. The flood
  runs per face and pushes portal neighbours onto the stack, so it hops
  UV islands through mesh adjacency. Blocked portals (colour mismatch on
  the far side) stop it. Cached per `(w, h)` on the mesh pick.
- After filling, `_bled` dilates into the UV fringe so seams do not
  show.

## v3 design

- `filters/fill.py`:
  - `match_pass`: GPU pass producing an R8 match texture from the source
    (active layer, or the channel bake when "Sample Merged" and PS-007
    bake exists), seed colour, tolerance and the transparent rule.
    Read back as numpy bool.
  - `connected(match, seeds)`: numpy row-span flood ported from
    Pixel Art Studio, with a `limit` mask from the selection (PS-091).
  - `SeamPortals(obj, image, tile)`: generalisation of `SurfaceFill`
    to float UVs. For each shared edge, sample at every texel crossing
    on either side and record `(texel_a, texel_b)`. Built from the
    evaluated mesh, cached per mesh token, invalidated by
    `depsgraph_update_post`. Portals apply to contiguous fills only.
  - The fill itself is a PS-050 pass: colour (or the current brush
    colour, or a gradient later) written where the result mask is set,
    with blend mode and opacity from the tool settings, then a one-texel
    dilate limited to the UV fringe (PS-092's coverage mask).
- `paint_system.fill` modal tool as a `WorkSpaceTool` in both editors:
  click to fill at the cursor texel (2D) or at the raycast hit (3D).
  Settings: tolerance, contiguous, sample merged, fill mode
  (`PIXELS`, `SELECTION`, `FACES`). `SELECTION` fills the current mask
  without a flood; `FACES` fills the UV polygons of the selected faces.
- Undo through PS-090.

## Acceptance

- Contiguous fill on a 4K layer with a 50 % coverage region completes in
  under 150 ms.
- Fill on a cube face in the 3D view continues onto the adjacent face
  across the UV seam.
- With a selection active, no texel outside it changes.
- Tolerance 0 on a flat region changes exactly the region and its
  one-texel fringe.
