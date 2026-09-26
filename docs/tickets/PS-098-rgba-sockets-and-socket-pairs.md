# PS-098 RGBA sockets and socket pairs

Epic B. Size L. Milestone M2. The layer socket model that linked layers
(PS-016) and the clipboard (PS-017) build on.

## Status

- Slice 1, RGBA-only sockets: done. See "Slice 1" below.
- Slice 2, socket pairs and the virtual input: built, then backed out in
  e199ca9. Blender's own node tools assume a node passes one input
  through: Delete with Reconnect, Detach and mute give every output the
  first linked input, so on a layer in two channels they wired the
  second channel to the first channel's stack. A Python node cannot
  choose its internal links. One node is one layer again. The bake's UV
  map fix found during the slice was kept (36852c5). "Slice 2" below
  records the design as it was built.
- Slice 3, Paste and Paste Linked: replaced by linked layers as separate
  nodes with linked settings (PS-016, a8749b5 and 05754ee) and the
  clipboard (PS-017, a8749b5).
- Slice 4, a Separate Color node in the Paint System tree: done. See
  "Slice 4" below.

## Decisions

Made with the user on 2026-09-23.

- Every link in a Paint System tree carries one RGBA value, as in the
  compositor. There are no Alpha sockets on layers, folders, group layers
  or the Group Input and Output. The compiled shader group still has
  `<channel>` and `<channel> Alpha` sockets, because materials need them
  apart. PS-005 later made the alpha socket depend on `use_alpha`.
- A Separate Color node splits an RGBA link into floats inside the tree.
- Every layer type can have more than one input/output pair. Each row of a
  list in the node editor sidebar (like the channel list) is one pair.
  Pairs are added and removed there, down to one. Only RGBA pairs exist.
- A greyed-out virtual input sits below the pairs. Linking into it turns
  it into a new pair. There is no virtual output. Unlinking a pair's
  input does not remove the pair; its output stays too.
- `Mask` is always the last input.
- Sockets are moved, never removed and made again, when the order
  changes. `inputs.move` keeps links and identifiers; remove plus new
  drops the links (probed on 4.2, 5.2 and 5.3).
- A linked layer is one node in several stacks, within one tree only.
  Paste copies a node with its settings and data. Paste Linked adds a pair
  to the same node and wires the target stack through that pair. All
  settings are shared by every pair; filter results and caches are built
  per pair. Superseded on 2026-09-26 with slice 2 (see "Status"): a
  linked layer is now a separate node with linked settings (PS-016).
- No migration. v3 is unreleased, so a tree saved before slice 1 keeps
  its stale Alpha sockets and links; make a fresh tree instead.

During slice 2 the user also chose:

- Everything a pair builds is per pair: its cache, its filter result, the
  result's freshness and the automatic refresh's error.
- Remove Layer removes only the pair in the active channel's stack. The
  layer is deleted with its last pair, and the dialog says how many other
  stacks it stays in. The code review refined "last pair": a pair that
  feeds nothing, such as a new one from Add Pair, does not keep the layer
  (see "Adding and removing pairs").
- The editing operators never put a layer in one channel twice. A repeat
  wired by hand still compiles, and the layer list shows its first row.

## Slice 1: RGBA-only sockets

### Socket layout

- Layer (`nodes/layers/base_layer_node.py`): inputs `Color` (RGBA,
  default 0,0,0,0) and `Mask` (float, default 1); one `Color` output.
- Folder: inputs `Color`, `Content Color`, `Mask`, in that order. The
  folder moves `Mask` after adding `Content Color`.
- Group layer: one RGBA input and one RGBA output per channel of its tree.
- Group Input and Output: one RGBA socket per channel
  (`channel_socket_specs`). A channel's type no longer changes any socket
  in the Paint System tree, so a type change keeps its links.
- The compiled interface comes from `interface_socket_specs`: `<channel>`
  typed by the channel, then `<channel> Alpha` as a 0-1 factor when the
  channel has `use_alpha` (PS-005).

### Compiler

- A Paint System output is recorded as a `(colour, alpha)` pair of IR
  references, keyed by `(node uuid, socket identifier)`. A constant half
  becomes a Value or RGB node with the role `const:<identifier>:<half>`.
- Consumers read that pair through `CompileContext.rgba_input` (the
  unlinked default's fourth value is the alpha), `connect_input` (one
  value, used for `Mask`) and `upstream` (the pair as it is, which the
  Group Output and group layer emitters link by the `<channel>` and
  `<channel> Alpha` names; `interface_socket_specs` declares them). The
  compiled group's inputs are read through `channel_base`. At the Group
  Output, a channel without alpha goes through `flattened` instead
  (PS-005).
- A colour linked into `Mask` is converted by the shader's implicit
  conversion, which is luminance. PS-015 decides whether masks should
  read luminance, the red channel or alpha.
