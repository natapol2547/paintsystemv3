"""Intermediate representation produced by the compiler.

An ``IR`` is a plain Python description of a shader node tree. It holds
interface sockets, nodes keyed by a stable identifier, and the links between
them. It has no references back into the Paint System tree. So it can be
hashed, compared, and applied to any ``ShaderNodeTree`` with ``IR.apply``,
which uses ``NodeTreeBuilder``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import bpy

from .builder import BuildStats, NodeTreeBuilder


SocketId = int | str
Ref = tuple[str, SocketId]
"""Reference to an IR output socket, as (node identifier, socket id)."""


@dataclass
class IRSocket:
    in_out: str
    socket_type: str
    name: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class IRNode:
    id: str
    bl_idname: str
    properties: dict[str, Any] = field(default_factory=dict)
    inputs: dict[SocketId, dict[str, Any]] = field(default_factory=dict)
    outputs: dict[SocketId, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class IRLink:
    from_id: str
    from_socket: SocketId
    to_id: str
    to_socket: SocketId


class IR:
    def __init__(self) -> None:
        self.sockets: list[IRSocket] = []
        self.nodes: dict[str, IRNode] = {}
        self.links: list[IRLink] = []
        self.meta: dict[str, Any] = {}

    # -- authoring ------------------------------------------------------

    def add_socket(self, in_out: str, socket_type: str, name: str, **props: Any) -> IRSocket:
        sock = IRSocket(in_out, socket_type, name, dict(props))
        self.sockets.append(sock)
        return sock

    def add_node(
        self,
        identifier: str,
        bl_idname: str,
        *,
        properties: dict[str, Any] | None = None,
        inputs: dict[SocketId, dict[str, Any]] | None = None,
        outputs: dict[SocketId, dict[str, Any]] | None = None,
    ) -> IRNode:
        if identifier in self.nodes:
            raise ValueError(f"IR node identifier already used: {identifier}")
        node = IRNode(
            id=identifier,
            bl_idname=bl_idname,
            properties=dict(properties or {}),
            inputs={k: dict(v) for k, v in (inputs or {}).items()},
            outputs={k: dict(v) for k, v in (outputs or {}).items()},
        )
        self.nodes[identifier] = node
        return node

    def set_input(self, identifier: str, socket: SocketId, **props: Any) -> None:
        self.nodes[identifier].inputs.setdefault(socket, {}).update(props)

    def link(self, from_ref: Ref, to_id: str, to_socket: SocketId) -> None:
        from_id, from_socket = from_ref
        if from_id not in self.nodes:
            raise ValueError(f"Unknown IR node in link source: {from_id}")
        if to_id not in self.nodes:
            raise ValueError(f"Unknown IR node in link target: {to_id}")
        self.links.append(IRLink(from_id, from_socket, to_id, to_socket))

    # -- hashing --------------------------------------------------------

    def payload(self) -> dict[str, Any]:
        """Return everything the fingerprint covers, as plain JSON data.

        Nodes and links are sorted. So two IRs that describe the same tree
        give the same payload, whatever order they were emitted in.
        """
        return {
            "meta": dict(self.meta),
            "sockets": [
                [s.in_out, s.socket_type, s.name, _serialize(s.properties)]
                for s in self.sockets
            ],
            "nodes": {
                nid: [
                    n.bl_idname,
                    _serialize(n.properties),
                    _serialize(n.inputs),
                    _serialize(n.outputs),
                ]
                for nid, n in sorted(self.nodes.items())
            },
            "links": sorted(
                [l.from_id, str(l.from_socket), l.to_id, str(l.to_socket)]
                for l in self.links
            ),
        }

    def fingerprint(self) -> str:
        return hash_payload(self.payload())

    # -- applying -------------------------------------------------------

    def apply(self, node_tree: bpy.types.NodeTree, *, arrange: bool = True) -> BuildStats:
        """Update *node_tree* to match this IR, and return what changed."""
        builder = NodeTreeBuilder(node_tree, self)
        builder.build(arrange=arrange)
        return builder.stats


# -- serialization helpers ---------------------------------------------


def hash_payload(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(data.encode("utf-8")).hexdigest()


def _serialize(value: Any) -> Any:
    """Convert *value* to plain data that always gives the same JSON.

    Most payload values are floats, strings or dicts of those. So the exact
    type is checked first, which is fast. Anything else goes to the slower
    ``isinstance`` checks in ``_serialize_other``. Dict keys are not sorted
    here, because the payload is dumped with ``sort_keys=True``.
    """
    kind = type(value)
    if kind is float:
        return round(value, 6)
    if kind is str or kind is int or kind is bool or value is None:
        return value
    if kind is dict:
        return {str(k): _serialize(v) for k, v in value.items()}
    if kind is list or kind is tuple:
        return [_serialize(v) for v in value]
    return _serialize_other(value)


def _serialize_other(value: Any) -> Any:
    """Slow path of ``_serialize``, for datablocks, subclasses and the rest.

    Datablocks are checked before containers, because a datablock is
    identified by its name, not by its contents.
    """
    if isinstance(value, bpy.types.ID):
        return ["id", type(value).__name__, value.name_full]
    if isinstance(value, dict):
        return {str(k): _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    if isinstance(value, float):
        return round(value, 6)
    if hasattr(value, "__len__") and hasattr(value, "__getitem__"):
        # bpy_prop_array, mathutils types
        try:
            return [_serialize(v) for v in value]
        except TypeError:
            pass
    return repr(value)
