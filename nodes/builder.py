from __future__ import annotations

import bpy
from dataclasses import dataclass, field
from typing import Any
from bpy.props import IntProperty, StringProperty


@dataclass
class Flexible:
    """Wrap a value to apply it only when the node/socket is first created."""
    value: Any


@dataclass
class NodeInstruction:
    bl_idname: str
    properties: dict[str, Any] = field(default_factory=dict)
    inputs: dict[int | str, dict[str, Any]] = field(default_factory=dict)
    outputs: dict[int | str, dict[str, Any]] = field(default_factory=dict)
    specials: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class SocketInstruction:
    in_out: str  # 'INPUT' or 'OUTPUT'
    socket_type: str
    name: str
    properties: dict[str, Any] = field(default_factory=dict)


LinkKey = tuple[str, int, str, int]


class NodeTreeBuilder:
    def __init__(self, node_tree: bpy.types.NodeTree):
        """Initialize the NodeTreeBuilder.

        Args:
            node_tree (bpy.types.NodeTree): The node tree to build.
        """
        self.node_tree = node_tree
        self._node_instructions: dict[str, NodeInstruction] = {}
        self._link_instructions: set[tuple[str,
                                           int | str, str, int | str]] = set()
        self._socket_instructions: list[SocketInstruction] = []
        self._existing_nodes: dict[str, bpy.types.Node] = {}
        self._hydrate_existing_nodes()

    # ── Instruction API (all return self for chaining) ───────────────

    def add_node(
        self,
        identifier: str,
        bl_idname: str,
        *,
        properties: dict[str, Any] | None = None,
        inputs: dict[int | str, dict[str, Any]] | None = None,
        outputs: dict[int | str, dict[str, Any]] | None = None,
        specials: dict[str, dict[str, Any]] | None = None,
    ) -> NodeTreeBuilder:
        instr = NodeInstruction(bl_idname=bl_idname)
        if properties:
            instr.properties = dict(properties)
        if inputs:
            instr.inputs = {k: dict(v) for k, v in inputs.items()}
        if outputs:
            instr.outputs = {k: dict(v) for k, v in outputs.items()}
        if specials:
            instr.specials = {k: dict(v) for k, v in specials.items()}
        self._node_instructions[identifier] = instr
        return self

    def set_node_property(
        self, identifier: str, prop: str, value: Any
    ) -> NodeTreeBuilder:
        self._node_instructions[identifier].properties[prop] = value
        return self

    def set_node_input(
        self, identifier: str, socket_id: int | str, **props: Any
    ) -> NodeTreeBuilder:
        instr = self._node_instructions[identifier]
        instr.inputs.setdefault(socket_id, {}).update(props)
        return self

    def set_node_output(
        self, identifier: str, socket_id: int | str, **props: Any
    ) -> NodeTreeBuilder:
        instr = self._node_instructions[identifier]
        instr.outputs.setdefault(socket_id, {}).update(props)
        return self

    def set_node_special(
        self, identifier: str, attr_name: str, props: dict[str, Any]
    ) -> NodeTreeBuilder:
        self._node_instructions[identifier].specials[attr_name] = dict(props)
        return self

    def add_socket(
        self,
        in_out: str,
        socket_type: str,
        name: str,
        **kwargs: Any,
    ) -> NodeTreeBuilder:
        """Declare an interface socket on the node tree.

        Args:
            in_out: 'INPUT' or 'OUTPUT'.
            socket_type: Blender socket bl_idname (e.g. 'NodeSocketFloat').
            name: Display name of the socket.
            **kwargs: Properties to force-apply to the socket on every build.
        """
        if not self.node_tree.bl_use_group_interface:
            raise ValueError("Node tree does not use group interface")
        self._socket_instructions.append(
            SocketInstruction(
                in_out=in_out,
                socket_type=socket_type,
                name=name,
                properties=dict(kwargs),
            )
        )
        return self

    def link_nodes(
        self,
        from_identifier: str,
        to_identifier: str,
        from_socket: int | str = 0,
        to_socket: int | str = 0,
    ) -> NodeTreeBuilder:
        self._link_instructions.add(
            (from_identifier, from_socket, to_identifier, to_socket)
        )
        return self

    # ── Build ────────────────────────────────────────────────────────

    def build(self) -> None:
        # Sync node tree interface sockets
        self._sync_interface_sockets()

        desired_ids = set(self._node_instructions.keys())

        # Remove excess nodes (auto-removes their links)
        for identifier in list(self._existing_nodes.keys()):
            if identifier not in desired_ids:
                self.node_tree.nodes.remove(
                    self._existing_nodes.pop(identifier))

        # Upsert nodes and apply properties / socket values
        for identifier, instr in self._node_instructions.items():
            node = self._existing_nodes.get(identifier)

            if node is not None and node.bl_idname != instr.bl_idname:
                self.node_tree.nodes.remove(node)
                node = None

            is_new = node is None
            if is_new:
                node = self.node_tree.nodes.new(instr.bl_idname)
                node.ps_identifier = identifier
                self._existing_nodes[identifier] = node

            self._apply_node_properties(node, instr.properties, is_new)
            self._apply_socket_properties(node.inputs, instr.inputs, is_new)
            self._apply_socket_properties(node.outputs, instr.outputs, is_new)

            for attr_name, props in instr.specials.items():
                print(f"Applying special {attr_name} to node {node.name}")
                sub_obj = getattr(node, attr_name, None)
                if sub_obj is not None:
                    self._apply_props_recursive(sub_obj, props, is_new)
                else:
                    print(f"Special {attr_name} not found on node {node.name}")

        # Remove excess links
        desired_link_keys = self._build_desired_link_keys()
        for link in list(self.node_tree.links):
            if self._link_to_key(link) not in desired_link_keys:
                self.node_tree.links.remove(link)

        # Create missing links
        for from_id, from_sock_id, to_id, to_sock_id in self._link_instructions:
            from_node = self._existing_nodes[from_id]
            to_node = self._existing_nodes[to_id]
            from_socket = self._resolve_socket(from_node.outputs, from_sock_id)
            to_socket = self._resolve_socket(to_node.inputs, to_sock_id)
            if not self._link_exists(from_socket, to_socket):
                self.node_tree.links.new(to_socket, from_socket)

    # ── Interface socket sync ────────────────────────────────────────

    def _sync_interface_sockets(self) -> None:
        """Ensure the node tree interface matches _socket_instructions in order."""
        if not self._socket_instructions:
            return

        # Sory socket instructions by in_out, Output first
        self._socket_instructions.sort(
            key=lambda x: x.in_out == 'OUTPUT', reverse=True)

        interface = self.node_tree.interface

        def _flat_sockets() -> list[bpy.types.NodeTreeInterfaceSocket]:
            return [item for item in interface.items_tree
                    if item.item_type == 'SOCKET']

        # Key: (name, in_out) — must be unique per declared socket
        SocketKey = tuple[str, str]
        desired_keys: dict[SocketKey, SocketInstruction] = {
            (instr.name, instr.in_out): instr
            for instr in self._socket_instructions
        }

        # Remove sockets that are no longer desired or have a changed type
        for sock in _flat_sockets():
            key: SocketKey = (sock.name, sock.in_out)
            instr = desired_keys.get(key)
            if instr is None or sock.bl_socket_idname != instr.socket_type:
                interface.remove(sock)

        # Rebuild lookup after removals
        existing: dict[SocketKey, bpy.types.NodeTreeInterfaceSocket] = {
            (s.name, s.in_out): s for s in _flat_sockets()
        }
        existing_before_creation: set[SocketKey] = set(existing.keys())

        # Create any still-missing sockets
        for instr in self._socket_instructions:
            key = (instr.name, instr.in_out)
            if key not in existing:
                sock = interface.new_socket(
                    instr.name, in_out=instr.in_out, socket_type=instr.socket_type
                )
                existing[key] = sock

        # Reorder sockets to match declaration order
        for idx, instr in enumerate(self._socket_instructions):
            key = (instr.name, instr.in_out)
            sock = existing[key]
            current = _flat_sockets()
            current_idx = next(i for i, s in enumerate(current) if s == sock)
            if current_idx != idx:
                interface.move(sock, idx)

        # Apply declared properties (Flexible values only on first creation)
        for instr in self._socket_instructions:
            key = (instr.name, instr.in_out)
            sock = existing[key]
            sock_is_new = key not in existing_before_creation
            for prop, value in instr.properties.items():
                is_flexible = isinstance(value, Flexible)
                if is_flexible and not sock_is_new:
                    continue
                setattr(sock, prop, value.value if is_flexible else value)

    # ── Hydration ────────────────────────────────────────────────────

    def _hydrate_existing_nodes(self) -> None:
        for node in self.node_tree.nodes:
            identifier = node.ps_identifier if node.ps_identifier else node.name
            self._existing_nodes[identifier] = node

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _resolve_socket(
        sockets: bpy.types.NodeInputs | bpy.types.NodeOutputs,
        socket_id: int | str,
    ) -> bpy.types.NodeSocket:
        if isinstance(socket_id, int):
            if 0 <= socket_id < len(sockets):
                return sockets[socket_id]
            raise ValueError(
                f"Socket index {socket_id} out of range (0..{len(sockets) - 1})"
            )
        for socket in sockets:
            if socket.name == socket_id:
                return socket
        names = [s.name for s in sockets]
        raise ValueError(
            f"Socket name '{socket_id}' not found; available: {names}")

    @staticmethod
    def _apply_node_properties(
        node: bpy.types.Node, properties: dict[str, Any],
        is_new: bool = True,
    ) -> None:
        for prop, value in properties.items():
            is_flexible = isinstance(value, Flexible)
            if is_flexible and not is_new:
                continue
            actual_value = value.value if is_flexible else value

            prop_rna = node.bl_rna.properties.get(prop)
            if prop_rna is None:
                continue
            if prop_rna.type == "POINTER" and isinstance(actual_value, str):
                collection = _get_data_collection(prop_rna.fixed_type)
                if collection:
                    ptr = collection.get(actual_value)
                    if ptr is not None:
                        setattr(node, prop, ptr)
            else:
                setattr(node, prop, actual_value)

    @staticmethod
    def _apply_socket_properties(
        sockets: bpy.types.NodeInputs | bpy.types.NodeOutputs,
        socket_specs: dict[int | str, dict[str, Any]],
        is_new: bool = True,
    ) -> None:
        for socket_id, props in socket_specs.items():
            socket = NodeTreeBuilder._resolve_socket(sockets, socket_id)
            for prop, value in props.items():
                is_flexible = isinstance(value, Flexible)
                if is_flexible and not is_new:
                    continue
                setattr(socket, prop, value.value if is_flexible else value)

    @staticmethod
    def _apply_props_recursive(
        idblock: Any, prop_dict: dict[str, Any],
        is_new: bool = True,
    ) -> None:
        for key, value in prop_dict.items():
            is_flexible = isinstance(value, Flexible)
            if is_flexible and not is_new:
                continue
            actual_value = value.value if is_flexible else value

            prop = idblock.bl_rna.properties.get(key)
            if prop is None:
                continue

            if not prop.is_readonly:
                if prop.type == "POINTER" and isinstance(actual_value, str):
                    collection = _get_data_collection(prop.fixed_type)
                    if collection:
                        ptr = collection.get(actual_value)
                        if ptr is not None:
                            setattr(idblock, key, ptr)
                else:
                    setattr(idblock, key, actual_value)

            elif prop.type == "COLLECTION":
                if not isinstance(actual_value, list):
                    continue
                collection = getattr(idblock, key)

                if hasattr(collection, "new"):
                    new_fn = collection.bl_rna.functions.get("new")
                    new_params = new_fn.parameters if new_fn else ()
                    if hasattr(collection, "clear"):
                        collection.clear()

                    for i, item in enumerate(actual_value):
                        used_keys: set[str] = set()
                        if i >= len(collection):
                            params = []
                            for p in new_params:
                                np = item.get(p.identifier)
                                if np is None:
                                    break
                                params.append(np)
                                used_keys.add(p.identifier)
                            obj = collection.new(*params)
                        else:
                            obj = collection[i]

                        for k, v in item.items():
                            if k not in used_keys:
                                setattr(obj, k, v)
                else:
                    for i, item in enumerate(actual_value):
                        if i < len(collection):
                            NodeTreeBuilder._apply_props_recursive(
                                collection[i], item, is_new
                            )

    def _link_to_key(self, link: bpy.types.NodeLink) -> LinkKey:
        from_id = self._get_node_identifier(link.from_node)
        to_id = self._get_node_identifier(link.to_node)
        from_idx = _socket_index(link.from_node.outputs, link.from_socket)
        to_idx = _socket_index(link.to_node.inputs, link.to_socket)
        return (from_id, from_idx, to_id, to_idx)

    def _build_desired_link_keys(self) -> set[LinkKey]:
        keys: set[LinkKey] = set()
        for from_id, from_sock_id, to_id, to_sock_id in self._link_instructions:
            from_node = self._existing_nodes.get(from_id)
            to_node = self._existing_nodes.get(to_id)
            if from_node is None or to_node is None:
                continue
            from_idx = _resolve_socket_index(from_node.outputs, from_sock_id)
            to_idx = _resolve_socket_index(to_node.inputs, to_sock_id)
            keys.add((from_id, from_idx, to_id, to_idx))
        return keys

    @staticmethod
    def _link_exists(
        from_socket: bpy.types.NodeSocket, to_socket: bpy.types.NodeSocket
    ) -> bool:
        for link in from_socket.links:
            if link.to_socket == to_socket:
                return True
        return False

    def _get_node_identifier(self, node: bpy.types.Node) -> str:
        return node.ps_identifier if node.ps_identifier else node.name

    def arrange_nodes(self) -> None:
        pass

    def _get_node_distance(
        self, node1: bpy.types.Node, node2: bpy.types.Node
    ) -> float:
        """Get distance in x axis between two nodes."""
        node1_x = node1.location[0]
        node2_x = node2.location[0]
        if node1_x < node2_x:
            node1_x += node1.dimensions.x
        else:
            node2_x += node2.dimensions.x
        return abs(node1_x - node2_x)