- A child channel that changes type keeps the parent's links to it,
  because the builder retypes the compiled socket in place (PS-005, covered
  by `tests/test_compile.py`). Slice 1 first put the child interface in
  `IR.meta` to rebuild the parent instead.

### Stack model

- One invariant is left: a layer's `Color` output feeds at most one slot.
  The alpha-mirroring invariant and its repair are gone.
- A slot is any layer input except `Mask`, or an input of the Group
  Output or a group layer. Inputs of reroutes and other nodes are not
  slots, which matches the stack walk.
- `detach` keeps links into masks. `consumer_input` skips masks and
  reroutes. Before this slice the first link on the output was taken,
  even one into a `Mask`, so moving a layer that masked another rewired
  the stack into the mask.
- Because a mask link now survives a move, a move can close a loop: a
  layer moved above the layer it masks would read that layer's result
  while feeding its mask. `move` checks for a loop through the moved
  layer after placing it, and puts the layer back when it finds a new one
  (found by the code review; the old `detach` cut the mask link instead).
  Slice 2 checks pair by pair.
- Apart from the nodes that create them, `below_input`, `stack_output`,
  `content_input` and `channel_input` in `nodetree/stack_ops.py` are the
  only code that names the sockets a stack runs through; `Mask` is read
  by name. Slice 2 gives them a pair argument.

### Consequences

- Every compiled artifact and every subtree hash changes once, so an
  existing file rebuilds each artifact once and shows cached layers and
  filter layers as out of date once. `FILTER_VERSION` and
  `LIBRARY_VERSION` are not bumped for it.
- The bake branch of `build_ir` reads `ctx.output(stack_output(target))`,
  which only works for a layer. Only layers are baked today. PS-057's
  known gaps records what the Cycles fallback needs instead.

## Slice 2: socket pairs

Backed out in e199ca9 (see "Status"). This section and slice 2's
acceptance below record the design as it was built; the code no longer
has pairs.

### Socket layout

- A layer's inputs are its pair inputs, then the virtual input, then the
  inputs every pair shares: `[Color, Color 2, ..., virtual, Mask]`, and on
  a folder `[Color, Color 2, ..., virtual, Content Color, Mask]`. The plan
  put `Content Color` first on a folder. It moved below the virtual input
  so that the first pair input is always the first input, and the virtual
  input sits right below the pairs, as the design asks.
- Pairs are numbered by position: the n-th pair input goes with the n-th
  output. Both sockets of a pair are named by `pair_name`: `Color`,
  `Color 2` and so on. Removing a pair names the pairs after it again but
  keeps their identifiers.
- The virtual input is a `NodeSocketVirtual` with the identifier
  `__extend__`, as on Blender's own nodes. It has no `default_value`, so
  `is_slot`, `channel_sockets` and `rgba_input` never see it.
- Each pair compiles as its own blend, from its input to its output, with
  the node's shared settings. `pair_role` adds `@<output identifier>` to
  the roles of every pair but the one with the `Color` identifier. So a
  layer with one pair compiles as in slice 1, and removing a pair keeps
  the other pairs' compiled nodes.

### Adding and removing pairs

- `PaintSystemLayerNode.update` turns a link into the virtual input into
  a new pair: it removes the link and links the same source into the
  input `add_pair` returns. Found while probing: `Node.update` runs once
  per node for a `links.new` from Python, with the new link already in
  place, and links made there are kept. Links made in `NodeTree.update`
  are dropped. The handler is idempotent anyway, because nothing is left
  linked into the virtual input.
- Unlinking a pair's input keeps the pair and its output.
- The node editor sidebar has a Pairs subpanel under Layers
  (`PAINTSYSTEM_PT_layer_pairs`). It lists the active layer's pairs, each
  with the channel its stack reaches, or "In no stack". Add Pair adds a
  pair in no stack. Remove Pair takes the selected pair out of its stack,
  closes the gap and removes the pair with its filter result
  (`stack_ops.remove_pair`). It never deletes the layer, and the last
  pair cannot be removed. The list's selection, `active_pair_index`, is
  left out of the compile hash.
- Removing a pair's input leaves Blender with dangling internal links, the
  pass-through a muted node uses, when several outputs took theirs from
  that input. The next depsgraph update then crashes on 4.5 and later.
  `remove_pair` unlinks the input and moves it after the other pair
  inputs first, so Blender chooses the links again without it
  (`tests/test_pairs.py` crashed without this).
