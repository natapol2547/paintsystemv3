# PS-092 Screen-to-UV projection pass

Epic J. Size M. Milestone M3.

## Goal

One GPU pass that maps something drawn in the 3D viewport (a lasso, a
rectangle, a moved sprite) into the texel space of the active layer's
image, across UV seams, without any mesh-adjacency code. This is the
primitive behind 3D selection (PS-093) and 3D transform (PS-094).

## Why not Pixel Art Studio's approach

Pixel Art Studio crosses seams with a "paper fold" engine
(`ps_paper.py`, `ps_session.py:6407 _seam_maps`) that finds an integer
texel isometry between neighbouring faces. That only exists when UVs
are grid-aligned at uniform density, which is true for pixel art and
false for general painting. Blender's own projection paint handles seams
by working in screen space and projecting onto every visible face; this
pass does the same thing for our tools.

## v3 design

- `filters/project.py`:
  - `DepthPrepass(context)`: renders every visible mesh object in the
    viewport into a depth `GPUOffScreen` at region size using the
    region's `perspective_matrix`. Cached per redraw.
  - `project_stencil(context, obj, image, tile, stencil: GPUTexture, mode) -> GPUTexture`:
    draws the evaluated mesh of `obj` into a texture-space offscreen at
    image tile size. Vertex shader outputs `uv * 2 - 1` as position and
    passes world position to the fragment stage. Fragment projects the
    world position with the viewport matrix, discards when the screen
    point is off region, occluded (depth prepass with a slack of 2e-5
    in NDC) or back-facing, then samples `stencil` at the screen point.
    `mode` selects what is written: the stencil's red channel as a mask
    (selection) or its RGBA (sprite reprojection).
  - `stencil_from_polygon(points, region)`, `stencil_from_rect`,
    `stencil_from_ellipse`: rasterise a screen-space shape into an R8
    stencil texture with a single triangle-fan or SDF pass.
  - `sprite_to_screen(image, tile, mask, context)` (used by PS-094):
    renders the selected texels of the layer through the mesh into a
    screen-space RGBA sprite, the inverse of `project_stencil`.
- Texel coverage: draw with conservative rasterisation off but dilate
  the result by one texel (PS-051 dilate pass) so seams get the same
  bleed as native painting.
- UDIM: run once per tile that the object's UVs touch (PS-009).
- Mirror modifiers and shape keys are respected because the evaluated
  mesh is used. Multires and subdivision use the evaluated mesh at
  viewport level.
- Runs in the viewport's draw context from a modal operator; also usable
  from `paint_system.project_selection` for tests with a camera view.

## Acceptance

- Lasso around a cube corner in the viewport selects the matching
  texels on all three visible faces across two UV islands.
- Texels on faces hidden behind another object are not selected.
- Sprite round trip: reproject an unmoved sprite and compare with the
  source; max error under 2/255 on the covered texels.
- A 4K target with a 1M-triangle mesh completes in under 50 ms on a
  mid-range GPU.