# ── Module-level helpers ─────────────────────────────────────────────

_bpy_type_to_data_collection: dict[type, str] = {}


def _get_data_collection(id_type):
    """Return the bpy.data collection that stores objects of *id_type*, or None."""
    if not _bpy_type_to_data_collection:
        for prop in bpy.data.bl_rna.properties:
            if prop.type == "COLLECTION":
                _bpy_type_to_data_collection[prop.fixed_type] = prop.identifier
    result = None
    while result is None:
        result = _bpy_type_to_data_collection.get(id_type)
        id_type = id_type.base
        if id_type is None:
            return None
    return getattr(bpy.data, result)


def _socket_index(
    sockets: bpy.types.NodeInputs | bpy.types.NodeOutputs,
    target: bpy.types.NodeSocket,
) -> int:
    for i, s in enumerate(sockets):
        if s == target:
            return i
    raise ValueError(f"{target!r} not found in sockets")


def _resolve_socket_index(
    sockets: bpy.types.NodeInputs | bpy.types.NodeOutputs,
    socket_id: int | str,
) -> int:
    if isinstance(socket_id, int):
        return socket_id
    for i, s in enumerate(sockets):
        if s.name == socket_id:
            return i
    raise ValueError(f"Socket name '{socket_id}' not found")


# ── Registration ─────────────────────────────────────────────────────


def register():
    bpy.types.Node.ps_identifier = StringProperty(
        name="Identifier", default="")


def unregister():
    del bpy.types.Node.ps_identifier
