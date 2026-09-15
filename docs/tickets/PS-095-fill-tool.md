# PS-095 Fill tool

Epic J. Size M. Milestone M3b.

## Goal

A bucket fill for the active image layer, with tolerance, contiguous and
global modes, limited by the selection, that crosses UV seams in the 3D
view the way the user sees the surface.

## v3 design

Seams are handled by where the flood runs, not by mesh adjacency.

- 3D view: the flood runs in screen space on a view render of the source,
  as the wand does (PS-093). The filled region reaches texels through the
  texel map (PS-092) and is refined at its edge in texel space. A fill that
  crosses a seam is continuous because the screen has no seams. Texels
  hidden from the view are not filled; filling around an object takes a
  second click from another angle, as with native projection painting.
- Image editor: the flood runs on the image in texel space.
- `filters/fill.py`:
  - `match_pass`: GPU pass giving a match texture against the seed colour
    by OKLab distance with alpha, from the active layer or the channel
    composite ("Sample Merged", PS-007). Fully transparent texels match a
    transparent seed.
  - Connectivity: numpy flood over the read-back match mask first; a jump
    flooding pass if PS-081 profiling asks for it. Limited by the selection
    mask when one exists (PS-091).
  - The fill is a PS-050 pass: the brush colour written with coverage
    `result * selection`, blend mode and opacity from the tool settings,
    then a dilation into the UV fringe through the texel map's coverage.
- `paint_system.fill` as a `WorkSpaceTool` in the 3D view, then the image
  editor. Settings: tolerance, contiguous, sample merged, fill mode:
  - `PIXELS`: flood from the clicked colour.
  - `SELECTION`: fill the selection mask, no flood.
  - `FACES`: fill the UV polygons of the selected faces.
- Pixels are written inside `record()` (PS-090), one undo entry per fill.

## Acceptance

- Contiguous fill of a region covering half of a 4K layer completes in
  under 150 ms.
- In the 3D view, a fill on a cube face that matches the adjacent face
  continues across the UV seam.
- With a selection, no texel outside it changes, and feathered edges fill
  partially.
- Tolerance 0 on a flat region changes exactly the region and its fringe.
