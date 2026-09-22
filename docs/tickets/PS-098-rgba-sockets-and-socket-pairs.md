# PS-098 RGBA sockets and socket pairs

Epic B. Size L. Milestone M2. The layer socket model that linked layers
(PS-016) and the clipboard (PS-017) build on.

## Status

- Slice 1, RGBA-only sockets: done. See "Slice 1" below.
- Slice 2, socket pairs and the virtual input: not started.
- Slice 3, Paste and Paste Linked: not started. Rewrites PS-016 and
  PS-017 around slice 2.
- Slice 4, a Separate Color node in the Paint System tree: not started.

## Decisions

Made with the user on 2026-09-23.

- Every link in a Paint System tree carries one RGBA value, as in the
  compositor. There are no Alpha sockets on layers, folders, group layers
  or the Group Input and Output. The compiled shader group still has
  `<channel>` and `<channel> Alpha` sockets, because materials need them
  apart.
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
  per pair.
- No migration. v3 is unreleased, so a tree saved before slice 1 keeps
  its stale Alpha sockets and links; make a fresh tree instead.

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
  typed by the channel, then `<channel> Alpha` as a float.

### Compiler

- A Paint System output is recorded as a `(colour, alpha)` pair of IR
  references, keyed by `(node uuid, socket identifier)`. A constant half
  becomes a Value or RGB node with the role `const:<identifier>:<half>`.
- Consumers read that pair through `CompileContext.rgba_input` (the
  unlinked default's fourth value is the alpha), `connect_input` (one
  value, used for `Mask`) and `link_channel` / `set_channel_output` (the
  only consumers that link by the `<channel> Alpha` name;
  `interface_socket_specs` declares it).
- A colour linked into `Mask` is converted by the shader's implicit
  conversion, which is luminance. PS-015 decides whether masks should
  read luminance, the red channel or alpha.
- `IR.meta` holds the child interface of every group layer, so a child
  channel that changes type rebuilds the parent. Without it Blender drops
  the parent's link to the recreated socket and the fingerprint does not
  notice (found by the design review, covered by `tests/test_compile.py`).

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
  layer after placing it, and puts the layer back and returns False when
  it finds a new one (found by the code review; the old `detach` cut the
  mask link instead).
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

- Input order on a layer is `[pair inputs..., virtual, Mask]`; on a
  folder `[Color, Content Color, pair inputs..., virtual, Mask]`. The
  first pair is the existing `Color` input and output, so a one-pair
  layer looks exactly like slice 1.
- A new pair is made with `inputs.new` and moved above the virtual
  socket. The handler lives in `Node.update` and must be idempotent:
  `insert_link` is not called for links made from Python, `links.new`
  fires `update` twice, and on 5.x `update` can fire during `init`.
- `NodeSocketVirtual` has no `default_value`, so `is_slot`,
  `channel_sockets` and `rgba_input` must never see it.
- Each pair compiles as its own blend, from its input to its output, with
  the node's shared settings.
- Prerequisite for linked layers: `detach`, `attach` and `move` must take
  the slot of the stack being edited and touch only that link. Today
  `detach` removes every slot link on the output and relinks one, so a
  layer that feeds two stacks empties the other when it moves.
  `layer_above` likewise needs to know which stack it is asked about.
- Blender's cycle check is per node, so one node twice on one stack path
  gets an invalid link. Paste Linked refuses that case.

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
  - A float child channel feeds the parent's channel and its alpha, and
    retyping the child keeps the parent's compiled links
    (`tests/test_compile.py`).
- Slice 2:
  - Linking into the virtual input adds a pair above it; the link lands
    on the new pair and `Mask` stays last.
  - Removing a pair in the sidebar list keeps the other pairs' links; the
    last pair cannot be removed.
  - Unlinking a pair's input keeps the pair and its output.
  - Dragging a link onto the virtual socket in the node editor (a GUI
    check by the user).
