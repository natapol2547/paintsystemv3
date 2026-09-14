# PS-021 Solid colour layer parity

Epic C. Size S. Milestone M1.

## v2 behaviour

`ShaderNodeRGB` source (`graph/basic_layers.py:437-440`); the list row shows
a colour swatch bound to the RGB node output (`panels/common.py:540`),
and the settings box shows the colour (`layers_panels.py:231-237`).
Operator `paint_system.new_solid_color_layer` (`layers_operators.py:169`)
takes a colour in its dialog.

## v3 design

`PaintSystemSolidColorLayerNode` exists with `fill_color`. Remaining:

- Row icon: `prop(node, 'fill_color', text='')` swatch in the UIList.
- Operator `paint_system.new_solid_color_layer` with `color` property,
  default from the unified brush colour (v2 behaviour) and dialog.
- Alpha: v2 had no alpha source (opaque). Keep `fill_color` RGBA but
  expose only RGB in the UI; alpha stays 1.0 unless set by script.

## Acceptance

- Creating from the menu inserts above the active layer with the chosen
  colour; the row swatch edits it live and patches the artifact in place.
