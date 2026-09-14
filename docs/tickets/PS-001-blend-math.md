# PS-001 Layer blend math: W3C compositing in generated library groups

Epic A. Size M. Milestone M1.

## Goal

One generated `.PS Lib Layer Blend [<BLEND>]` group per blend mode that
composites straight-alpha layers with the W3C compositing formula. MIX
matches v2 pixel for pixel. Other modes match v2 wherever the backdrop
is opaque and intentionally differ where it is transparent.

## v2 behaviour

`create_mixing_graph` (`paintsystem/graph/common.py:116-143`) composes
three groups per layer:

- `.PS Pre Mix` (inputs Over Alpha, Opacity; output Over Alpha). Effective
  alpha of the layer.
- `ShaderNodeMix` RGBA with the layer blend type, Factor 1, A = previous
  colour, B = layer colour.
- `.PS Post Mix` or `.PS Porter-Duff Over` (inputs Clip, Color, Alpha,
  Blended Color, Over Color, Over Alpha; outputs Color, Alpha).
  Porter-Duff is used when `blend_mode not in {MIX, PASSTHROUGH}` and the
  layer is not clipped (`common.py:118-120`).

Post Mix premultiplies both sides, does an over, and unpremultiplies:

```
ao = clip ? ab : ab + oa * (1 - ab)
co = (cb * ab * (1 - oa) + blended * oa) / ao
```

where `blended` is the Mix node output, which is `B(cb, cs)` everywhere,
including texels where `ab = 0` and `cb` is meaningless. The
premultiply / unpremultiply round trip is correct for straight-alpha
images; the flaw is only that non-MIX modes blend against a backdrop
that is not there.

Disabled layers bypass the whole graph (`common.py:137-142`).

## v3 design

Generate the groups in Python (`compiler/library.py::layer_blend_group`)
with this math, per texel, all colours straight alpha:

```
eff = as * opacity * mask * (clip ? ab : 1)
cs' = mix(cs, B(cb, cs), ab)              -- blend only where backdrop exists
ao  = clip ? ab : ab + eff * (1 - ab)
co  = (cb * ab * (1 - eff) + cs' * eff) / ao    -- 0 when ao == 0
```

`B` is the per-mode blend function, implemented with a `ShaderNodeMix`
of that blend type at factor 1 (MIX gives `B = cs`, so `cs' = cs` and
the whole thing reduces to Porter-Duff over). `cs' = mix(cs, B, ab)` is
the W3C `Cs' = (1 - αb)·Cs + αb·B(Cb, Cs)` term and is the one
intentional difference from v2: under non-MIX modes, texels with a
partially or fully transparent backdrop now show the layer colour
weighted by `ab` instead of a blend with an undefined colour.

- One group per entry in `BLEND_MODE_ITEMS`. Interface: Prev Color,
  Prev Alpha, Color, Alpha, Opacity, Mask, Clip (float 0/1, for PS-013);
  outputs Color, Alpha. No Viewer output.
- Convention for every socket on the chain: straight alpha, colour
  undefined where alpha is 0. Consumers must never rely on the colour of
  a transparent texel.
- Pre Mix, Mix and Post Mix fold into one group instance per layer.
- Bump `LIBRARY_VERSION`. Non-mixing groups (projection, parallax,
  aspect, tangent normal, occlusion) are still appended from
  `library2.blend` per PS-002; the three v2 mixing groups are not.

## Acceptance

- Headless parity test renders a 3-layer stack through v2 (old addon)
  and v3 for every blend mode at opacity 1.0 and 0.4, clipped and
  unclipped, over an opaque backdrop. Max channel difference < 1/255.
- Same test for MIX over a transparent and half-transparent backdrop.
  Max channel difference < 1/255.
- W3C property test for every non-MIX mode: a layer over a fully
  transparent backdrop renders as the layer alone; over a half-alpha
  backdrop the result equals `mix(cs, B(cb, cs), 0.5)` composited over.
- `tests/smoke_compile.py` still passes.
- Interface identifiers of regenerated groups are stable across rebuilds.

## Notes

The disabled-layer bypass is already expressed as Opacity 0. Keep it that
way so toggling `enabled` is a value patch, not a graph change.

PS-070 migration notes should mention the transparent-region change so
users comparing v2 and v3 renders of the same file know it is expected.
