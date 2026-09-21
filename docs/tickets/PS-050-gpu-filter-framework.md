# PS-050 GPU image filter framework

Epic F. Size L. Milestone M3.

## Status

The pass shipped on 2026-09-20 as `filters/core.py` and
`filters/registry.py`, checked by `tests/test_filters_gpu.py`. It is the
design below with six differences, all of which the spikes forced:

- The module is `filters/core.py`, not `filters/gpu.py`, and there is no
  `ImageTarget`. A `PixelSource` holds one image's values as a numpy
  array and a `GPUTexture` over the same memory.
- `gpu.texture.from_image` is not used. It reports an sRGB byte image's
  values as straight linear and CPU-converts other colour spaces, so
  nothing it returns can be written back unchanged. The source texture is
  built from `Image.pixels.foreach_get` instead, as `RGBA16F` for a byte
  image and `RGBA32F` for a float one.
- No ping-pong pair and no `GPUOffScreen`. One pass draws into one
  target texture in 512-row bands, and `gpu_passes.core.read_color` reads
  it back. Chaining passes waits for the first filter that needs more
  than one (PS-051).
- Alpha is not premultiplied on the way in. A filter is handed straight
  colour in the image's own storage space, and the prelude converts only
  for a float image, which Blender stores premultiplied. Premultiplying a
  byte image and dividing back would cost the exactness the acceptance
  asks for. Partial mask coverage still mixes premultiplied, so a
  feathered edge lowers alpha rather than darkening colour.
- The result goes to an output object rather than back to the source:
  `LayerImage` writes through `undo.pixels.write_pixels`, `ResultImage`
  into a separate image with no undo step, for a derived image under
  PS-090's rule. `ResultImage` has since gone: a filter layer's build
  packs a PNG it writes itself (PS-053), which is faster than writing
  the pixels and having Blender encode them. With one output left,
  `LayerImage` went too: `filters.actions.apply_passes` writes through
  `undo.pixels.write_pixels` itself.
- UDIM is not covered. `filters.actions` refuses a tiled image; the
  per-tile loop belongs with PS-009.

A `GPUFrameBuffer` does not keep its colour slot alive, and reading one
whose texture Python has already freed gives zeroes rather than raising.
`run_pass` therefore returns the framebuffer and its target together, and
every caller holds both until the read is done.

The exactness acceptance holds; the implied speed does not. A 4K action
costs about 0.9 to 1.1 s on 5.2 and 2.8 to 4.6 s on 4.2. The GPU pass is
roughly 50 ms of that: the rest is `foreach_get`, `read_color` and the
undo snapshot, all of which go through `Image.pixels`. PS-095's "half a
4K layer in 150 ms" and PS-051's "8K blur under 2 s" are unreachable
through that transport and need rewriting against what it costs.

## v2 behaviour

Filters were numpy on the CPU: `blender_image_to_numpy` /
`set_image_pixels` (`paintsystem/image.py:151, 340`) with UDIM tile
round-trips through temp files, `basic_filters.py` and
`brush_painter_core.py` (`operators/image_filters/`). Results were
written to a copied image assigned to the layer (non-destructive).
Slow at 4K and unusable at 8K.

## v3 design

Filters run on the GPU with the `gpu` module and modify the layer's
image in place, with undo.

- `gpu_passes/core.py` already exists (PS-092) and owns the parts that
  are not about filtering: `gpu_available()`, which probes the context
  once and runs `gpu.init()` in a background session from 5.2;
  `gpu_known()`, which gives the same answer without starting a context,
  or None when only `gpu.init()` could tell; and `read_color()`, which
  reads a framebuffer slot back through a one-dimensional `Buffer`
  passed as `read_color(..., data=buf)` because a multi-dimensional
  buffer reports reversed strides on 4.2 (PS-096 spike 4). On 5.2.1 and
  5.3 alpha `gpu.init()` crashes Blender instead of raising when EGL has
  no usable driver, so a `poll` uses `gpu_known()` and only a path about
  to draw calls `gpu_available()`. Build on the module rather than
  repeating it.
- `filters/gpu.py`:
  - `ImageTarget(image, tile)`: uploads a tile with
    `gpu.texture.from_image` (or `GPUTexture` from `pixels` for float
    buffers), owns a ping-pong pair of `GPUOffScreen` at tile size, and
    `read_back()` copies `core.read_color` into `image.pixels` with
    `foreach_set` and calls `image.update()`.
  - `run_pass(shader, target, uniforms, inputs)`: full-screen quad
    through a `GPUShader` built from a shared vertex shader and the
    filter's fragment source, using `gpu.shader.create_from_info` so it
    works on Vulkan and Metal backends in 5.x.
  - Passes chain by swapping the ping-pong targets.
- `filters/registry.py`: a `Filter` declares `id`, label, fragment
  source(s), a `PropertyGroup` of parameters, and `passes(params)`
  yielding pass descriptors. `MAT_MT_ImageFilterMenu` and the "Apply
  Image Filters" box are generated from it.
- `paint_system.apply_filter(filter_id)` operator: resolves the target
  image (bake image when Use Baked, PS-007, else the active layer or mask
  image), runs the passes per UDIM tile, limits the write to the
  selection mask when one exists (PS-091; per tile through `get_mask`,
  cancelling with the `MaskUnavailable` message rather than writing
  unrestricted), writes the result with `write_pixels` (PS-090, which
  registers it with Blender's image undo; a bare `ed.undo_push` does not
  restore scripted pixel writes, so the operator has no `UNDO` option),
  saves or packs per PS-056. Optional `preview` flag re-runs passes on
  parameter change with a modal redraw and commits on confirm.
- Alpha handling is a shared convention: filters operate on
  premultiplied colour and unpremultiply on output, as v2's
  `_gaussian_blur_alpha_safe` did, so edges never bleed black.
- Headless tests use `gpu` in background mode, which works from Blender
  5.2 through `gpu.init()` and needs no display (PS-092). 4.2 to 5.1 have
  no background GPU context, so their coverage comes from the windowed
  job: `tests/run.sh --ui`. A test that cannot draw calls `harness.skip`
  rather than failing, so no CPU reference implementation is needed.
  From 5.2 CI also runs a named list of GPU test files headless on
  Vulkan through Mesa's lavapipe (PS-080). The filter tests belong on
  that list, because the OpenGL jobs miss Vulkan-only faults.

## Acceptance

- Identity filter round-trips a 4K RGBA image with max error 0 for byte
  images and < 1e-6 for float.
- Applying a filter and pressing undo restores the previous pixels.
- UDIM image with two tiles filters both.
