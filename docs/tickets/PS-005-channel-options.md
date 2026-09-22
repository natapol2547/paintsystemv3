# PS-005 Channel options: alpha socket, colour space, factor range, defaults

Epic A. Size M. Milestone M2 (needed by templates in M1 for `use_alpha`).

## Status

Done: Use Alpha, Color Space, Limit Range, the base value in the channel
list and the Channel Settings section. The v3 design below describes the
code as it is. The vector options are PS-006.

## v2 behaviour

`Channel` (`paintsystem/data.py:2300-2490`) has:

- `type` COLOR/VECTOR/FLOAT (icons from `CHANNEL_TYPE_ENUM`, `data.py:207`).
- `use_alpha`: expose a `<name> Alpha` output on the group.
- `color_space` COLOR/NONCOLOR: bake image colour space and interface
  subtype (`data.py:2339`, `bake_operators.py:232`).
- `use_max_min`, `factor_min`, `factor_max` for FLOAT: interface socket gets
  FACTOR subtype and min/max (`data.py:443-490, 2594`).
- Channel default value is edited on the material group node's unlinked
  input socket (`panels/channels_panels.py:55-63`).
- `alpha_clamp_start` / `alpha_clamp_end` Clamp nodes bracket the alpha
  path (`data.py:1866-1886`).

### v2 UI

All paths are relative to `~/paintsystem`. A line number without a file
refers to the file cited last before it in the same bullet or
paragraph, or else in the lead-in line of the enclosing list; when
neither names a file, it refers to `panels/channels_panels.py`. PS-032
describes the enclosing Channels section, the channel list and the
delete/move operators. This subsection covers the controls for this
ticket's properties and what they do to the node graphs. PS-006 covers
the VECTOR block.

Where it is drawn:

- `draw_channels_settings_panel` (`:156-211`) is the body of
  `box.panel("MAT_PT_ChannelsSettings", default_closed=True)`, header
  label "Channel Settings", no icon (`:120-123`). It is the last item
  in the box that `draw_channels_panel` (`:114-123`) draws: the "Bake
  and Export" menu (legacy UI only), the channel list, then this
  section. That box is the body of `layout.panel("MAT_PT_ChannelsPanel",
  default_closed=True)`, header label "Channels" with the custom
  `channel` icon, inside `MAT_PT_PaintSystemMainPanel` (VIEW_3D, UI,
  category "Paint System", no `bl_options`, poll `ps_object is not
  None`; `panels/main_panels.py:117-122, 139-142, 211-215`). Both
  sections start closed. The main panel turns `use_property_split` on
  for its layout (`panels/main_panels.py:150`).
- The Channels section exists when `poll_channels_panel` passes: the
  material has Paint System data and an active group whose node tree is
  not multi-user (`:108-112`). The panel classes `MAT_PT_ChannelsPanel`
  (`:125-154`) and `MAT_PT_ChannelsSettings` (`:213-229`) are commented
  out of `classes` (`:258-259`) and are dead.
- When the channel has a bake image, the function first draws a
  `row(align=True)` with `prop(channel, "use_bake_image", text="Use
  Baked Image", icon="TEXTURE_DATA")` and
  `paint_system.delete_bake_image` (text "", icon TRASH), whether or
  not the image is in use (`:159-162`; PS-007). While the image is in
  use, the function then draws only `bake_vector_space` for VECTOR and
  returns (`:163-167`). None of the controls below can be edited then.

Controls, in order (`:163-178, 204-211`):

1. `col = layout.column(align=True)` with `use_property_split = True`
   and `use_property_decorate = False` (`:163, 168-169`).
2. `prop(channel, "type", text="Type")` (`:170`): a dropdown of "Color"
   (`color_socket`), "Vector" (`vector_socket`) and "Value"
   (`float_socket`), all custom icons (`paintsystem/data.py:113-117`;
   PNGs in `icons/`, loaded by `custom_icons.py:8-22`).
3. `prop(channel, "color_space", text="Color Space")` (`:171`): "Color"
   or "Non-Color" without icons (`paintsystem/data.py:192-195`), shown
   for every type.
4. `prop(channel, "use_alpha", text="Use Alpha")` (`:172`): a checkbox.
   The property's own name is "Expose Alpha Socket"
   (`paintsystem/data.py:2345-2350`); the panel overrides it.
