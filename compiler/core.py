"""Compiler: PaintSystemNodeTree -> IR -> compiled ShaderNodeTree.

Data flows one way. The Paint System tree is the document; the compiled shader
tree is a build artifact owned by the tree (``tree.compiled``). Nodes never own
shader datablocks. They implement ``emit(ctx)`` which appends to the IR.

Edits compile synchronously (see "Scheduling" below for why not on a timer).
Batches of edits run inside ``suspend_compile`` and compile once at the end.
An artifact is only rewritten when its IR fingerprint changes.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from typing import Any, Iterable

import bpy

from .ir import IR, Ref, SocketId, hash_payload, _serialize
from ..props.channel import channel_socket_specs

log = logging.getLogger(__name__)

PS_TREE_ID = 'PaintSystemNodeTree'
ARTIFACT_OWNER_KEY = "ps_owner"
# The fingerprint lives on the artifact, not on the tree, so it always
# describes the nodes it sits next to whichever copy undo restores.
ARTIFACT_FINGERPRINT_KEY = "ps_fingerprint"
BAKE_TREE_NAME = ".PS Bake Target"

_BASE_NODE_PROPS = {p.identifier for p in bpy.types.Node.bl_rna.properties}
_HASH_EXCLUDED_PROPS = {'uuid'}


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
    """Repair invariants without triggering update callbacks.

    - every node has a uuid unique within the tree (duplicates come from copy/paste)
    - group input/output nodes exist and one output is active
    - at least one channel exists
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
    """Passed to every node's ``emit``. Tracks which IR sockets provide each
    custom node's outputs so downstream nodes can link to them."""

    def __init__(self, tree, ir: IR, *, bake_target=None) -> None:
        self.tree = tree
        self.ir = ir
        self.bake_target = bake_target
        self._outputs: dict[tuple[str, str], Ref] = {}
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

    def set_output(self, node, socket_name: str, ir_id: str, ir_socket: SocketId) -> None:
        self._outputs[(node.uuid, socket_name)] = (ir_id, ir_socket)

    def alias_output(self, node, socket_name: str, source: Ref | Any) -> None:
        """Expose *source* (a Ref or a constant) as one of *node*'s outputs."""
        if _is_ref(source):
            self._outputs[(node.uuid, socket_name)] = source
        else:
            # Constants need a real socket to be linked from; emit a value node.
            role = f"const:{socket_name}"
            if isinstance(source, (int, float)):
                nid = self.emit_node(node, role, 'ShaderNodeValue',
                                     outputs={0: {'default_value': float(source)}})
            else:
                nid = self.emit_node(node, role, 'ShaderNodeRGB',
                                     outputs={0: {'default_value': _copy_value(source)}})
            self._outputs[(node.uuid, socket_name)] = (nid, 0)

    def output_ref(self, node, socket_name: str) -> Ref | None:
        return self._outputs.get((node.uuid, socket_name))

    # -- reading inputs -------------------------------------------------

    @staticmethod
    def incoming_link(socket) -> bpy.types.NodeLink | None:
        for link in socket.links:
            if link.is_muted or not link.is_valid:
                continue
            return link
        return None

    def upstream(self, socket) -> Ref | None:
        """IR reference feeding *socket* on a custom node, or None if unlinked."""
        link = self.incoming_link(socket)
        if link is None:
            return None
        return self._outputs.get((link.from_node.uuid, link.from_socket.name))

    def input_source(self, socket) -> Ref | Any:
        """Ref when linked, else the socket's default value."""
        ref = self.upstream(socket)
        if ref is not None:
            return ref
        return _copy_value(socket.default_value)

    def connect_input(self, socket, to_id: str, to_socket: SocketId) -> None:
        self.link_or_set(self.input_source(socket), to_id, to_socket)

    # -- caching --------------------------------------------------------

    def is_cached(self, node) -> bool:
        if node == self.bake_target:
            return False
        if not getattr(node, 'cache_enabled', False):
            return False
        if getattr(node, 'cache_image', None) is None:
            return False
        return node.cache_hash == self.subtree_hash(node)

    def subtree_hash(self, node) -> str:
        """Hash of everything that influences *node*'s outputs: its own
        properties, its unlinked socket values and, recursively, its upstream."""
        cached = self._subtree_hashes.get(node.name)
        if cached is not None:
            return cached
        # Guard against cycles: a provisional entry stops infinite recursion.
        self._subtree_hashes[node.name] = "cycle"
        parts: list[Any] = [node.bl_idname, _serialize(node_state(node))]
        for sock in node.inputs:
            link = self.incoming_link(sock)
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


