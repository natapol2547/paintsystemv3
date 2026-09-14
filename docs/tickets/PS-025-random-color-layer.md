# PS-025 Random colour layer

Epic C. Size S. Milestone M2.

## v2 behaviour

`graph/basic_layers.py:519-543`: Object Info (Material Index + Random) ->
Math ADD -> Math ADD (seed) -> White Noise 1D -> remap to -1..1 -> Separate
XYZ -> three Multiply-Add (hue/sat/value ranges) -> Hue/Saturation/Value on
a base colour. Settings box exposes seed, base colour, H/S/V ranges by
poking the live nodes (`layers_panels.py:239-261`). Menu "Random Color"
(icon SEQ_HISTOGRAM).

## v3 design

- `PaintSystemRandomColorLayerNode` with `seed`, `base_color`, `hue_range`,
  `saturation_range`, `value_range`, `per_object` (Random) / `per_material`
  (Material Index) toggles. All IR values; no parameter nodes needed.
- `emit_source` emits the v2 chain with the props as unlinked inputs.

## Acceptance

- Two cubes sharing the material render different colours; changing
  `seed` patches one socket value.