5. When `use_alpha` is on and the group node is found:
   `prop(socket, "default_value", text="Alpha")` for the group node
   input "<name> Alpha", only when that input is enabled and unlinked
   (`:173-178`). It is a 0-1 slider, because the socket is FACTOR (see
   below), and it edits the material's group node, not the channel.
   The input is looked up by name without a check, so a missing socket
   raises (inferred from code).
6. VECTOR only: the "Default Value" row and the "Vector Transform" box
   (`:179-203`, PS-006).
7. FLOAT only: `col.box()` > `column()` with `use_property_split =
   False`, then `prop(channel, "use_max_min")` (checkbox "Use Max
   Min"). When it is on, `prop(channel, "factor_min")` ("Factor Min")
   and `prop(channel, "factor_max")` ("Factor Max") follow (`:204-211`;
   labels from `paintsystem/data.py:2351-2368`). Nothing checks that
   min is below max.

Channel default value:

- The channel stores no colour or float default. The list row draws
  the group node input named after the channel when it is enabled and
  unlinked: `NodeSocketColor` as a swatch (icon COLOR),
  `NodeSocketFloat` as a number field, and vectors not at all
  (`:56-63`). With `use_max_min` the float field is limited by the
  interface min/max. The widget sits in the right 30 % of a
  `split(factor=0.7)` (`:51`). A channel that uses its baked image
  draws a plain row instead: type icon, name, and a "Baked" label (icon
  TEXTURE_DATA), with no value widget (`:42-50`).
- The Channel property called `default_value` is something else: the
  VECTOR fallback enum None / Normal / World Position / Object Position
  (`paintsystem/data.py:2387-2401`, PS-006).

Add Channel dialog (`operators/channel_operators.py:12-124`):

- `paint_system.add_channel`, bl_label "Add Channel",
  `{'REGISTER', 'UNDO'}`, no poll. It is a `MultiMaterialOperator`, so
  it adds the channel to the active material of the Paint System object
  and of every other selected mesh, through the inherited properties
  `multiple_objects` (default True) and `multiple_materials` (default
  False), which the dialog does not draw (`operators/common.py:32-77`).
- Properties (`:18-77`): `template` (the four templates plus CUSTOM,
  default CUSTOM, SKIP_SAVE; `:10`), `channel_name` "Channel Name"
  (default "New Channel"), `channel_type` (default COLOR),
  `color_space` (default COLOR), `use_alpha` "Expose Alpha Socket"
  (default False), `normalize_input` "Normalize Channel" (False),
  `use_max_min` (False), `factor_min` (0) and `factor_max` (1). The
  last five are SKIP_SAVE. Name, type and colour space are not, so
  Blender reuses their last values, and `invoke` makes the reused name
  unique (`:25-29, 103`).
- `invoke` (`:99-104`): a template executes at once. CUSTOM opens
  `invoke_props_dialog(self)` with no width or title argument, so
  Blender uses its default width (300) and the bl_label as title.
- `draw` (`:106-124`) uses plain `layout.prop` calls without property
  split, in order: "Name", "Type", "Color Space", "Expose Alpha
  Socket", and "Normalize" for VECTOR only. If the typed name collides,
  a box follows with `alert = True`, `alignment = 'CENTER'` and the
  label "Name will be changed to '<unique>'" (icon ERROR). FLOAT then
  shows "Use Max Min" and, when it is on, "Factor Min" and "Factor
  Max". The warning box therefore sits between the vector and the
  float options.
- `process_material` (`:79-97`): CUSTOM calls `Group.create_channel`
  with the dialog values and `vector_space="OBJECT"`; a template calls
  `create_channel_template`.
- `Group.create_channel` (`paintsystem/data.py:2665-2686`) creates a
  node tree, adds and selects the item, makes the name unique, sets
  `type`, `disable_output_transform = False` and the keyword values,
  then assigns the tree and rebuilds the channel and the group.

`MAT_MT_AddChannelMenu` (`:231-252`, described in PS-032): "Custom
Channel" (`channels` icon) opens the dialog above; the template items
run without a dialog. The list's add button opens the menu only while
a template is unused, and otherwise opens the dialog directly
(`:84-88`).

Template values for this ticket's properties
(`paintsystem/data.py:2701-2741`):

- Color: COLOR, `use_alpha=True`, colour space Color. The target is
  the first Principled BSDF, else Diffuse BSDF, and the wiring runs only
  when both it and the group node are found (`:2697-2704`). Alpha is
  turned off when that BSDF has no "Alpha" input (`:2712-2719`). The
  group inputs take the BSDF's links or values through
  `transfer_connection` (`utils/nodes.py:50-72`), so "Color Alpha"
  starts at the BSDF's Alpha value.
- Metallic and Roughness: FLOAT, `use_alpha=False`,
  `use_max_min=True` with the property range 0-1, Non-Color.
- Normal: VECTOR, `use_alpha=False`, Non-Color (PS-006).
- The `use_alpha` defaults disagree: the property defaults to True for
  every type (`paintsystem/data.py:2348`), the dialog to False.

Behaviour the UI implies:

- Update callbacks (`paintsystem/data.py:2329-2368`): `type` rebuilds
  the channel and the group. `use_alpha`, `use_max_min`, `factor_min`
  and `factor_max` call `update_active_group` (`:291-295`), which
  rebuilds only the group. `color_space` has no callback.
- Group interface (`paintsystem/data.py:2589-2599`): per channel, in
  list order, a socket "<name>" (COLOR `NodeSocketColor`, VECTOR
  `NodeSocketVector`, FLOAT `NodeSocketFloat`), then "<name> Alpha"
  (`NodeSocketFloat`, FACTOR, 0-1) only when `use_alpha` is on. The
  same list is applied to the outputs and to the inputs, so both sides
  gain or lose the alpha socket together.
- `ensure_sockets` (`paintsystem/data.py:448-499`) diffs the sockets
  by name (`detect_change`, `:416-437`). Toggling `use_alpha` adds or
  removes only the alpha socket; the other sockets keep their identity
  and their links. A removed alpha socket loses its links, and turning
  the option back on creates a new, unlinked socket. A type change
  edits `socket_type` in place (`:498-499`).
- Subtype (`paintsystem/data.py:465-468, 485-493`): with `use_max_min`,
  FACTOR with min/max from `factor_min`/`factor_max`; otherwise NONE
  with min/max reset to -1e39/1e39. The rule runs on every socket that
  has a subtype.
- `hide_value` is `default_value != "NONE"` for the channel socket and
  False for the alpha socket (`paintsystem/data.py:495-496,
  2594-2596`). Only VECTOR channels show that enum, but a type change
  does not reset it, so a channel switched away from VECTOR keeps a
  hidden socket value while the list row still draws the value
  (`update_type`, `:2329-2331`; inferred from code).
- v2 never writes an interface `default_value`. New sockets keep
  Blender's defaults: float 0.0, vector (0, 0, 0), colour (0, 0, 0, 1)
  (checked in Blender 5.2.1). A freshly exposed "<name> Alpha" input is
  therefore 0 and the stack starts from a transparent base. The layer
  warning "Input Alpha of <name> channel is 0. Blending may not work."
  exists for this case. It fires for the last layer of the stack when
  it has no layer below, `use_alpha` is on, the group node's "<name>"
  or "<name> Alpha" input is linked, and the alpha input's stored value
  is 0. It reads the stored value even when the alpha input itself is
  linked (`paintsystem/data.py:1452-1472`).
- Group graph (`paintsystem/data.py:2601-2618`), rebuilt from scratch
  (`clear=True`): one `ShaderNodeGroup` per channel with the channel
  tree and its "Alpha" input set to 1. Group Input "<name>" > channel
  Color; with `use_alpha`, Group Input "<name> Alpha" > channel Alpha.
  Channel Color > Group Output "<name>"; with `use_alpha`, channel
  Alpha > Group Output "<name> Alpha".
- With `use_alpha` off, neither side of the group has an alpha socket.
  The channel node's Alpha input stays unlinked at 1, so the layers
  composite over an opaque base made of the "<name>" input, and the
  channel's Alpha output is left unconnected. The channel graph does
  not change, since `use_alpha` rebuilds only the group.
- Nothing in the group graph or the channel graph multiplies colour by
  alpha. The per-layer mix groups `.PS Post Mix` and `.PS Porter-Duff
  Over` (`paintsystem/graph/common.py:116-120`) multiply internally
  and divide by the resulting alpha before output (`library2.blend`,
  inspected in Blender 5.2.1). The channel's Color output is therefore
  straight colour. A pixel where both alphas are 0 comes out black,
  because Vector Math Divide returns 0 for a zero divisor.
- Channel tree interface (`paintsystem/data.py:1828-1832`): Color
  (`NodeSocketColor`) and Alpha (`NodeSocketFloat`) outputs and
  inputs, created once, for every channel type. FLOAT and VECTOR
  channels rely on Blender's implicit socket conversion at the group
  links (`:2611, 2615`).
- Alpha clamps: `alpha_clamp_end`, a hidden Clamp, feeds Group Output
  Alpha (`paintsystem/data.py:1867-1869`), and `alpha_clamp_start`
  takes Group Input Alpha into the bottom of the stack (`:2001-2003`).
  Both keep the Clamp defaults (Min Max, 0-1). With the baked image in
  use, the image Alpha goes straight to Group Output Alpha and the end
  clamp is left unlinked (`:1891-1895`). The range cited above
  (`:1866-1886`) covers the end clamp and the vector output transform,
  not the start clamp.
- `color_space` readers: Bake Channel and Bake All Channels set the
  bake image to "Non-Color" or "sRGB" (`operators/bake_operators.py:262,
  334`). Baking to a new layer forces "sRGB" whatever the channel says
  (`:232`, the line cited above). Isolate channel always switches the
  view transform to "Standard" (`paintsystem/data.py:2543`). Nothing
  sets an interface subtype from `color_space`, so the "interface
  subtype" in the paragraph above is wrong.
- Baking forces the alpha socket on: `Channel.bake` sets `use_alpha =
  True` for the bake and restores it afterwards
  (`paintsystem/data.py:2152-2154, 2263, 2281`). When the option was
  off and the channel input is linked, it also sets the new "<name>
  Alpha" input to 1 (`:2206-2208`). Both hang on the `force_alpha`
  argument, which defaults to True (`:2115`) and which no caller passes
  as False (`operators/bake_operators.py:238, 266-276, 337`, and the
  transfer, convert and merge bakes at `:591, 662, 792, 913`).

## v3 design

Properties of `PaintSystemChannel` (`props/channel.py`):

- `use_alpha` "Use Alpha". Its RNA default is True, so channels saved
  before the option existed keep their alpha sockets. `create_channel`
  sets it from the type through `channel_defaults`: on for COLOR, off for
  FLOAT and VECTOR. A later type change leaves it alone, because the user
  may have set it.
- `color_space` Color / Non-Color, set from the type the same way. An
  image layer added to a Non-Color channel gets a Non-Color image
  (`image_colorspace`). PS-061 reads it for the preview's view
  transform, and PS-007 should read it for bake images. Changing it leaves existing images
  alone: giving painted bytes another colour space reinterprets them, it
  does not convert them.
- `use_range` "Limit Range", `range_min` "Min" and `range_max` "Max", for
  FLOAT channels. They replace v2's `use_max_min`, `factor_min` and
  `factor_max`, whose labels did not say what is limited. The interface
  input gets subtype FACTOR and the range, so the material's input is a
  slider; off, it is NONE without limits. Min and max are sorted, since
  nothing stops a user from setting min above max. The range is a soft
  limit: it bounds the slider's drag, while a typed, scripted or linked
  value outside it is kept (checked in 4.2, 5.2 and 5.3). Blender clamps
  no value when the range changes, and the shader never clamps. A
  subtype change keeps the socket's identifier, value and links.
- No `default_value`. The value a stack starts from is the material group
  node's input, as in v2. A copy on the channel would drift from it, and
  a tree shared by several materials can start from a different value in
  each.

Compiled interface:

- The Paint System tree has one RGBA socket per channel (PS-098), so
  `use_alpha` touches no socket there. It changes the compiled interface
  (`interface_socket_specs`) and the places that cross it:
  `CompileContext.set_channel_output`, `link_channel` and
  `link_flattened`. With it off, neither side of the compiled group has
  a `<name> Alpha` socket and the Group Input gives alpha 1, so the stack
  starts from an opaque base, as in v2. Blend modes such as Multiply then
  act on the base. The Group Input's `hash_parts` holds each channel's
  interface specs, keyed by socket identifier, so a layer cache above it
  goes stale when these options change, but not on a rename or a move.
- At the Group Output, a channel without alpha is flattened onto its
  input: a Mix node lays the colour over the compiled group's own
  `<name>` input, by the alpha (`link_flattened`). An unlinked channel is
  an empty stack, so the input comes out as it is. v2 drops the alpha
  instead. Normal layers keep an opaque base opaque, so the mix changes
  nothing there. A filter layer does not keep it opaque: it replaces the
  stack with pixels filtered without the base, so without the mix a
  blurred stroke would show its colour at full strength out to the last
  pixel of its soft edge. Multiplying by the alpha, which is what
  converting straight colour to premultiplied would do, flattens onto
  black instead, which is right only when the input is black.
- A group layer follows its child's channels. A child channel without
  alpha gives the parent alpha 1 (a constant emitted on the group layer
  node), and no alpha is passed into it.
- The `<name> Alpha` input is FACTOR 0-1 whenever it exists.
- Toggling `use_alpha` removes or adds only the alpha sockets. The
  material's other links survive; a link to a removed alpha socket is
  lost, and turning the option back on makes new, unlinked sockets.
- A type change retypes the compiled sockets in place
  (`NodeTreeBuilder._sync_interface_sockets`), so they keep their
  identifiers and the material's links, as in v2. Replacing them would
  drop the links.
- No alpha clamps. v2 clamps the alpha where it enters and leaves the
  stack. Here the blend groups keep alpha in 0-1 when their inputs are,
  and the material's alpha input is a 0-1 slider. The slider is a soft
  limit, so a typed or linked value outside 0-1 is the user's choice.

UI (`panels/main_panels.py`):

- Each channel list row shows the channel's base value, the material
  group node's unlinked input, found through `context.material_input`:
  a swatch for COLOR, a number field for FLOAT, nothing for VECTOR, and
  nothing when the input is linked or the tree is not the material's own
  (a nested tree).
- A "Channel Settings" section below the list, closed at first, shows
  Type, Color Space, Use Alpha, "Base Alpha" (the material's unlinked
  `<name> Alpha` input) and, for FLOAT, Limit Range with Min and Max.
- The Add Channel dialog keeps Name and Type. The options come from the
  type and are changed in Channel Settings.

Known gaps:

- A layer cache bakes the Group Input with the interface defaults, not the
  material's input, and the cached layer then stands in for everything
  below it. So a cache over a channel without alpha holds an opaque base
  of the interface default: black, or for FLOAT 0 clamped into the Limit
  Range. This predates PS-005; with alpha on, the cache holds a
  transparent base instead, and the material's alpha input is lost below
  it all the same.
- A filter layer's GPU composite (`filters/composite.py`) starts from a
  transparent backdrop, since it cannot know the material's input. In a
  channel without alpha, the flatten lays the filtered result over the
  input, which matches the shader for Mix layers. A layer with a blend
  mode such as Multiply directly on the base acts on the input only in
  the shader, so the filter sees a different picture. Filter layers only
  run on COLOR channels, so this needs Use Alpha turned off by hand.

## Acceptance

- `tests/test_channels.py`: new channels take their options from the
  type; image layers get the channel's colour space; toggling `use_alpha`
  removes and adds only the alpha sockets and keeps the material's other
  links; a type change keeps the material's links; pixels show the opaque
  base under a Multiply layer, the flattening, and an empty channel giving
  its input; a layer cache goes stale when Use Alpha or the range
  changes, and not on a rename; Limit Range gives a FACTOR socket with the
  sorted range and back without identifier churn or a lost link.
- `tests/test_compile.py`: a child channel without alpha gives the parent
  a constant alpha, and a parent channel without alpha goes through the
  flatten mix.
- `tests/test_main_panel.py`: the list row and Channel Settings draw the
  channel's options and the material's unlinked inputs; a VECTOR row and a
  nested tree draw no value; Base Alpha stays hidden without alpha even
  when another channel is called `<name> Alpha`; the open channels
  section draws Channel Settings.
