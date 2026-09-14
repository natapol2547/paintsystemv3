# PS-026 Texture layer redesign

Epic C. Size M. Milestone M2.

## v2 behaviour

`texture_type` (`data.py:138`): Brick, Checker, Gradient, Magic, Noise,
Voronoi, Wave, White Noise (`get_texture_identifier`,
`graph/basic_layers.py:387-398, 562-572`). The raw texture node is the
source; `color_output_name` / `alpha_output_name` enumerate its live
sockets (`data.py:1041-1074`); `update_texture_type` resets them
(`data.py:1189`). Settings panel draws the live node
(`texture_node_settings_panel`, `layers_panels.py:353-366`). Coordinates
via `create_coord_graph`. Problems: socket enums were rebuilt from the
live node and broke on type change; texture parameters were lost on
rebuild; no way to bake a procedural texture down.

## v3 design

- `PaintSystemTextureLayerNode(CoordMixin, PaintSystemLayerNode)` with
  `texture_type`, `color_output` and `alpha_output` enums generated from a
  static table per type (Color/Fac/Distance/Position for Voronoi, etc.),
  not from a live node.
- The texture node is an artifact-owned parameter node (PS-003) so all its
  enum properties and socket values are edited directly with
  `template_node_inputs` and the node's `draw_buttons`. The Vector input
  is IR-linked from `emit_coords`.
- Changing `texture_type` recreates the parameter node and resets the
  output enums to the first valid entries.
- "Bake to Image" button in the settings panel uses the node cache
  (`paint_system.bake_cache`) and a "Convert to Image Layer" entry uses
  PS-018. Procedural layers default to `cache_enabled = False`, but the
  cache status icon in the row makes baking discoverable, which is the
  hybrid workflow this rewrite is for.
- Menu "Texture" submenu generated from the type table; row icon TEXTURE.

## Acceptance

- Each texture type compiles and renders; switching types does not leave
  orphan nodes in the artifact.
- Voronoi `Distance` selected as alpha produces a mask.
- Baking a noise texture at 512 and enabling the cache removes the
  texture node from the artifact and keeps the look.
