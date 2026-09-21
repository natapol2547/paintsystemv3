# PS-051 Gaussian blur and sharpen on the GPU

Epic F. Size M. Milestone M3.

## v2 behaviour

`gaussian_blur` / `sharpen_image` (`operators/image_operators.py:178,
208`): alpha-premultiplied blur (`basic_filters.py:33`), sharpen as an
unsharp mask against a sigma 1.0 blur with the original alpha restored.
Parameters: radius/sigma, strength.

## v3 design

- Blur: a separable fragment pass run once per axis, as
  `registry.BLUR`. Premultiplied, so a transparent neighbour contributes
  no colour and an edge does not fade towards black, which is what v2's
  `_gaussian_blur_alpha_safe` was for.
- Sharpen: the blur passes into the chain's own target, then a combine
  pass `orig + strength * (orig - blurred)` on colour only, alpha
  copied. The radius is a parameter rather than fixed at sigma 1.0,
  since the passes are already there.
- Both registered in PS-050 with the v2 property names so the operator
  dialogs look the same, both available as filter-layer kinds (PS-057)
  through `filters/layer_specs.py`, and both available as destructive
  actions (PS-052) through `ops/pixel_ops.py`. The action form asks for
  a radius in a dialog before it runs, because running it again is a
  second full pass over the layer rather than a redo of a cheap one.

### What was built differently

- **The weights are evaluated in the shader**, not computed on the CPU
  and uploaded. A 63-tap kernel is 256 bytes of push constant, past the
  block a Vulkan driver is obliged to offer, and an `exp` per tap costs
  far less than the texture fetch beside it. The shader takes `sigma`
  and `radius` and nothing else.
- **A wide blur is run again rather than widened.** Blurring n times
  with sigma s is a blur with sigma `s * sqrt(n)`, so `blur_passes`
  splits a sigma past one kernel into iterations of two passes each,
  instead of downsampling. Downsampling is the better answer and is not
  built: it needs a pass that reads at a different size from the one it
  writes, which the `apply(texel, c)` contract of `filters/core.py`
  cannot express -- `_MAIN` fetches the source at the target's own
  coordinates. That is the follow-up, and it is what would lift the cap
  below.
- **No wrap edge mode.** Sampling clamps to the edge. The derived image
  of a filter layer is a UV layout rather than a tiling pattern, and
  wrapping would fold the far side of the map into the near one. The
  option belongs with the tiling-texture work, not here.
- **A kind runs a list of passes.** `LayerFilterSpec.passes_of` replaced
  the single `filter` plus `params_of`, because the number of passes a
  blur needs is a function of its sigma. A kind that returns an empty
  list asks for the stack below unchanged, which is how a blur of zero
  costs nothing rather than running an identity kernel. The build
  fingerprint hashes the derived pass list rather than the node
  properties behind it, so it records what the pixels actually depend
  on.
- **A pass can read a second texture.** `FilterSpec.reads_second` and a
  third sampler on every filter shader, which `run_pass` binds to a 1x1
  placeholder for the specs that do not use it. The unsharp mask needs
  the picture the blur was made from, and by then the chain holds the
  blur. `layer_build` keeps the composite out of the pool for the whole
  chain when some pass asks and not otherwise, so the cost is one
  texture and only for Sharpen.
- **The unsharp difference is taken on the sRGB encoding**, as Invert
  is, because a high pass on scene-linear values responds to a highlight
  far more than to a shadow of the same visible contrast -- `strength`
  would mean something different in each half of the picture. The blur
  feeding it runs in linear like the Blur kind, which leaves the detail
  as the difference between an encoded original and the encoding of a
  linear blur. It is still exactly zero wherever the picture is flat,
  which is the property that matters, and it saves two full passes that
  would otherwise encode and decode around the blur.