- Remove Layer removes only the pair in the active channel's stack, and
  needs the active layer to be in that stack. The layer is deleted when
  none of its other pairs feeds anything: another stack, a mask or any
  other node. A folder's content goes only when the folder itself is
  deleted, on the same rule, and links into nodes deleted in the same
  removal do not count. A link into a pair the removal takes out counts
  for the slot that pair fed, since closing the gap moves the value on
  there. A muted link is not moved on, so it does not count.
  `stack_ops.removal` works this out first, as a fixed point: it assumes
  everything it reaches goes, keeps each layer that still feeds something
  staying, and repeats until nothing changes. So the stack order does not
  change the result, and layers that only feed each other or their own
  folder go together. The walk shares one visited set, as `stack` does,
  so a folder wired into its own content is opened once.
  `stack_ops.remove` carries out exactly that plan. A deleted layer
  detaches the pairs the plan takes out before anything else, so the gaps
  close the way the plan assumed. The first row below the removed ones
  that is still there becomes active, or else the nearest row above. The
  dialog says which other channels
  the layer stays in, or that it stays in the tree because a pair still
  feeds something. A deleted filter layer takes the results of all its
  pairs with it, linked or not.
- The layer list shows a placeholder `LINKED` badge on a layer with more
  than one pair.

### Per-pair state

- Each pair has a state in the layer's `pairs` collection, at the pair's
  position: its cache (`PairCache`) and, on a filter layer, its result,
  freshness and refresh error (`PaintSystemFilterPair`). Settings stay on
  the layer and are shared, Auto Refresh and the cache's UV map included.
  A bake that changes the UV map sends the other pairs' caches back to
  baking, since they were baked with the old one. The bake dialog starts
  from the layer's UV map. `bpy.ops.object.bake` writes through the
  active UV map, whatever the target node's Vector input reads, so the
  bake makes the cache's UV map active, or the active render one for '',
  and restores the user's after. Its `uv_layer` option would name the map
  instead, but that string holds 63 bytes, fewer than a UV map name can
  have.
- Bake Cache, Update Filter and Clear Result act on the pair in the
  active channel's stack (`pair_in_stack`).
- An automatic filter refresh remembers the position of the pair it
  builds, and drops the build when that pair moves or goes, since the
  position would then name another pair.
- In-memory tables, such as the refresh job and its counts, key a pair
  by `pair_key`: the node's uuid and the output's identifier. Blender
  gives a new socket the identifier of one removed earlier, so
  `layer_job.forget` drops a removed pair's job and counts before its
  sockets go.
- `path_from_id` cannot make a path to a property group stored on a node,
  so `PaintSystemFilterPair.node` finds its node by searching the tree.

### Stack model

- Every stack operation takes the pair of the stack it edits, as a
  `Position(node, pair)`, and touches only that pair's link. `detach`,
  `attach`, `move`, `layer_above` and `layer_below` all take a pair.
- Blender's cycle check is per node, so a layer can sit in one channel
  twice without a cycle, for example inside a linked folder and again
  below it. `move` refuses to make that (`stack_ops.repeats`). Paste
  Linked must refuse it too.
- The same per-node check flags two layers stacked in opposite orders in
  two channels, and Blender draws one of their links red. Each pair
  compiles on its own, so that is allowed. `move` checks for loops pair by
  pair (`loops_back`), and refuses a move only when the moved pair would
  read its own output, as through a mask link. It returns why it refused,
  `'LOOP'` or `'REPEAT'`, and the Move operators report that.

### File compatibility

- No migration. `complete_pairs` gives every layer a virtual input and a
  state per pair. It runs when a file is read, in `normalize_tree` before
  every compile, and on every flush for all trees, which covers a tree
  appended from an older file. A tree linked from an older file is
  completed in memory only, so that happens again in each session.
  A layer saved before pairs existed bakes its cache and builds its
  filter result again.

### Known gaps

- Delete with Reconnect (Ctrl+X) and Detach Links in the node editor use
  Blender's internal links, which map every pair output to the first
  linked pair input. On a layer with several pairs they wire every stack
  to the stack below pair 0. Remove Layer and Remove Pair are correct.
  Blender has no Python API for a node's internal links, so a fix would
  be a Paint System operator on Ctrl+X in the addon's own node editor
  keymap.
- A layer node's body in the node editor finds the pair in the active
  channel by walking that channel's stack, about 0.4 ms per redraw for a
  layer with several pairs in a 40-layer stack. That is left as it is.

## Slice 4: Separate Color

`PaintSystemSeparateColorNode` (`nodes/converter/separate_color_node.py`),
under Converter in the node editor's Add menu.

- One RGBA input, `Color`, with a value field (0.8 grey, as on Blender's
  node). Four float outputs, `Red`, `Green`, `Blue` and `Alpha`.
- `mode` is RGB, HSV or HSL, as on the shader's Separate Color. The first
  three outputs are renamed to match (Hue, Saturation, Value or
  Lightness). Their identifiers stay `Red`, `Green` and `Blue`, so a mode
  change keeps the links. The mode is hashed, so a cache above the node
  goes stale when it changes.
