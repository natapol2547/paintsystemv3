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

Decided on 2026-09-21. The painter is a filter layer kind, Painterly,
rather than a destructive operator: it repaints the stack below and
stays editable, and PS-057 already provides the derived image, the
freshness stamps, the auto refresh and the Update button. A destructive
action can follow on the same core, as Blur and Sharpen did.

### The build

A filter layer kind has so far been a list of full-target fragment
passes. The painter is not one: part of it is per-stamp work that
belongs on the CPU, and the stamps are geometry rather than a pass. So
`LayerFilterSpec` gains an optional `build` hook, a generator that
takes the composited stack below and returns the painted texture,
yielding between units so the build stays cancellable. A kind with no
hook runs its `passes_of` as before.

Inside the hook, in order:

1. **Encode and premultiply.** The composite arrives scene linear and
   straight. v2 painted into stored byte values, which for a painted
   layer are sRGB, and its look depends on that -- the blur, the luma
   the gradient is taken on, and the edge of every "over" blend. So the
   stack is encoded to sRGB first and decoded again at the end, as
   Invert and Sharpen already do with `encode`.
2. **Analysis.** The existing gaussian passes blur the colour the
   stamps sample. A Sobel pass over a blur of the picture's luma, as in
   v2, writes the gradient field (`gx`, `gy`, magnitude), and a short
   chain of max-reduction passes finds the field's peak, which v2
   normalises the magnitude by and the Edge Threshold is relative to.
3. **Gather.** The stamp centres are uploaded as a small data texture,
   and one pass reads the blurred colour and the gradient at each of
   them into a target of the same small size. That readback is the only
   one before the result: a few hundred kilobytes for thousands of
   stamps, where reading the field back whole would be 256 MB at 4K.
   The ticket first planned to read back a downsampled field and plan
   from that; gathering at the exact centres is both cheaper and
   faithful to v2, which samples one texel per stamp.
4. **Planning, on the CPU.** Numpy over the gathered arrays: the
   transparent-centre and threshold skips, the stroke angle, the HSV
   jitter and, in the seam slice, the mirrored duplicates. Everything
   here is per stamp rather than per texel, so it is small, and it is
   testable without a GPU.
5. **Stamping.** Each step's stamps are drawn in the order they were
   planned, as rotated quads with premultiplied "over" blending
   (`gpu.state.blend_set('ALPHA_PREMULT')`), up to 32768 to a draw so
   that no single draw stalls the driver. The brushes are packed into
   one atlas per step, resized on the CPU to that step's stamp size as
   v2 did: sampling a 1024-texel brush for a 60-texel stamp without
   mipmaps aliases, and the Python `gpu` module cannot build a mip
   chain. A stamp larger than its brush keeps the brush at its own size
   and magnifies it, as does one whose atlas would not fit the GPU's
   largest texture. One atlas rather than one
   texture per brush, so the draw keeps the planned order -- grouping
   stamps by brush would stack every stamp of one brush under every
   stamp of the next.

The texture `build` returns goes through the build's usual sRGB encode,
readback and commit, so everything downstream of the kind is unchanged.

### Determinism

A filter layer rebuilds whenever the stack below changes, so a painter
that reshuffled its strokes on every build would boil the whole picture
on every stroke painted under it. The seed is therefore a node property
and always used, and every random number a build needs -- positions,
brush choices, rotation jitter, colour jitter -- is drawn up front, per
step, before anything looks at the picture. A stamp that is skipped
still consumes its draws, so painting below changes the colour and the
angle of the stamps it reaches and never where any stamp lands.

### Where v3 differs from v2 on purpose

- **Stroke angle.** v2 holds its arrays top-down and rotates the brush
  counter-clockwise by the gradient angle measured in that y-down frame,
  which mirrors the brush against the gradient on diagonal edges: a 45°
  edge gets its stroke at 135°, while horizontal and vertical edges come
  out right. v3 aligns the brush with the gradient on every edge. The
  Rotation setting still turns every stroke by a fixed amount.
- **Colour jitter** is drawn per stamp from the seeded stream rather
  than from NumPy's global generator, so it repeats with the seed too.
- **Brush resize** box-filters before the bilinear step when a brush
  shrinks by a factor of two or more, where v2's bilinear alone skips texels.
  The stamp count is still computed from the resized brushes' covered
  area, v2's formula.

### Brushes

The two v2 presets ship in the package with the default circle. Custom
brushes, in a later slice, are Blender images chosen on the layer
rather than paths on disk: a filter layer rebuilds on its own, and a
path that does not exist on the machine that opened the file would
lose the brush without saying so, where a packed image travels with
the .blend. Decided with the user on 2026-09-21.

### Seams

Seam pairs come from the evaluated mesh of an object stored on the
layer, filled in from the active object on first build. Auto refresh
then works whatever is selected, and the seam index can be keyed by the
surface content key `gpu_passes/surface.py` already computes, which is
also what lets a UV edit mark the layer out of date. Decided with the
user on 2026-09-21; see the next section for why this is load-bearing.

### Slices

1. Painterly as a filter layer kind, without seams: the hook, the
   passes, the planning, the presets.
2. Seams: the stored object, the seam index from the surface arrays,
   the duplicates, a refusal by name when the layer has no mesh to read.
3. Custom brushes as Blender images.
4. A UV or mesh edit on the seam object marks the layer out of date.

## Working across seams is a requirement, not a refinement

Seam duplication is the part to design around rather than the part to
add last.
A stamp is the size of many texels, so every stamp near a seam lands
half on a shell that continues somewhere else in the map. Without the
mirrored instance the stroke stops dead at the seam and the model shows
a hairline of unpainted texels along every one of them, which is the
failure the whole feature is judged on.

It is also the one part of the painter that no other GPU path here can
lend anything to. Every pass built so far samples by normalised
coordinate with clamp-to-edge and knows nothing about the mesh: the
filter layers, the composite and the texel map all treat the image as a
flat rectangle. Seam pairs have to come from the mesh, which means the
painter needs a resolved object and UV map before it can plan a single
stamp -- unlike a filter layer, which works on the image alone as long
as the stack below shares one map (`filters/layer_plan.py`). Now that
the painter is a filter layer kind, that precondition is the first
thing its build checks, and a layer with no mesh to read refuses by
name rather than painting without seams.

## Acceptance

- Same stamp count as v2 for the same image, brushes and settings: the
  count formula is v2's, so this is exact, not approximate. The
  distribution is similar; an exact match is not expected, because the
  random stream and the stroke angle differ on purpose.
- The same settings and the same stack build the same pixels, and
  painting below moves no stamp, only its colour and angle.
- 4K image with default settings completes in under 10 seconds.
- Seam duplication produces continuous strokes across a UV seam on the
  factory monkey. This one is load-bearing: a painter that stops at a
  seam is not shippable, however good it looks inside a shell.