def node_state(node) -> dict[str, Any]:
    """Node-specific properties (excluding base Node props, uuid, cache_*)."""
    state: dict[str, Any] = {}
    for prop in node.bl_rna.properties:
        ident = prop.identifier
        if ident in _BASE_NODE_PROPS or ident in _HASH_EXCLUDED_PROPS:
            continue
        if ident.startswith('cache_') or ident.startswith('rna_'):
            continue
        state[ident] = getattr(node, ident)
    return state


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
    for name, socket_type, props in channel_socket_specs(channels):
        props = dict(props)
        props.pop('hide_value', None)
        ir.add_socket('INPUT', socket_type, name, **props)


def interface_outputs(ir: IR, channels) -> None:
    for name, socket_type, _ in channel_socket_specs(channels):
        ir.add_socket('OUTPUT', socket_type, name)


def topological_order(start, ctx: CompileContext) -> list:
    """Upstream-first order of every node reachable from *start*.

    Cached nodes are treated as leaves: their upstream is not compiled.
    """
    order: list = []
    visited: set[str] = set()

    def visit(node) -> None:
        if node.name in visited:
            return
        visited.add(node.name)
        if not ctx.is_cached(node):
            for sock in node.inputs:
                link = ctx.incoming_link(sock)
                if link is not None:
                    visit(link.from_node)
        order.append(node)

    visit(start)
    return order


def build_ir(tree, *, bake_target=None) -> IR:
    ir = IR()
    ir.meta['tree'] = tree.name
    ctx = CompileContext(tree, ir, bake_target=bake_target)

    # Inputs are always the channel sockets so a Group Input node upstream of
    # the bake target still resolves (unlinked in the bake material = empty stack).
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
        for socket_name in ('Color', 'Alpha'):
            ref = ctx.output_ref(bake_target, socket_name)
            if ref is not None:
                ir.link(ref, out_id, socket_name)
    ir.ctx = ctx  # instance attribute, not part of the fingerprint; bake reads subtree hashes
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
    normalize_tree(tree)
    ir = build_ir(tree)
    fingerprint = ir.fingerprint()
    artifact = ensure_artifact(tree)
    if force or artifact.get(ARTIFACT_FINGERPRINT_KEY) != fingerprint:
        ir.apply(artifact)
        artifact[ARTIFACT_FINGERPRINT_KEY] = fingerprint
    return fingerprint


# ── Scheduling ───────────────────────────────────────────────────────
#
# Edits compile synchronously, before Blender pushes the undo step for
# them. Memfile undo takes every datablock that is byte-identical in two
# consecutive steps straight from memory instead of re-reading it. An
# artifact patched after its step was pushed (from a timer, say) can
# therefore survive an undo with nodes for layers that no longer exist
# and pointers to images the undo just freed.
#
# Three situations cannot compile on the spot:
# - inside ``suspend_compile``: the outermost exit compiles once;
# - while a file loads or an undo step decodes (Blender calls
#   ``NodeTree.update`` on half-restored data): the post handler compiles;
# - while ``bpy.data`` is restricted (addon registration) or writing is
#   forbidden (drawing): a timer compiles as soon as Blender allows it.

_dirty_uuids: set[str] = set()
_dirty_all = False
_suspended = 0
_blocked = False
_flushing = False

# Compiling a tree can dirty others (parents of a group layer). Settle in
# a few rounds; anything left after that waits for the next edit.
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


def _schedule() -> None:
    if not bpy.app.timers.is_registered(flush):
        try:
            bpy.app.timers.register(flush, first_interval=0.0)
        except Exception:
            # Can fail during addon unregister; nothing to schedule then.
            log.debug("could not schedule compile flush", exc_info=True)


def flush():
    """Compile every dirty tree. Doubles as the fallback timer callback."""
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
    """Compile *targets*; False when writing is forbidden and a timer takes over."""
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
    """Context manager: batch many edits, compile once at the outermost exit."""

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
    _dirty_uuids.clear()
    _dirty_all = False
    _suspended = 0
    _blocked = False
    _flushing = False
    if bpy.app.timers.is_registered(flush):
        bpy.app.timers.unregister(flush)
