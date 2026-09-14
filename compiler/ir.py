"""Intermediate representation produced by the compiler.

An ``IR`` is a plain Python description of a shader node tree: interface
sockets, nodes keyed by a stable identifier, and links between them. It has no
references back into the Paint System tree, so it can be hashed, compared and
applied to any ``ShaderNodeTree`` through ``NodeTreeBuilder``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import bpy

from ..nodes.builder import NodeTreeBuilder, Flexible


SocketId = int | str
Ref = tuple[str, SocketId]
"""(ir node identifier, socket id) — a reference to an output socket in the IR."""


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
    specials: dict[str, dict[str, Any]] = field(default_factory=dict)


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
        specials: dict[str, dict[str, Any]] | None = None,
    ) -> IRNode:
        if identifier in self.nodes:
            raise ValueError(f"IR node identifier already used: {identifier}")
        node = IRNode(
            id=identifier,
            bl_idname=bl_idname,
            properties=dict(properties or {}),
            inputs={k: dict(v) for k, v in (inputs or {}).items()},
            outputs={k: dict(v) for k, v in (outputs or {}).items()},
            specials={k: dict(v) for k, v in (specials or {}).items()},
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

    def fingerprint(self) -> str:
        payload = {
            "meta": self.meta,
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
                    _serialize(n.specials),
                ]
                for nid, n in sorted(self.nodes.items())
            },
            "links": sorted(
                [l.from_id, str(l.from_socket), l.to_id, str(l.to_socket)]
                for l in self.links
            ),
        }
        return hash_payload(payload)

    # -- applying -------------------------------------------------------

    def apply(self, node_tree: bpy.types.NodeTree, *, arrange: bool = True) -> None:
        builder = NodeTreeBuilder(node_tree)
        for sock in self.sockets:
            builder.add_socket(sock.in_out, sock.socket_type, sock.name, **sock.properties)
        for node in self.nodes.values():
            builder.add_node(
                node.id, node.bl_idname,
                properties=node.properties,
                inputs=node.inputs,
                outputs=node.outputs,
                specials=node.specials,
            )
        for link in self.links:
            builder.link_nodes(link.from_id, link.to_id, link.from_socket, link.to_socket)
        builder.build(arrange=arrange)


# -- serialization helpers ---------------------------------------------


def hash_payload(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(data.encode("utf-8")).hexdigest()


def _serialize(value: Any) -> Any:
    """Convert *value* into something json.dumps can handle deterministically."""
    if isinstance(value, Flexible):
        return ["flexible", _serialize(value.value)]
    if isinstance(value, bpy.types.ID):
        return ["id", type(value).__name__, value.name_full]
    if isinstance(value, dict):
        return {str(k): _serialize(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
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
