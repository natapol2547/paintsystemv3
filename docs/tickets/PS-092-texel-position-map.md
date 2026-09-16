# PS-092 Texel position map

Epic J. Size M. Milestone M3b.

## Goal

One cached GPU buffer that tells every texel of a layer image where it
sits on the mesh: world position, normal and coverage. With it, anything
done in the 3D view (a lasso, a flood in screen space, a moved decal)
reaches the image as a flat pass over texels. No tool needs mesh
adjacency or seam handling, and the mesh is drawn once per change
instead of once per operation.

## v3 design

- `gpu_passes/texel_map.py`:
  - `TexelMap(obj, uv_map, width, height, tile=1001)`: draws the evaluated
    mesh's loop triangles into a `GPUOffScreen` with the UV coordinate
    as clip position. Outputs: `position` (RGBA32F: world position,
    coverage in alpha) and `normal` (RGBA16F: world normal).
  - Fringe: a jump flooding pass copies the nearest covered texel into
    uncovered texels up to a margin (default 4 texels), so tools write
    into the UV margin the way Blender's paint bleed does.
  - `get_texel_map(obj, uv_map, size, tile)` caches maps per key. The key
    includes the object's `session_uid`, the UV map name, the size, the
    tile and the world matrix; `depsgraph_update_post` drops maps of
    objects whose geometry or transform changed.
- `gpu_passes/view.py`:
  - `ViewDepth(region, rv3d)` or from stored matrices: a depth render of
    the visible mesh objects, for occlusion.
  - Shared GLSL: `project_texel(position, view_projection)` returns the
    screen coordinate, and `visible(...)` combines inside-region, the
    depth test with a small slack and facing (`dot(normal, view_dir)`).
- UDIM: one map per tile the object's UVs touch (PS-009).
- Modifiers, shape keys and mirror come from the evaluated mesh.
- The map stays on the GPU. A numpy read back is a separate call for
  tools that need it, through a preallocated one-dimensional
  `gpu.types.Buffer` passed as `read_color(..., data=buf)`: on 4.2 a
  multi-dimensional buffer reports reversed strides and `np.frombuffer`
  fails, while the one-dimensional buffer is copy-free on both versions
  (PS-096 spike 4). On 4.2 `GPUFrameBuffer.viewport_set` takes no
  arguments.
- GPU tests run in the windowed CI job (Mesa under Xvfb). PS-096 spike 4
  ran every pass on llvmpipe; the Xvfb job itself is PS-080's.

Measured in PS-096 spike 4 at 4K (factory cube / 1M-triangle grid):
draw 1.2 / 6.1 ms on an RTX 2060 (5.2 and 4.2), 19 / 55 ms on Intel
UHD, 50 / 210 ms on llvmpipe; RGBA32F read back 100–180 ms on 5.2 and
240–300 ms on 4.2; face-centre check max error 9.77e-4 everywhere.

## Acceptance

- On the factory cube, the texel at the centre of each face's UV island
  holds that face centre's world position within 1e-3.
- Texels outside every island within the margin hold their nearest
  island texel's position; beyond it, coverage is 0.
- A 4K map of a 1M triangle mesh draws in under 100 ms on a discrete
  GPU and under 250 ms on llvmpipe, stays on the GPU, and is reused
  until the mesh changes.
