# PS-001 Layer blend math: Porter-Duff compositing in generated library groups

Epic A. Size M. Milestone M1.

## Status

Done for the demo (M0 slice 2): `compiler/library.py::_build_layer_blend`
and `tests/test_blend.py`. The v2 parity fixtures are still open.

## Goal

One generated `.PS Lib Layer Blend [<BLEND>]` group per blend mode that
composites straight-alpha layers correctly over any backdrop, including
transparent and partly transparent ones, clipped or not.

## v2 behaviour

`create_mixing_graph` (`paintsystem/graph/common.py:116-143`) composes
three groups per layer:

- `.PS Pre Mix` (inputs Over Alpha, Opacity; output Over Alpha):
  `oa = alpha * opacity`.
- `ShaderNodeMix` RGBA with the layer blend type, Factor 1, A = previous
  colour, B = layer colour: `B(cb, cs)`.
- `.PS Porter-Duff Over` for non-MIX unclipped layers, `.PS Post Mix`
  for everything else (`common.py:118-120`).

Porter-Duff Over, premultiplied, with Clip wired in:

```
co_p = B * oa * ab  +  cs * oa * (1 - ab) * (1 - clip)  +  cb * ab * (1 - oa)
ao   = clip ? ab : ab + oa * (1 - ab)
co   = co_p / ao
```

Post Mix drops the middle term's `(1 - ab)` split:

```
co = (cb * ab * (1 - oa) + B * oa) / ao
```

For MIX (`B = cs`) unclipped that is the same as Porter-Duff Over. For
clipped layers over a partly transparent backdrop it is wrong: `oa` is
not scaled by `ab`, so the colour exceeds 1 as `ab` falls (a clipped
white layer at full opacity over `ab = 0.5` gives `co = 2`).

Disabled layers bypass the whole graph (`common.py:137-142`).

## v3 design

Generated in Python, one group instance per layer. All colours are
straight alpha. With backdrop `cb, ab` (Prev Color, Prev Alpha), layer
colour `cs` and layer coverage `es = Alpha * Opacity * Mask` (clamped to
0..1), each texel splits into three regions:

| Region | Weight | Colour |
|---|---|---|
| layer over backdrop | `es * ab` | `B(cb, cs)` |
| layer alone | `es * (1 - ab) * (1 - clip)` | `cs` |
| backdrop alone | `ab * (1 - es)` | `cb` |

```
ao = sum of the weights
co = weighted average of the colours      -- cb where ao == 0
```

Unclipped this is the W3C source-over with blending,
`Cs' = (1 - ab) * cs + ab * B`. Clipped it is source-atop: the result
alpha is `ab` and the colour is `mix(cb, B, es)`. It equals v2's
Porter-Duff Over for every input, and v2's Post Mix wherever v2's Post
Mix is correct (unclipped MIX, or an opaque backdrop).

The W3C spec applies `Cs'` to every operator, which for source-atop
would reintroduce `cs` weighted by `es * ab * (1 - ab)` in a region where
the layer has no backdrop. The coverage table above is what v2's
Porter-Duff Over already does and what the tests assert.

Node layout (MIX skips the blend and source nodes because `B = cs`):

```
kept          = mix(1, ab, clip)                -- share of the layer that survives
source_weight = es * kept
alpha         = source_weight + ab * (1 - es)
source_share  = source_weight / alpha           -- Math DIVIDE gives 0 for 0
source        = mix(cs, B, ab / kept)           -- ab unclipped, 1 clipped
Color         = mix(cb, source, source_share)
```

- One group per entry in `BLEND_MODE_ITEMS`. Interface: Prev Color,
  Prev Alpha, Color, Alpha, Opacity, Mask, Clip (float 0/1, for PS-013);
  outputs Color, Alpha. No Viewer output.
- Convention for every socket on the chain: straight alpha, colour
  undefined where alpha is 0. Consumers must never rely on the colour of
  a transparent texel.
- `LIBRARY_VERSION` 2. Groups from an older version are rebuilt in place
  on first use, keeping interface identifiers. Non-mixing groups
  (projection, parallax, aspect, occlusion) are still appended from
  `library2.blend` per PS-002; the three v2 mixing groups are not.

## Acceptance

- Done: `tests/test_blend.py` bakes every blend mode at opacity 1.0 and
  0.4 and checks the closed form for each backdrop: over a transparent
  backdrop the layer alone; over an opaque one `mix(cb, B, es)`, clipped
  or not; over a half-transparent one `mix(cs, B, 0.5)` composited over,
  or clipped `mix(cb, B, es)` at alpha 0.5; alpha and mask scaling; no
  NaN where the result is transparent. `B` comes from a bare Mix node
  bake, so the test checks compositing, not Blender's blend functions.
- Done: interface identifiers and nodes are stable across rebuilds; an
  older group is rebuilt with the Clip input.
- Open: headless parity test that renders a 3-layer stack through v2
  (old addon) and v3 for every blend mode at opacity 1.0 and 0.4,
  clipped and unclipped, over an opaque backdrop. Max channel difference
  < 1/255. Same for unclipped MIX over transparent and half-transparent
  backdrops.

## Notes

The disabled-layer bypass is already expressed as Opacity 0. Keep it that
way so toggling `enabled` is a value patch, not a graph change.

PS-070 migration notes should mention that clipped layers over partly
transparent pixels render differently from v2 (v2 overshoots there), so
users comparing v2 and v3 renders of the same file know it is expected.

### One group for every blend mode: tried and dropped

On 2026-09-17 the blend-mode Mix node was moved out of the group and
into the compiled tree, so that a single library group with a Blended
Color input served every blend mode. MIX layers linked their colour
straight into that input. Renders were unchanged (171 Cycles float bakes
over every mode, clip runs, folders and float channels matched exactly,
and so did an EEVEE render). It was dropped because the per-mode groups
cost little: a file only carries the groups of the modes its layers
use, their names start with a dot so most of the UI hides them, and an
unused group is not saved. The
shared group cost every stack instead. Measured on Blender 5.2.1 under
heavy unrelated machine load:

- MIX layers pay one more link each, and a divide and a colour mix
  inside the group that the MIX group skips. With 50 MIX image layers,
  EEVEE compiled the material about 6% slower (95% interval 3% to 17%)
  and rendered about 3% slower; an opacity edit with its compile took
  9% longer, and 16% longer at 100 layers.
- Other modes pay an extra node in the compiled tree. With 50 MULTIPLY
  layers an opacity edit went from 230 ms to 512 ms and a first build
  from 0.5 s to 4.8 s. Most of that is `NodeTreeBuilder` scaling
  quadratically with links and positioned nodes (PS-081).

Keeping a MIX-only group next to the shared one caps a file at two
groups and leaves MIX layers as they are, but still costs other modes the
extra node, for a cosmetic gain.