- **The Blur and Sharpen actions blur a byte layer in linear light.** A
  byte sRGB layer stores encoded values, and blurring those directly
  darkens every edge between two colours, so the same blur looked
  different on a byte layer, a float layer and a filter layer. The
  action wraps the blur passes in `DECODE_SRGB` and `ENCODE_SRGB` for a
  byte sRGB image, which costs two cheap per-texel passes. A byte image
  in another colour space (Non-Color data) is blurred as stored. This
  departs from Blender's own Soften brush, which works on the stored
  bytes.
- **A masked action masks once, at the end.** `core.apply_passes` runs
  one pass with the selection mask inline, as the single-filter path did
  before it,
  and several passes unmasked followed by a `_COMPOSE` pass that lays
  the chain's result over the original through the mask. Masking every
  pass would be wrong rather than slow: pass two would read texels the
  mask had already faded back towards the original and blur those, so
  the result inside a soft edge would depend on how many passes the
  sigma happened to need. Composing once also keeps the promise a
  destructive edit has to keep, which is that a texel the selection
  leaves out comes back byte for byte.

## Measurements

A filter-layer rebuild over a 2048² noise source, on the probe machine
(Blender 5.2.1, headless GPU context). Best of two after a warm-up. "GPU"
is everything before the commit; the rest is the write and the pack,
which PS-057 measures on its own.

| Resolution | sigma 0 | sigma 4 | sigma 21 | sigma 42 |
| --- | --- | --- | --- | --- |
| | 0 passes | 2 passes | 2 passes | 8 passes |
| 1024² | 17 ms | 29 ms | 59 ms | 207 ms |
| 2048² | 55 ms | 85 ms | 249 ms | 841 ms |
| 4096² | 260 ms | 478 ms | 1020 ms | 3351 ms |

The cap comes from this table. Sixteen iterations, which is where the
arithmetic would naturally stop, costs 3.4 s at 2048² and 13 s at 4096²
-- not a slider anybody can drag, and hopeless on the auto-refresh path,
which would spend that long in 0.02 s slices. Four iterations puts the
worst case at about a second at the default resolution.

The total rebuild is often *lower* at a wide sigma than at a narrow one,
because the pack dominates it and a blurred picture is a smaller PNG.

## Known gaps

- Sigma is in texels of the image being built, so raising a filter
  layer's resolution makes the same number a finer blur. Expressing it as
  a fraction of the image needs the cap to scale with the resolution,
  which needs the downsample pass.
- The effective sigma stops at `BLUR_MAX_EFFECTIVE_SIGMA` (42 texels).
  Bloom and glow widths are out of reach until the downsample pass
  lands.
- A pass has one `storage` constant, so a spec reading a second texture
  needs both to hold alpha the same way. True everywhere in the layer
  build, where every texture in the chain is straight; an action path
  starting from a float image would have to convert.

## Acceptance

- Blur of a single white pixel at sigma 2 matches a numpy reference
  within 1/255. **Done** -- `tests/test_filter_blur.py` matches it to
  within 2e-4, the passes running in `RGBA32F`.
- A flat picture blurs to itself, edges included: the weights normalise
  and the taps past the edge clamp rather than reading zero. **Done.**
- Colour does not bleed towards black across a transparent edge.
  **Done.**
- A filter layer set to Blur builds a result matching a numpy blur of
  what the same layer builds at sigma zero, within 2/255. **Done.**
- An unsharp mask leaves a flat picture alone, leaves alpha alone, and
  is not a filter at all at strength zero. **Done.**
- A filter layer set to Sharpen matches the reference through the whole
  build, and a step edge survives it -- which a combine reading its own
  input instead of the composite would turn back into the blur.
  **Done.**
- Blur and Sharpen run as destructive actions, and a selection stops
  them: every texel it leaves out comes back byte for byte, and a
  radius that rounds to nothing refuses by name instead of writing the
  layer back over itself. **Done** --
  `tests/test_selection_actions.py`.
- ~~8K image blurs in under 2 seconds on the user's machine.~~ Withdrawn.
  PS-050 records why the speed acceptances of that generation are
  unreachable: the transport is `Image.pixels`, and PS-057's own
  measurements put a 4096² pack alone at over two seconds. The table
  above replaces it.
