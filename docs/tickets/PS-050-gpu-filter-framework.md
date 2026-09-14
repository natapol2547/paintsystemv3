# PS-050 GPU image filter framework

Epic F. Size L. Milestone M3.

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

- `filters/gpu.py`:
  - `ImageTarget(image, tile)`: uploads a tile with
    `gpu.texture.from_image` (or `GPUTexture` from `pixels` for float
    buffers), owns a ping-pong pair of `GPUOffScreen` at tile size,
    `read_back()` copies the result into `image.pixels` through
    `Buffer` + `foreach_set` and calls `image.update()`.
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
  selection mask when one exists (PS-091), pushes the before/after
  pixels through the pixel undo stack (PS-090; a bare `ed.undo_push`
  does not restore scripted pixel writes), saves or packs per PS-056.
  Optional `preview` flag re-runs passes on parameter change with a
  modal redraw and commits on confirm.
- Alpha handling is a shared convention: filters operate on
  premultiplied colour and unpremultiply on output, as v2's
  `_gaussian_blur_alpha_safe` did, so edges never bleed black.
- Headless tests use `gpu` in background mode, which Blender 5.x
  supports with `--factory-startup -b` when a GPU context is available;
  provide a CPU reference implementation for CI without a GPU.

## Acceptance

- Identity filter round-trips a 4K RGBA image with max error 0 for byte
  images and < 1e-6 for float.
- Applying a filter and pressing undo restores the previous pixels.
- UDIM image with two tiles filters both.
