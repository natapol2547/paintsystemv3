# PS-025 Random colour layer

Epic C. Size S. Milestone M2.

## v2 behaviour

`graph/basic_layers.py:519-543`: Object Info (Material Index + Random) ->
Math ADD -> Math ADD (seed) -> White Noise 1D -> remap to -1..1 -> Separate
XYZ -> three Multiply-Add (hue/sat/value ranges) -> Hue/Saturation/Value on
a base colour. Settings box exposes seed, base colour, H/S/V ranges by
poking the live nodes (`layers_panels.py:239-261`). Menu "Random Color"
(icon SEQ_HISTOGRAM).

### v2 UI

Host: `draw_layer_settings` (`panels/layers_panels.py:171-492`) inside
the "Layer Settings" `layout.panel("layer_settings_panel")`, open by
default, at the bottom of `MAT_PT_Layers` (VIEW_3D, UI, category "Paint
System", no `bl_parent_id`, no `bl_options`;
`panels/layers_panels.py:494-507, 671-674`). Disabled while
`lock_layer` is on (`:174`). For this type the only content is the
per-type box followed by the "Actions" sub-panel (`:462-463`); there is
no node sub-panel and no Transform sub-panel (`:379`). PS-022 describes
the rest of the host panel, including the clip/lock/blend/opacity row
that the default UI draws above the layer list (`:604-610`).

- Per-type box (`panels/layers_panels.py:239-261`): a new `box()` in the
  default UI (`panels/common.py:464-480`) > `column()`. Drawn only when
  the nodes `add_2`, `hue_multiply_add`, `saturation_multiply_add`,
  `value_multiply_add` and `hue_saturation_value` are all found;
  otherwise the box stays empty.
  1. `label(text="Random Settings:", icon='SHADERFX')`.
  2. `prop(add_2.inputs[1], "default_value", text="Random Seed")`, full
     width (no property split). It is a float field, because the seed
     is the second input of a Math ADD node (`graph/basic_layers.py:
     524`).
  3. Sub-`column()` with `use_property_split = True`,
     `use_property_decorate = False`:
     `hue_saturation_value.inputs['Color']` "Base Color";
     `hue_multiply_add.inputs[1]` "Hue";
     `saturation_multiply_add.inputs[1]` "Saturation";
     `value_multiply_add.inputs[1]` "Value".
  The Multiply-Add offsets (`inputs[2]`) and the Hue/Saturation node's
  Fac are not drawn.
- List row icon `SEQ_HISTOGRAM` (`panels/common.py:566-567`); the same
  icon is used in the Shader Editor "Paint System" panel's layer rows
  (`panels/extras_panels.py:459-477`).
- Menu: in `MAT_MT_AddLayerMenu`, after "Attribute Color",
  `operator("paint_system.new_random_color_layer", text="Random Color",
  icon='SEQ_HISTOGRAM')` (`panels/layers_panels.py:880-881`). The last
  entry, "Custom Layer" (icon NODETREE), follows it
  (`panels/layers_panels.py:883-884`).
- `PAINTSYSTEM_OT_NewRandomColor`
  (`operators/layers_operators.py:380-402`): bl_label "New Random Color
  Layer", `{'REGISTER', 'UNDO'}`, poll needs an active channel. Prop
  `layer_name` (default "Random Color"), used as the layer name, plus
  `multiple_objects` (True) and `multiple_materials` (False) from
  `MultiMaterialOperator` (`operators/common.py:32-77`), so it also adds
  the layer to the active material of every selected mesh that has a
  Paint System channel (see PS-022). No `invoke`, no dialog; the redo
  panel shows `layer_name` and the two multi-object options.

Behaviour implied by the UI:

- Node defaults on first build (`graph/basic_layers.py:519-543`):
  `add_2` input 1 (seed) 0; White Noise `noise_dimensions` 1D; Vector
  Math MULTIPLY_ADD with (2, 2, 2) and (-1, -1, -1), so each noise
  channel is remapped from 0..1 to -1..1; Multiply-Add pairs
  (multiplier, offset): hue (1, 0.5), saturation (1, 1), value (1, 1);
  Hue/Saturation/Value Color (0.5, 0.25, 0.25, 1). Resulting inputs:
  Hue = X * hue + 0.5, Saturation = Y * saturation + 1, Value = Z *
  value + 1, so a multiplier of 0 gives the unchanged base colour.
- W of the noise is Object Info Random + Object Info Material Index +
  seed. There is no toggle for either term. Material Index is Blender's
  material pass index, not the slot index.
- Output: only `hue_saturation_value.Color` is wired, to `mix_rgb` B and
  `post_mix` Over Color. No alpha socket is given, so `pre_mix` Over
  Alpha stays at 1.0 and the layer is opaque
  (`graph/basic_layers.py:520`; `graph/common.py:123-131`).
- Values edited in the box are node input defaults; the builder
  captures and re-applies them on each rebuild
  (`graph/nodetree_builder.py:76-105, 277, 957-980, 1040-1091`), and
  paste copies the node tree (`paintsystem/data.py:1492-1495`). Nothing
  is stored on `Layer`.
- The Layer Menu offers "Convert to Image Layer" for this type
  (`panels/layers_panels.py:733-739`); that operator is covered by
  PS-018.

## v3 design

- `PaintSystemRandomColorLayerNode` with `seed`, `base_color`, `hue_range`,
  `saturation_range`, `value_range`, `per_object` (Random) / `per_material`
  (Material Index) toggles. All IR values; no parameter nodes needed.
- `emit_source` emits the v2 chain with the props as unlinked inputs.

## Acceptance

- Two cubes sharing the material render different colours; changing
  `seed` patches one socket value.
