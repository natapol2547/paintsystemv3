# PS-041 Group templates and channel templates

Epic E. Size L. Milestone M1.

## v2 behaviour

`paint_system.new_group` (`operators/group_operators.py:73-380`),
`TEMPLATE_ENUM` (`data.py:91`):

- BASIC "Blank Canvas" (IMAGE): one COLOR channel with alpha; optional
  Solid Color and Image layers; group -> Mix Shader <- Transparent BSDF
  -> new Material Output (`create_basic_setup`, `:35`). Forces the scene
  view transform to Standard.
- PAINT_OVER (`paintbrush` icon): EEVEE only. Splices into whatever feeds
  the Material Output: shader socket -> Shader to RGB then the group;
  colour socket -> group directly; sets `Color Alpha` input to 1.0 and
  routes through a new Mix Shader.
- PBR (MATERIAL): finds/creates a Principled, shifts upstream nodes left,
  inserts the group before it, adds channel templates for the checked
  boxes (Color / Metallic / Roughness / Normal; Color + Normal default on).
- NORMAL (NORMALS_VERTEX_FACE): group + Diffuse BSDF + Material Output,
  NORMAL channel template.
- NONE: bare COLOR channel + image layer, group parked to the right.

Material prep: `use_nodes`, optional `BLEND`, optional backface culling +
`show_transparent_back = False`. `invoke` auto-selects PAINT_OVER when
`node_tree_has_complex_setup` (`:60`, anything but a lone Principled)
under EEVEE, leaves EDIT mode, uniquifies the name. `TEMPLATE_ENUM` drops
PAINT_OVER outside EEVEE.

`CHANNEL_TEMPLATE_ENUM` (`data.py:214`): COLOR, METALLIC, ROUGHNESS,
NORMAL. `add_channel` with a template skips the dialog and wires the
channel output to the matching Principled input.

## v3 design

- `ops/templates.py`: a `Template` class per kind with `prepare_material`,
  `create_channels(tree)`, `wire(material, group_node)` and
  `dissolve(material, group_node)` (PS-042). `paint_system.new_group`
  picks the template, creates and initialises the tree as
  `setup_material` does, records
  `tree.template`, and calls the three steps. Existing
  `link_tree_to_material` becomes the NONE/PBR wiring primitive.
- Channel templates: `CHANNEL_TEMPLATES = {COLOR: (type, use_alpha,
  color_space, principled socket), ...}` used by both `new_group` and
  `add_channel`. Wiring to Principled is done by socket name and skips
  linked sockets.
- PAINT_OVER keeps the EEVEE-only rule; the render engine check must
  handle `BLENDER_EEVEE_NEXT` and `BLENDER_EEVEE`.
- The dialog layout (`draw`, `group_operators.py:330-380`) is ported one
  to one: template enum expanded with icons, name, per-template options
  (add solid/image layers, PBR channel checkboxes, blend mode, backface
  culling).
- The initial image layer uses `PSImageCreateMixin` (PS-009) so the
  resolution and UDIM options appear in the same dialog as in v2.

## Acceptance

- Each template on a fresh cube produces the v2 material graph (compare
  node type sets and link endpoints by socket name in a headless test).
- PAINT_OVER on a material with an Emission -> Output chain inserts
  Shader to RGB and renders the painted colour over the emission.
- PBR with all four channels wires Base Color, Metallic, Roughness,
  Normal.
