# PS-092 Texel position map

Epic J. Size M. Milestone M3b.

## Status

Done for the texel map. `gpu_passes/core.py`, `gpu_passes/texel_map.py`
and `tests/test_texel_map.py`. 25 checks passed on 5.2.1 headless and on
4.2.23 windowed when it landed; with PS-093's surface keys and depth
batches the file has 39 checks, all passing on 5.2.1 headless and on
4.2.23 windowed.

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
- **Background Blender can run GPU passes from 5.2.** `gpu.init()` builds
  a context with no display at all - it found the real GPU on this
  machine with `DISPLAY` unset - so the GPU tests run in the ordinary
  headless job on 5.2. 4.2 to 5.1 have no equivalent (checked on 4.5.13,
  5.0.1 and 5.1.2) and `gpu_available()` returns False there, so
  `tests/run.sh --ui` runs the texel map test windowed as well to keep
  them covered.
- **GPU objects must be given back before Python shuts down.** Freeing a
  texture during interpreter teardown segfaults a background Blender, so
  `gpu_passes` registers only to get an `unregister` that calls
  `texel_map.release()`.
- **Two MAT4 push constants exceed what Vulkan guarantees** (144 bytes
  against 128). World transforms are applied with numpy while the vertex
  arrays are built, which leaves 24 bytes of push constants.

Found while building PS-091 milestone 1 and resolved by PS-093:

- `_triangle_arrays` fell back to the mesh's active UV map when the
  named one was missing. `VIEW` selection ops must not: a silent fallback
  changes the selection without telling the user. `get_texel_map`,
  `get_position_batch` and `_triangle_arrays` take `fallback_to_active`,
  and `VIEW` ops pass False and report `SURFACE` instead. A UV map name
  of `''` resolves to the active render map's name first
  (`resolve_uv_map`), so the cache key names a real map.
- Maps were dropped on every geometry update, and undo, redo and a
  stroke undo all report one, as does entering texture paint mode. On
  5.3 alpha every native stroke reports an Object geometry update too.
  Maps are now keyed by the surface key (`gpu_passes/surface.py`,
  PS-093), and nothing drops them on a geometry update, an undo or a
  redo: the handlers only mark surfaces suspect. A map survives while
  the mesh content is unchanged, and undoing a vertex move finds the map
  built before it.
- The selection stencil writes `mesh.uv_layer_stencil_index`, which is a
  geometry update. It used to drop the object's maps once per holding;
  it no longer drops anything.

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
  runs `gpu.init()` in a background session from 5.2; `read_color()`
  reads a framebuffer slot back through a one-dimensional
  `gpu.types.Buffer` passed as `read_color(..., data=buf)`, because a
  multi-dimensional buffer reports reversed strides on 4.2 (PS-096
  spike 4).
- `gpu_passes/texel_map.py`:
  - `get_texel_map(obj, uv_map, size, tile=1001, margin=4)` draws the
    evaluated mesh's loop triangles into a `GPUFrameBuffer` with two
    colour slots, using the UV coordinate as clip position. The
    `TexelMap` it returns holds two textures: `position` (RGBA32F: world
    position, coverage in alpha) and `normal` (RGBA16F: world normal).
    One shader, two fragment outputs, one pass. A missing UV map gives
    None.
  - Margin: the same batch drawn first with each triangle grown outward
    from its UV centroid by the margin, writing `MARGIN_COVERAGE` (0.5)
    in alpha, then again at its real size writing 1.0. A tool that wants
    real surface tests alpha `> 0.75`; one that wants every paintable
    texel tests `> 0.0`.
  - `get_texel_map` caches maps per key. The key is the
    object's `session_uid`, the resolved UV map name, the size, the tile,
    the margin, the world matrix and the surface key from
    `surface.resolve_key`. Maps store world positions, so a move gives a
    new key; the surface key leaves the transform out. Without a surface
    key (Edit Mode) the map is built and not cached.
    `depsgraph_update_post`, undo and redo only mark surfaces suspect
    (PS-093); a file read drops every map.
  - `get_position_batch(obj, uv_map)`: the
    same triangles as a world-position `TRIS` batch for depth passes,
    keyed like the maps. A map and a batch that both miss in one build
    share one `_triangle_arrays` extraction.
  - The cache evicts least recently used maps and batches over
    `CACHE_BUDGET` (1 GB), since two 4K maps alone come to 768 MB. A
    batch is counted as 12 bytes per vertex. Maps of a surface that
    changed are not dropped at once, so up to the budget of old maps can
    stay on the GPU.
- The view pass lives in `selection/view_raster.py` (PS-093), its first
  consumer: a depth render of the painted object only, at region size,
  and one banded texel pass that projects each position, tests it
  against the screen shape, facing and depth, and combines it into the
  selection mask.
- UDIM: one map per tile the object's UVs touch (PS-009).
- Modifiers, shape keys and mirror come from the evaluated mesh.
- The map stays on the GPU. A numpy read back is a separate call for
  tools that need it, through a preallocated one-dimensional
  `gpu.types.Buffer` passed as `read_color(..., data=buf)`: on 4.2 a
  multi-dimensional buffer reports reversed strides and `np.frombuffer`
  fails, while the one-dimensional buffer is copy-free on both versions
  (PS-096 spike 4). On 4.2 `GPUFrameBuffer.viewport_set` takes no
  arguments.
- GPU tests run headless from 5.2, once on the default backend and once
  on Vulkan through Mesa's lavapipe, and in the windowed job (Mesa under
  Xvfb) on every series, which is the only GPU coverage 4.2 to 5.1 get.
  PS-096 spike 4 ran every pass on llvmpipe; the Xvfb and lavapipe steps
  are PS-080's.

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
