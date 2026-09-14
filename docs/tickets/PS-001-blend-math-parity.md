# PS-001 Match v2 blend math in the layer blend library groups

Epic A. Size M. Milestone M1.

## Goal

A v3 stack must render pixel-identical to the v2 stack for every blend mode,
opacity and clip combination, so migrated files do not change appearance.

## v2 behaviour

`create_mixing_graph` (`paintsystem/graph/common.py:116-143`) composes three
groups per layer:

- `.PS Pre Mix` (inputs Over Alpha, Opacity; output Over Alpha). Effective
  alpha of the layer.
- `ShaderNodeMix` RGBA with the layer blend type, Factor 1, A = previous
  colour, B = layer colour.
- `.PS Post Mix` or `.PS Porter-Duff Over` (inputs Clip, Color, Alpha,
  Blended Color, Over Color, Over Alpha; outputs Color, Alpha).
  Porter-Duff is used when `blend_mode not in {MIX, PASSTHROUGH}` and the
  layer is not clipped (`common.py:118-120`). Clip is a boolean input.

Disabled layers bypass the whole graph (`common.py:137-142`).

## v3 design

`compiler/library.py::layer_blend_group(blend_type)` currently uses a
simplified formula (`color = Mix(prev, color, eff)`, `alpha = prev + eff *
(1 - prev)`). Replace its body with the exact v2 math:

1. Dump the internal graphs of `.PS Pre Mix`, `.PS Post Mix` and
   `.PS Porter-Duff Over` from `library2.blend` headlessly and write the math
   down in this ticket before coding.
2. Generate `.PS Lib Layer Blend [<BLEND>]` in Python with that math. One
   group per blend mode; MIX and clipped layers use the Post Mix path,
   other modes use the Porter-Duff path. Keep the current interface
   (Prev Color, Prev Alpha, Color, Alpha, Opacity, Mask) and add a `Clip`
   float input (0/1) so PS-013 can reuse the same group.
3. Bump `LIBRARY_VERSION`.

Generate in Python rather than append the v2 groups: the v2 groups need a
Mix node outside them, and generation lets us fold Pre Mix, Mix and Post
Mix into one group instance per layer (one node instead of three).

## Acceptance

- Headless test renders a 3-layer stack through v2 (old addon) and v3 for
  every entry in `BLEND_MODE_ITEMS`, at opacity 1.0 and 0.4, clipped and
  unclipped. Max channel difference < 1/255.
- `tests/smoke_compile.py` still passes.
- Interface identifiers of regenerated groups are stable across rebuilds.

## Notes

The disabled-layer bypass is already expressed as Opacity 0. Keep it that
way so toggling `enabled` is a value patch, not a graph change.
