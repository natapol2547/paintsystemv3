# PS-092 Texel position map

Epic J. Size M. Milestone M3b.

## Status

Done for the texel map. `gpu_passes/core.py`, `gpu_passes/texel_map.py`
and `tests/test_texel_map.py`; `handlers/node_tree_handlers.py` drops
cached maps on a geometry or transform update, an undo, a redo and a file
read. 25 checks pass on 5.2.1 headless and on 4.2.23 windowed.

Four things the plan had wrong or had not reached:

- **The margin is drawn, not flooded.** A jump flood wants two ping-pong
  seed textures and a gather pass into fresh position and normal targets,
  about 1 GB of video memory at 4K against the 400 MB the map itself
  occupies. Instead each triangle is rasterised twice, once grown outward
  from its own UV centroid by the margin and once at its real size over
  the top, which is what Blender's bake margin calls Extend. One extra
  draw, no extra memory. The margin continues each triangle's own surface
  rather than repeating the nearest island texel; for a four texel margin
  the two are visually the same, and the continuation is the better value
  for a tool to read.
- **Background Blender can run GPU passes from 5.0.** `gpu.init()` builds
  a context with no display at all - it found the real GPU on this
  machine with `DISPLAY` unset - so the GPU tests run in the ordinary
  headless job on 5.2. 4.2 has no equivalent and `gpu_available()`
  returns False there, so `tests/run.sh --ui` now runs the texel map test
  windowed as well to keep 4.2 covered.
- **GPU objects must be given back before Python shuts down.** Freeing a
  texture during interpreter teardown segfaults a background Blender, so
  `gpu_passes` registers only to get an `unregister` that calls
  `texel_map.release()`.
- **Two MAT4 push constants exceed what Vulkan guarantees** (144 bytes
  against 128). World transforms are applied with numpy while the vertex
  arrays are built, which leaves 24 bytes of push constants.

Also settled: `GPUFrameBuffer.viewport_set` takes no arguments at all on
4.2, not even keywords. It is never called, because binding a framebuffer
already sets the viewport to its own size on both versions.

Measured on an Intel UHD 620, factory cube at 4096x4096: 92 ms to build
on 5.2 and 75 ms on 4.2, 384 MB of textures; RGBA32F read back 767 ms on
5.2 and 1222 ms on 4.2. The read back is far slower than PS-096 spike 4
saw on an RTX 2060 and is the reason tools should stay on the GPU. The
test ceiling is 2000 ms for the build so a software renderer in CI does
not flake.

## Goal

One cached GPU buffer that tells every texel of a layer image where it
sits on the mesh: world position, normal and coverage. With it, anything
done in the 3D view (a lasso, a flood in screen space, a moved decal)
reaches the image as a flat pass over texels. No tool needs mesh
adjacency or seam handling, and the mesh is drawn once per change
instead of once per operation.

## v3 design

- `gpu_passes/core.py`: what every pass needs, and what PS-050's filter
  framework will sit on. `gpu_available()` probes the context once and
  runs `gpu.init()` in a 5.x background session; `read_color()` reads a
  framebuffer slot back through a one-dimensional `gpu.types.Buffer`
  passed as `read_color(..., data=buf)`, because a multi-dimensional
  buffer reports reversed strides on 4.2 (PS-096 spike 4).
- `gpu_passes/texel_map.py`:
  - `build_texel_map(obj, uv_map, width, height, tile=1001, margin=4)`
    draws the evaluated mesh's loop triangles into a `GPUFrameBuffer`
    with two colour slots, using the UV coordinate as clip position.
    Outputs: `position` (RGBA32F: world position, coverage in alpha) and
    `normal` (RGBA16F: world normal). One shader, two fragment outputs,
    one pass.
  - Margin: the same batch drawn first with each triangle grown outward
    from its UV centroid by the margin, writing `MARGIN_COVERAGE` (0.5)
    in alpha, then again at its real size writing 1.0. A tool that wants
    real surface tests alpha `> 0.75`; one that wants every paintable
    texel tests `> 0.0`.
  - `get_texel_map(obj, uv_map, size, tile, margin)` caches maps per key.
    The key includes the object's `session_uid`, the UV map name, the
    size, the tile, the margin and the world matrix;
    `depsgraph_update_post` drops maps of objects whose geometry or
    transform changed, and undo, redo and a file read drop all of them.
    The cache evicts least recently used maps over `CACHE_BUDGET`
    (1 GB), since two 4K maps alone come to 768 MB.
- `gpu_passes/view.py` moves to PS-093, which is the first consumer:
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
- GPU tests run in the ordinary headless job on 5.x and in the windowed
  one (Mesa under Xvfb) for 4.2, which has no background GPU context.
  PS-096 spike 4 ran every pass on llvmpipe; the Xvfb job is PS-080's.

Measured in PS-096 spike 4 at 4K (factory cube / 1M-triangle grid):
draw 1.2 / 6.1 ms on an RTX 2060 (5.2 and 4.2), 19 / 55 ms on Intel
UHD, 50 / 210 ms on llvmpipe; RGBA32F read back 100–180 ms on 5.2 and
240–300 ms on 4.2; face-centre check max error 9.77e-4 everywhere.

## Acceptance

- On the factory cube, every covered texel holds the world position its
  own UV maps to on the surface, within 1e-3. Each face's UVs are affine
  in its plane, so three corners give the exact answer for every texel of
  that island, not just its centre.
- Texels outside every island but within the margin carry
  `MARGIN_COVERAGE`, hold a position on the surface their triangle
  extends from, and are no further than the margin from an island.
  Beyond it, coverage is 0.
- Moving the object moves every position by the same amount, and a UDIM
  tile holds only the UVs that fall inside it.
- A 4K map draws in under 100 ms on a discrete GPU and under 250 ms on
  llvmpipe, stays on the GPU, and is reused until the mesh changes or the
  cache budget evicts it.
