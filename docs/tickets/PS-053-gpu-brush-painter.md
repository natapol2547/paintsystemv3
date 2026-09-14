# PS-053 Brush painter on the GPU

Epic F. Size L. Milestone M3.

## v2 behaviour

`brush_painter` (`operators/image_operators.py:238-400`) configures
`BrushPainterCore` (`operators/image_filters/brush_painter_core.py`,
`apply_brush_painting` at `:1273`): coverage density, min/max brush
scale, start/end opacity, step count, gradient threshold, gaussian sigma,
optional HSV shift, fixed seed, random rotation range, UV-seam
duplication. Brush sources: PRESET (folders under
`image_filters/brush_presets`), FOLDER, SINGLE file, DEFAULT circular.
Per-tile numpy stamping with positions/scales/rotations derived from the
image gradient field; UV-seam index mirrors stamps across seams. Minutes
at 4K.

## v3 design

Same look, GPU execution, in place on the image (PS-050):

1. Analysis passes: downsample, Sobel gradient (direction + magnitude)
   into a float texture, optional gaussian smoothing of the field.
2. Stamp planning on the CPU from the read-back gradient field (small,
   e.g. 512x512): positions by stratified random sampling weighted by
   density, rotation from the field angle, scale from magnitude and the
   step schedule. Output a flat instance buffer (pos, scale, rot, opacity,
   brush index, colour shift).
3. Stamp pass: instanced quads drawn with a brush texture array (presets
   loaded once into a `GPUTexture` array, padded to a common size),
   sampling the source image colour at the stamp centre, blended with
   premultiplied alpha into the ping-pong target. One draw call per step
   batch.
4. UV seam duplication: precompute seam edge pairs from the mesh once
   (CPU, same logic as v2's seam index), and for stamps within the brush
   radius of a seam append a mirrored instance in the paired edge's UV
   frame.
5. Progress through `wm.progress_*` per step batch; cancel with Escape via
   a modal operator.

Presets folder and UI dialog are ported unchanged.

## Acceptance

- Visual comparison against v2 output on the sample texture with a fixed
  seed: same stamp count, similar distribution (exact match is not
  expected because sampling differs).
- 4K image with default settings completes in under 10 seconds.
- Seam duplication produces continuous strokes across a UV seam on the
  factory monkey.