- It emits a shader Separate Color. Each output is recorded with an alpha
  of 1, from one Value node, which is how Blender converts a float to a
  colour, as in the compositor. `Alpha` gives the input's alpha as its
  value.
- It is not a layer, so its input is not a slot. A layer that feeds it
  stays in its stack, and `detach` keeps the link, as it keeps a mask
  link. `move` already follows every input when it checks for a loop, so
  it refuses a move that would loop through the node, as it refuses a
  mask loop. Examples are a layer moved below the layer its mask reads
  through the node, and a layer moved above a folder whose content starts
  from the node reading that layer. The Move operators report that the layer would read its
  own result.
- An output can feed a Group Output socket, a layer's `Mask`, or the
  bottom of a stack, where it is that stack's backdrop, as the Group
  Input is. The stack edits keep it at the bottom: a new bottom layer
  takes it as the stack below, and removing the last layer links it to
  the output again.
- With it, a mask can read one component of a colour, such as its red,
  instead of the luminance the shader's implicit conversion gives.

### Known gaps

- A filter layer over a stack that starts from the node cannot build.
  The GPU composite does not draw the node and refuses it by name
  (`filters.composite._plan_chain`), and the Cycles bake path it would
  fall back to is not built (PS-057, Path B). Drawing it would take a
  conversion pass per mode after compositing the stack that feeds it.
- A filter layer's own `Mask` can come from the node, because a build
  reads only what feeds the filter's `Color` input. A layer below a
  filter layer with a mask from the node is refused, as any linked mask
  below one is (`filters.composite._plan_layer`).
- The layer list shows only layers, so a stack starting from the node
  looks like any other in the panel.

## Acceptance

- Slice 1, done:
  - Every stack item feeds exactly one slot through its one output
    (`check_one_link` in `tests/test_stack.py` and `tests/test_layers.py`).
  - One hand-made link carries colour and alpha into the output, and an
    unlinked channel is transparent (`tests/test_stack.py`).
  - A layer that masks another stays linked to the mask through inserts
    and moves, a reroute into a mask is not a slot, and the compiled
    blend reads the mask (`tests/test_stack.py`).
  - A group layer composites its tree over the colour and alpha it takes
    in (`tests/test_stack.py`).
  - A channel type change keeps its links, and the compiled interface
    follows the type with the alpha beside it (`tests/test_channels.py`).
  - A float child channel, which has no alpha since PS-005, feeds the
    parent's channel, and a constant feeds the parent's alpha. Retyping
    the child keeps the parent's compiled links (`tests/test_compile.py`).
- Slice 2, met when it was built and backed out since, with
  `tests/test_pairs.py`:
  - Linking into the virtual input adds a pair above it; the link lands
    on the new pair, the virtual input is free again and `Mask` stays
    last (`tests/test_pairs.py`).
  - Each pair blends its own stack with the shared settings, and is
    cached and filtered on its own (`tests/test_pairs.py`).
  - Unlinking a pair's input keeps the pair and its output
    (`tests/test_pairs.py`).
  - Removing a pair, from the sidebar list or with Remove Layer, keeps
    the other pairs' links and compiled nodes; the last pair cannot be
    removed from the list (`tests/test_pairs.py`).
  - Remove Layer deletes a layer that no other pair keeps, and a folder's
    content that nothing else reads (`tests/test_pairs.py`).
  - Removing a pair of a tree Blender evaluates leaves no dangling
    internal link (`tests/test_pairs.py`).
  - A folder's pairs share its content (`tests/test_pairs.py`).
  - Moves never put a layer in one channel twice, allow opposite orders
    in two channels and refuse a pair loop (`tests/test_pairs.py`).
  - A layer saved before pairs gets its virtual input and pair states
    before the tree compiles, in a local or a linked tree
    (`tests/test_layers.py`).
  - The pair list draws in the node editor sidebar
    (`tests/test_ui_draw.py`, CI only).
  - Dragging a link onto the virtual socket in the node editor, and the
    Pairs list (a GUI check by the user).
- Slice 4, done, with `tests/test_separate_color.py`:
  - An output starts another channel, and the layer feeding the node
    stays in its own stack.
  - A layer inserted, moved to the bottom or removed keeps the node at
    the bottom of the stack it starts.
  - HSV and HSL separate as the shader does, rename the outputs and keep
    their links, and the mode changes the hash.
  - `Alpha` gives the input's alpha, and a colour output carries an
    alpha of 1. Unlinked, the node separates its own value.
  - An output masks a layer, and a move that would loop through the node
    is refused, from a mask or from a stack the node starts.
  - A filter layer over it in a colour channel is refused as needing a
    Cycles bake, naming the node. A filter layer whose own mask comes
    from it still plans.
  - The node in the node editor (a GUI check by the user).
