# PS-013 Clipping layers

Epic B. Size M. Milestone M1.

## Status

Done (M0 slice 5) as designed below, with the v2 pixel comparison
replaced by closed-form pixel tests in `tests/test_clip.py`.

- `is_clip` is on the base layer node. `stack_ops.clip_base(node)` finds
  the base from the links on every compile; `feeds_clip_run(node)` is true
  for the base and for clipped layers under the top of the run.
  `StackItem` has no `clip_base`: nothing outside the compiler needs it.
- In `PaintSystemLayerNode.emit` a base outputs its source unblended,
  each clipped layer blends with `Clip = 1` over the run so far, and the
  top of the run then calls the base's `emit_blend` on the result. The
  base's blend node keeps the base's identifier.
- The base's blend mode applies to the run as a whole, as does its
  opacity and visibility. v2 blended the base inside the run and put the
  run over the stack with `.PS Alpha Over`, so the base's blend mode never
  applied against the stack below.
- A clipped layer with no layer below it (bottom of the channel or of a
  folder) composites unclipped. Layers clipped to an empty folder are
  hidden, since the folder is their base.
- A cached base or a cached clipped layer under the top of its run
  compiles live (`CompileContext.is_cached`): their outputs are part of a
  run, and the top needs the base's inputs compiled. The top of a run
  caches the whole stack as usual.
- The blend's `Clip` input is set on every layer. The artifact builder
  reuses nodes by identifier and keeps the value of an input the IR stops
  setting, so setting it only for clipped layers left unclipped layers
  clipped.
- The list shows the `clipping` icon on clipped rows; the active layer
  box and the node have the `SELECT_INTERSECT` toggle, disabled while the
  layer is locked.

## v2 behaviour

- `Layer.is_clip` (`data.py:1269`), drawn as the SELECT_INTERSECT toggle in
  the blend row (`panels/common.py:316-377`) and as the `clipping` icon in
  the list.
- In `Channel.update_node_tree` (`data.py:1907-1930, 1959`) a run of
  clipped layers above a base layer is isolated: a `.PS Alpha Over` group
  composites base + clipped layers as a unit, then alpha-overs that unit
  onto the stack below. `Clip = layer.is_clip or type == 'ADJUSTMENT'`.
- Clipped layers are forced to MIX-compatible mixing (Post Mix path,
  `graph/common.py:118-120`).

## v3 design

Clipping is handled at compile time by rewriting what the base layer
blends. Given the chain (top-first) `C2, C1, B, ...` with C1 and C2
clipped:

1. Emit B's `emit_source` -> (bc, ba).
2. Emit C1 over (bc, ba) with its own blend group, `Clip = 1` (mask
   multiplied by `ba`, output alpha = `ba`). Then C2 over that.
3. Feed the result into B's blend group as B's source.

This gives the "composite inside the base, then blend the base" behaviour
of the v2 Alpha Over isolation with fewer nodes and no extra library
group.

- `PaintSystemLayerNode.is_clip: BoolProperty`. The stack walk marks
  `StackItem.clip_base` for clipped items. A clipped layer whose
  downstream neighbour is not a layer (bottom of chain, or a folder
  content root) compiles as unclipped, matching v2's "exit clip run"
  rule.
- The blend group's `Clip` input exists (PS-001): Clip 1 is
  source-atop, output alpha = backdrop alpha, colour
  `mix(cb, B(cb, cs), es)`, for every blend mode. Clipped layers no
  longer need to be forced onto MIX.
- Adjustment layers set `is_clip` True and hide the toggle (PS-023).
- Moving a layer (PS-012) does not touch `is_clip`; the compile simply
  re-evaluates which base it clips to.

## Acceptance

- Pixel test: red base at 50% opacity with a clipped blue layer renders
  identically in v2 and v3.
- Clipped layer above an empty folder root compiles without error and is
  effectively unclipped.
- Toggle in the list shows/hides the `clipping` icon.
