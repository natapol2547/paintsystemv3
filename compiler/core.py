"""Compiler: PaintSystemNodeTree -> IR -> compiled ShaderNodeTree.

Main entry points: ``compile_tree``, ``mark_dirty``, ``suspend_compile``,
``build_ir`` and ``CompileContext``.

- Data flows one way. The Paint System tree is the document. The compiled
  shader tree is a build artifact that the tree owns (``tree.compiled``).
- Nodes never own shader datablocks. Each node implements ``emit(ctx)``,
  which adds its part of the shader graph to the IR.
- Edits compile right away, not on a timer. See "Scheduling" below for
  why.
- A batch of edits runs inside ``suspend_compile`` and compiles once at
  the end.
- An artifact is only rewritten when its IR fingerprint changes.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from typing import Any, Iterable

import bpy

from .builder import BuildStats
from .ir import IR, Ref, SocketId, hash_payload, _serialize
from .library import MIX_IN_A_COLOR, MIX_IN_B_COLOR, MIX_IN_FACTOR, MIX_OUT_COLOR
from .profile import phase
from ..nodetree.stack_ops import feeding_link, feeds_clip_run, link_index, producing_link, stack_output
from ..props.channel import channel_alpha_name, interface_socket_specs

log = logging.getLogger(__name__)

PS_TREE_ID = 'PaintSystemNodeTree'
ARTIFACT_OWNER_KEY = "ps_owner"
# The fingerprint is stored on the artifact, not on the tree. So it always
# describes the nodes stored with it, whichever copy undo restores.
ARTIFACT_FINGERPRINT_KEY = "ps_fingerprint"

# What the last ``compile_tree`` changed in an artifact. ``None`` when the
# fingerprint matched and nothing was written. Tests and profiling read it
# to tell a small patch from a rebuild. No add-on code depends on it.
last_build_stats: BuildStats | None = None

_BASE_NODE_PROPS = {p.identifier for p in bpy.types.Node.bl_rna.properties}
# Identity and editing state that never reaches the shader.
_HASH_EXCLUDED_PROPS = {'uuid', 'is_expanded', 'lock_layer', 'lock_alpha'}
# Node class -> the property names node_state reads. See _hashed_props.
_hashed_prop_names: dict[type, tuple[str, ...]] = {}


# ── Tree helpers ─────────────────────────────────────────────────────


def ps_trees() -> Iterable[bpy.types.NodeTree]:
    for ng in bpy.data.node_groups:
        if ng.bl_idname == PS_TREE_ID:
            yield ng


def ensure_tree_uuid(tree) -> str:
    if not tree.uuid:
        tree.uuid = str(_uuid.uuid4())
    return tree.uuid


def artifact_name(tree) -> str:
    return f"PS {tree.name} [{tree.uuid[:8]}]"


def normalize_tree(tree) -> None:
    """Fix the tree's basic invariants, without triggering update callbacks.

    Afterwards:

    - every node has a uuid that is unique in the tree (copy and paste can
      make duplicates);
    - every channel has a uuid;
    - the Group Input and Group Output nodes exist, and one output is
      active.

    Links are not repaired here. A compile can run after the edit's undo
    step was pushed (see ``tree_updated``), and then it must not change the
    document.
    """
    ensure_tree_uuid(tree)
    seen: set[str] = set()
    for node in tree.nodes:
        node_uuid = getattr(node, 'uuid', None)
        if node_uuid is None:
            continue
        if not node_uuid or node_uuid in seen:
            node.uuid = str(_uuid.uuid4())
        seen.add(node.uuid)
    for ch in tree.channels:
        ch.ensure_uuid()
    tree.ensure_io_nodes()


def normalize_all_trees() -> None:
    seen: set[str] = set()
    for tree in ps_trees():
        ensure_tree_uuid(tree)
        if tree.uuid in seen:
            tree.uuid = str(_uuid.uuid4())
            tree.compiled = None
        seen.add(tree.uuid)


# ── Compile context ──────────────────────────────────────────────────


class CompileContext:
    """State passed to every node's ``emit`` during one IR build.

    It records the (colour, alpha) IR sockets that provide each Paint
    System node output, so nodes further down the graph can link to them.
    """

    def __init__(self, ir: IR, *, bake_target=None) -> None:
        self.ir = ir
        self.bake_target = bake_target
        self._outputs: dict[tuple[str, str], tuple[Ref, Ref]] = {}
        self._subtree_hashes: dict[str, str] = {}

    # -- emitting -------------------------------------------------------

    @staticmethod
    def node_id(node, role: str) -> str:
        return f"{node.uuid}:{role}"

    def emit_node(self, node, role: str, bl_idname: str, **kwargs: Any) -> str:
        identifier = self.node_id(node, role)
        self.ir.add_node(identifier, bl_idname, **kwargs)
        return identifier

    def link(self, from_ref: Ref, to_id: str, to_socket: SocketId) -> None:
        self.ir.link(from_ref, to_id, to_socket)

    def link_or_set(self, source: Ref | Any, to_id: str, to_socket: SocketId) -> None:
        """Link *source* if it is a Ref, otherwise store it as the socket default."""
        if _is_ref(source):
            self.ir.link(source, to_id, to_socket)
        else:
            self.ir.set_input(to_id, to_socket, default_value=_copy_value(source))

    # -- outputs ----------------------------------------------------------
    #
    # A link in the Paint System tree carries one RGBA value. In the shader
    # it is two sockets, a colour and an alpha. So every Paint System output
    # is recorded as a (colour, alpha) pair of refs, keyed by the output's
    # identifier. Its name is not used, because a renamed channel socket
    # keeps its old identifier and another socket may take the old name.

    def set_output(self, socket, color: Ref | Any, alpha: Ref | Any) -> None:
        """Record (*color*, *alpha*) as the value of the Paint System output *socket*.

        Each is an IR ref or a constant.
        """
        self._outputs[(socket.node.uuid, socket.identifier)] = (
            self._as_ref(socket, 'color', color), self._as_ref(socket, 'alpha', alpha))

    def _as_ref(self, socket, half: str, value: Ref | Any) -> Ref:
        if _is_ref(value):
            return value
        # A constant needs a real socket to link from, so emit a Value or
        # RGB node for it.
        role = f"const:{socket.identifier}:{half}"
        if isinstance(value, (int, float)):
            nid = self.emit_node(socket.node, role, 'ShaderNodeValue',
                                 outputs={0: {'default_value': float(value)}})
        else:
            nid = self.emit_node(socket.node, role, 'ShaderNodeRGB',
                                 outputs={0: {'default_value': _copy_value(value)}})
        return nid, 0

    def set_channel_output(self, socket, channel, ir_id: str) -> None:
        """Record *channel*'s two sockets on the IR node *ir_id* as the value of *socket*.

        A channel without ``use_alpha`` has no alpha socket, so its alpha
        is 1. At the Group Input, that makes the stack start from an
        opaque base.
        """
        alpha = (ir_id, channel_alpha_name(channel.name)) if channel.use_alpha else 1.0
        self.set_output(socket, (ir_id, channel.name), alpha)

    def output(self, socket) -> tuple[Ref, Ref] | None:
        """The (colour, alpha) refs recorded for the output *socket*, or None."""
        node_uuid = getattr(socket.node, 'uuid', None)
        if node_uuid is None:
            return None
        return self._outputs.get((node_uuid, socket.identifier))

    # -- reading inputs -------------------------------------------------

    def _upstream(self, socket) -> tuple[Ref, Ref] | None:
        """Return the (colour, alpha) refs that feed *socket*, or None if unlinked.

        Reroutes are skipped. Only Paint System nodes emit IR, so a link
        from any other kind of node reads as unlinked.
        """
        link = producing_link(socket)
        if link is None:
            return None
        return self.output(link.from_socket)

    def rgba_input(self, socket) -> tuple[Ref | Any, Ref | Any]:
        """Return (colour, alpha) for the colour input *socket*.

        Unlinked, the socket's default value gives the colour and its
        fourth component the alpha.
        """
        pair = self._upstream(socket)
        if pair is not None:
            return pair
        value = _copy_value(socket.default_value)
        return value, value[3]

    def connect_input(self, socket, to_id: str, to_socket: SocketId) -> None:
        """Link or set a one-value input, such as a layer's ``Mask``.

        Linked, it reads the colour half, and the shader converts that to
        the input's type.
        """
        pair = self._upstream(socket)
        self.link_or_set(pair[0] if pair is not None else _copy_value(socket.default_value),
                         to_id, to_socket)

    def link_channel(self, socket, channel, to_id: str) -> None:
        """Link what feeds *socket* into *channel*'s two sockets on the IR node *to_id*.

        An unlinked *socket* links nothing, so *to_id* keeps the defaults
        of the compiled interface. Without ``use_alpha`` the channel has no
        alpha socket, and the alpha half is dropped.
        """
        pair = self._upstream(socket)
        if pair is not None:
            color, alpha = pair
            self.link(color, to_id, channel.name)
            if channel.use_alpha:
                self.link(alpha, to_id, channel_alpha_name(channel.name))

    def link_flattened(self, socket, channel, to_id: str, base: Ref) -> None:
        """Link what feeds *socket* into *channel*'s socket on *to_id*, laid over *base*.

        For a channel without ``use_alpha`` at the Group Output, where the
        alpha has no socket to go to. It weights the colour over *base*,
        the value the stack started from, instead of being dropped. Layers
        keep an opaque base opaque, so the mix usually changes nothing. A
        filter layer does not: it replaces the stack with pixels filtered
        without the base, and its soft edges would otherwise show their
        colour at full strength. Over a black base this is the same as
        multiplying the colour by its alpha. An unlinked *socket* is an
        empty stack, so only *base* shows.
        """
        pair = self._upstream(socket)
        if pair is None:
            self.link(base, to_id, channel.name)
            return
        color, alpha = pair
        mix = self.emit_node(socket.node, f"flatten:{socket.identifier}", 'ShaderNodeMix', properties={
            'data_type': 'RGBA', 'blend_type': 'MIX', 'clamp_factor': True, 'clamp_result': False,
        })
        self.link(alpha, mix, MIX_IN_FACTOR)
        self.link(base, mix, MIX_IN_A_COLOR)
        self.link(color, mix, MIX_IN_B_COLOR)
        self.link((mix, MIX_OUT_COLOR), to_id, channel.name)

    # -- caching --------------------------------------------------------

    def is_cached(self, node) -> bool:
        if node == self.bake_target:
            return False
        if not getattr(node, 'cache_enabled', False):
            return False
        if getattr(node, 'cache_image', None) is None:
            return False
        # A cache image holds a finished stack, but these outputs are part
        # of a clip run. The top layer of the run also needs the base's
        # inputs compiled.
        if feeds_clip_run(node):
            return False
        return node.cache_hash == self.subtree_hash(node)

    def subtree_hash(self, node) -> str:
        """Return a hash of everything that affects *node*'s outputs.

        That is its own properties, its unlinked socket values, its
        ``hash_parts`` and, recursively, every node upstream of it.
        """
        cached = self._subtree_hashes.get(node.name)
        if cached is not None:
            return cached
        # A placeholder entry stops a cycle from recursing forever.
        self._subtree_hashes[node.name] = "cycle"
        parts: list[Any] = [node.bl_idname, _serialize(node_state(node))]
        for sock in node.inputs:
            link = feeding_link(sock)
            if link is not None:
                parts.append([sock.identifier, 'link',
                              self.subtree_hash(link.from_node),
                              link.from_socket.identifier])
            else:
                parts.append([sock.identifier, 'value', _serialize(sock.default_value)])
        extra = getattr(node, 'hash_parts', None)
        if extra is not None:
            parts.append(_serialize(extra(self)))
        result = hash_payload(parts)
        self._subtree_hashes[node.name] = result
        return result


def _hashed_props(node) -> tuple[str, ...]:
    """Return the property names ``node_state`` reads, cached per node class.

    A class's properties do not change while it is registered, and listing
    them is most of the cost of hashing a subtree. The cache is keyed by
    the class object and cleared in ``reset_state``. So after an add-on
    reload it never returns the names of a class that no longer exists.

    A node class can leave properties out with ``ps_unhashed_props``. This
    is for state that does not change the compiled artifact by itself. For
    example, a filter layer's settings change nothing until its image is
    rebuilt, and the rebuild shows up through ``hash_parts`` instead.
    """
    cls = type(node)
    names = _hashed_prop_names.get(cls)
    if names is None:
        unhashed = getattr(cls, 'ps_unhashed_props', ())
        names = tuple(
            prop.identifier for prop in node.bl_rna.properties
            if prop.identifier not in _BASE_NODE_PROPS
            and prop.identifier not in _HASH_EXCLUDED_PROPS
            and prop.identifier not in unhashed
            and not prop.identifier.startswith(('cache_', 'rna_'))
        )
        _hashed_prop_names[cls] = names
    return names


def node_state(node) -> dict[str, Any]:
    """Return the node's own property values that go into its hash.

    Base ``Node`` properties, ``uuid`` and ``cache_*`` properties are left
    out. See ``_hashed_props`` for the other rules.
    """
    return {ident: getattr(node, ident) for ident in _hashed_props(node)}


def _is_ref(value: Any) -> bool:
    return (isinstance(value, tuple) and len(value) == 2
            and isinstance(value[0], str) and isinstance(value[1], (int, str)))


def _copy_value(value: Any) -> Any:
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    try:
        return tuple(value)
    except TypeError:
        return value


# ── IR construction ──────────────────────────────────────────────────


def interface_inputs(ir: IR, channels) -> None:
    for name, socket_type, props in interface_socket_specs(channels):
        ir.add_socket('INPUT', socket_type, name, **props)


def interface_outputs(ir: IR, channels) -> None:
    for name, socket_type, _ in interface_socket_specs(channels):
        ir.add_socket('OUTPUT', socket_type, name)


def topological_order(start, ctx: CompileContext) -> list:
    """Return every node reachable from *start*, upstream nodes first.

    Cached nodes are treated as leaves, so their upstream is not compiled.
    """
    order: list = []
    visited: set[str] = set()

    def visit(node) -> None:
        if node.name in visited:
            return
        visited.add(node.name)
        if not ctx.is_cached(node):
            for sock in node.inputs:
                link = producing_link(sock)
                if link is not None:
                    visit(link.from_node)
        order.append(node)

    visit(start)
    return order


def build_ir(tree, *, bake_target=None) -> IR:
    """Build the IR for *tree*, or for the subtree of the layer *bake_target* when given."""
    # The build only reads *tree*'s links, so one link index serves the
    # whole walk. A nested compile of a child tree installs its own index,
    # under its own key.
    with link_index(tree):
        return _build_ir(tree, bake_target=bake_target)


def _build_ir(tree, *, bake_target=None) -> IR:
    ir = IR()
    ir.meta['tree'] = tree.name
    ctx = CompileContext(ir, bake_target=bake_target)

    # The inputs are always the channel sockets, so a Group Input node
    # upstream of the bake target still resolves. In the bake material
    # those inputs are unlinked, which reads as an empty stack.
    interface_inputs(ir, tree.channels)
    if bake_target is None:
        interface_outputs(ir, tree.channels)
        start = tree.get_output_node()
    else:
        ir.add_socket('OUTPUT', 'NodeSocketColor', 'Color')
        ir.add_socket('OUTPUT', 'NodeSocketFloat', 'Alpha')
        start = bake_target

    if start is None:
        return ir

    for node in topological_order(start, ctx):
        emit = getattr(node, 'emit', None)
        if emit is not None:
            emit(ctx)

    if bake_target is not None:
        out_id = ir.add_node('bake:out', 'NodeGroupOutput').id
        pair = ctx.output(stack_output(bake_target))
        if pair is not None:
            color, alpha = pair
            ir.link(color, out_id, 'Color')
            ir.link(alpha, out_id, 'Alpha')
    # Not part of the fingerprint. The bake reads subtree hashes from it.
    ir.ctx = ctx
    return ir


# ── Artifact management ──────────────────────────────────────────────


def ensure_artifact(tree) -> bpy.types.NodeTree:
    art = tree.compiled
    if art is not None:
        if art.bl_idname != 'ShaderNodeTree' or art.get(ARTIFACT_OWNER_KEY) != tree.uuid:
            art = None
    if art is None:
        art = bpy.data.node_groups.new(artifact_name(tree), 'ShaderNodeTree')
        art[ARTIFACT_OWNER_KEY] = tree.uuid
        tree.compiled = art
    name = artifact_name(tree)
    if art.name != name:
        art.name = name
    return art


def cleanup_orphan_artifacts() -> int:
    """Remove compiled trees whose owning Paint System tree no longer exists."""
    owners = {tree.uuid for tree in ps_trees()}
    removed = 0
    for ng in list(bpy.data.node_groups):
        owner = ng.get(ARTIFACT_OWNER_KEY)
        if owner is None or owner in owners:
            continue
        if ng.users == 0 or (ng.users == 1 and ng.use_fake_user):
            bpy.data.node_groups.remove(ng)
            removed += 1
    return removed


def artifact_fingerprint(tree) -> str:
    """Fingerprint of the IR the artifact was last built from ("" if none)."""
    art = tree.compiled
    return art.get(ARTIFACT_FINGERPRINT_KEY, "") if art is not None else ""


def compile_tree(tree, *, force: bool = False) -> str:
    """Bring ``tree.compiled`` up to date. Returns the IR fingerprint."""
    global last_build_stats
    with phase("normalize", tree):
        normalize_tree(tree)
    with phase("build_ir", tree):
        ir = build_ir(tree)
    with phase("fingerprint", tree):
        fingerprint = ir.fingerprint()
    artifact = ensure_artifact(tree)
    if force or artifact.get(ARTIFACT_FINGERPRINT_KEY) != fingerprint:
        with phase("apply", tree) as timing:
            last_build_stats = ir.apply(artifact)
            timing.detail(last_build_stats)
        artifact[ARTIFACT_FINGERPRINT_KEY] = fingerprint
    else:
        last_build_stats = None
    return fingerprint


# ── Scheduling ───────────────────────────────────────────────────────
#
# Edits compile right away, before Blender pushes the undo step for them.
# Blender's memfile (global) undo reuses every datablock that is
# byte-identical in two neighbouring steps, instead of reading it again.
# So an artifact changed after its step was pushed (from a timer, for
# example) can survive an undo. It would keep nodes for layers that no
# longer exist, and pointers to images the undo just freed.
#
# Four cases cannot compile on the spot:
# - Inside ``suspend_compile``. The outermost exit compiles once.
# - While a file loads or an undo step is restored. Blender calls
#   ``NodeTree.update`` on half-restored data then. The post handler
#   compiles.
# - While ``bpy.data`` is restricted (add-on registration) or writing is
#   forbidden (drawing). A timer compiles as soon as Blender allows it.
# - Inside ``NodeTree.update``, for edits made in the node editor. See
#   ``tree_updated``.

_dirty_uuids: set[str] = set()
_dirty_all = False
_suspended = 0
_blocked = False
_flushing = False

# Compiling a tree can mark other trees dirty, such as the trees that wrap
# it in a group layer. Compiling repeats for up to this many rounds.
# Anything still dirty after that waits for the next edit.
_MAX_FLUSH_ROUNDS = 8


def mark_dirty(tree=None) -> None:
    """Recompile *tree* (every tree when None) now, or as soon as allowed."""
    global _dirty_all
    if tree is None:
        _dirty_all = True
    else:
        _dirty_uuids.add(ensure_tree_uuid(tree))
    if _suspended or _flushing or _blocked:
        return
    if not isinstance(bpy.data, bpy.types.BlendData):
        _schedule()
        return
    flush()


def tree_updated(tree) -> None:
    """Handle ``NodeTree.update``: compile on the next tick when needed.

    Blender does not rebuild node sockets while it runs node tree update
    callbacks, because ``BKE_ntree_update`` returns early when re-entered.
    So a group node created here would have no sockets to link, and the
    build is left to a timer.

    The IR is still built here, to tell a no-op from a real change. On a
    real change, the artifact's fingerprint is set to a new random token
    that no earlier state has. That stamp is part of the edit's undo step.
    So memfile undo sees the artifact differ from every other step and
    reads it again, instead of keeping the copy the timer changed after
    the step was pushed.
    """
    if _suspended or _flushing or _blocked or not isinstance(bpy.data, bpy.types.BlendData):
        mark_dirty(tree)
        return
    try:
        current = artifact_fingerprint(tree) == build_ir(tree).fingerprint()
    except Exception:
        log.debug("could not fingerprint '%s' during a node tree update", tree.name, exc_info=True)
        current = False
    if current:
        return
    _dirty_uuids.add(ensure_tree_uuid(tree))
    ensure_artifact(tree)[ARTIFACT_FINGERPRINT_KEY] = f"pending {_uuid.uuid4().hex}"
    _schedule()


def _schedule() -> None:
    if not bpy.app.timers.is_registered(flush):
        try:
            bpy.app.timers.register(flush, first_interval=0.0)
        except Exception:
            # This can fail while the add-on unregisters. Nothing needs
            # scheduling then.
            log.debug("could not schedule compile flush", exc_info=True)


def flush():
    """Compile every dirty tree. Also used as the fallback timer callback.

    As a Blender timer, the return value picks the next run. While
    compiles are suspended or blocked it returns 0.05, so it runs again
    0.05 seconds later. Otherwise it returns None, which stops the timer.
    """
    global _dirty_all, _flushing
    if _suspended or _blocked:
        return 0.05
    if _flushing:
        return None
    _flushing = True
    try:
        for _round in range(_MAX_FLUSH_ROUNDS):
            if not (_dirty_all or _dirty_uuids):
                break
            normalize_all_trees()
            if _dirty_all:
                targets = list(ps_trees())
            else:
                targets = [t for t in ps_trees() if t.uuid in _dirty_uuids]
            _dirty_all = False
            _dirty_uuids.clear()
            if not _compile_targets(targets):
                break
        else:
            if _dirty_all or _dirty_uuids:
                log.warning("compile did not settle after %d rounds", _MAX_FLUSH_ROUNDS)
    finally:
        _flushing = False
    return None


def _compile_targets(targets) -> bool:
    """Compile *targets*, and return False if a timer has to take over.

    Blender forbids writing to its data in some contexts, such as while
    drawing, and raises an AttributeError that says "not allowed". The
    tree that failed and the ones after it are then marked dirty again
    for the timer.
    """
    for index, tree in enumerate(targets):
        try:
            compile_tree(tree)
        except AttributeError as exc:
            if "not allowed" not in str(exc):
                log.exception("failed to compile '%s'", tree.name)
                continue
            for rest in targets[index:]:
                _dirty_uuids.add(rest.uuid)
            _schedule()
            return False
        except Exception:
            log.exception("failed to compile '%s'", tree.name)
    return True


def flush_now() -> None:
    """Compile pending trees immediately and drop any fallback timer."""
    if bpy.app.timers.is_registered(flush):
        bpy.app.timers.unregister(flush)
    flush()


def block_compile() -> None:
    """Hold compiles while Blender restores data (file load, undo, redo)."""
    global _blocked
    _blocked = True


def unblock_compile() -> None:
    """End a ``block_compile``. The caller then marks what needs compiling."""
    global _blocked
    _blocked = False


class suspend_compile:
    """Context manager that batches edits and compiles once at the end.

    Nested blocks only compile when the outermost one exits. Each exit
    marks *tree* dirty, or every tree when *tree* is None.
    """

    def __init__(self, tree=None):
        self.tree = tree

    def __enter__(self):
        global _suspended
        _suspended += 1
        return self

    def __exit__(self, *exc):
        global _suspended
        _suspended -= 1
        mark_dirty(self.tree)
        return False


def reset_state() -> None:
    global _dirty_all, _suspended, _blocked, _flushing
    _hashed_prop_names.clear()
    _dirty_uuids.clear()
    _dirty_all = False
    _suspended = 0
    _blocked = False
    _flushing = False
    if bpy.app.timers.is_registered(flush):
        bpy.app.timers.unregister(flush)
