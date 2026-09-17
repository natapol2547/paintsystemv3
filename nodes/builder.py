from __future__ import annotations

import logging
import struct
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import bpy

log = logging.getLogger(__name__)

# Custom ID property that tags each artifact node with the identifier the
# builder emitted it under, so rebuilds can reuse nodes across renames.
IDENTIFIER_KEY = "ps_identifier"


def node_identifier(node) -> str:
    return node.get(IDENTIFIER_KEY) or node.name


# ── Value comparison ─────────────────────────────────────────────────

_pack_float32 = struct.Struct('f').pack
_unpack_float32 = struct.Struct('f').unpack

# Stands in for a property that cannot be read back, so it is always written.
_UNREADABLE = object()


def _as_float32(value: float) -> float:
    """*value* as RNA would store it: rounded to float32, widened back."""
    try:
        return _unpack_float32(_pack_float32(value))[0]
    except (OverflowError, struct.error):
        return value


def _same_id(current: Any, desired: Any) -> bool:
    """Whether two datablock pointers refer to the same datablock.

    Two wrappers of one datablock are different Python objects, so they are
    compared by address.
    """
    if current is None or desired is None:
        return current is None and desired is None
    if not (isinstance(current, bpy.types.ID) and isinstance(desired, bpy.types.ID)):
        return False
    return current.as_pointer() == desired.as_pointer()


def same_value(current: Any, desired: Any) -> bool:
    """Whether writing *desired* over *current* would store the same thing.

    RNA keeps floats as float32, so *desired* is compared rounded to float32:
    ``0.1`` reads back as ``0.10000000149011612`` and would otherwise look
    different on every build. A difference RNA *can* store always compares
    unequal. Datablocks compare by identity, sequences element by element.

    Answering "no" only costs a redundant write; answering "yes" wrongly
    leaves the artifact stale for good, so anything unrecognised is "no".
    """
    if isinstance(desired, str):
        return isinstance(current, str) and current == desired
    if isinstance(desired, (bool, int, float)):
        if not isinstance(current, (bool, int, float)):
            return False
        if isinstance(current, float) or isinstance(desired, float):
            return current == _as_float32(desired)
        return current == desired
    if desired is None:
        return current is None
    if isinstance(desired, bpy.types.ID) or isinstance(current, bpy.types.ID):
        return _same_id(current, desired)
    if isinstance(desired, (set, frozenset)):
        try:
            return set(current) == set(desired)
        except TypeError:
            return False
    try:
        desired_items = list(desired)
        current_items = list(current)
    except TypeError:
        return False
    if len(current_items) != len(desired_items):
        return False
    return all(same_value(c, d) for c, d in zip(current_items, desired_items))


# ── Layout constants ─────────────────────────────────────────────────
H_MARGIN = 50.0       # horizontal gap between columns
V_MARGIN = 30.0       # vertical gap between siblings in a column
HEADER_HEIGHT = 35.0  # approximate node header height in px
SOCKET_HEIGHT = 22.0  # approximate per-socket row height in px
DEFAULT_NODE_WIDTH = 140.0


@dataclass
class Flexible:
    """Wrap a value to apply it only when the node/socket is first created."""
    value: Any


@dataclass
class BuildStats:
    """What a build actually changed in the tree.

    Counted on every build so a test or the profiler can tell a patch from a
    rebuild; nothing in the add-on branches on it. ``values_written`` counts
    the RNA writes that happened, not the ones that were asked for.
    """
    nodes_created: int = 0
    values_written: int = 0
    links_created: int = 0
    links_removed: int = 0
    arranged: bool = False


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
        self._newly_created: set[str] = set()
        # (node pointer, is_input) -> (sockets, name -> socket), filled during
        # the link phase only. See _socket_by_id.
        self._socket_cache: dict[
            tuple[int, bool],
            tuple[list[bpy.types.NodeSocket], dict[str, bpy.types.NodeSocket]],
        ] = {}
        self.stats = BuildStats()
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
        # bl_use_group_interface exists from Blender 4.3; earlier versions
        # give every node tree an interface.
        if not getattr(self.node_tree, "bl_use_group_interface", True):
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

    def build(self, *, arrange: bool = True) -> None:
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
                node[IDENTIFIER_KEY] = identifier
                self._existing_nodes[identifier] = node
                self._newly_created.add(identifier)
                self.stats.nodes_created += 1

            self._apply_node_properties(node, instr.properties, is_new)
            self._apply_socket_properties(node.inputs, instr.inputs, is_new)
            self._apply_socket_properties(node.outputs, instr.outputs, is_new)

            for attr_name, props in instr.specials.items():
                sub_obj = getattr(node, attr_name, None)
                if sub_obj is not None:
                    self._apply_props_recursive(sub_obj, props, is_new)
                else:
                    log.warning("special %s not found on node %s", attr_name, node.name)

        self._sync_links()

        if arrange:
            self.arrange_nodes()

    # ── Link sync ────────────────────────────────────────────────────

    def _sync_links(self) -> None:
        """Make the tree's links exactly the declared ones.

        Links are matched by the pair of socket addresses they connect, never
        by ``ps_identifier``: the tag is copied when a user duplicates an
        artifact node by hand, so identifier matching would mistake the copy's
        link for the real one and leave the real link uncreated.

        Addresses also keep this off ``NodeSocket.links``, which is a Python
        property that scans every link of the tree on each read. One pass over
        ``node_tree.links`` decides every removal, and creation then only
        looks up a socket address in a dict.
        """
        # The cache may only be filled now: up to here a bl_idname change
        # could still recreate a node, which frees its sockets.
        self._socket_cache.clear()

        # to socket address -> from socket addresses, for the declared links
        # and for the ones the tree already has.
        wanted: dict[int, set[int]] = {}
        declared: list[tuple[bpy.types.NodeSocket, bpy.types.NodeSocket]] = []
        for from_id, from_sock_id, to_id, to_sock_id in self._link_instructions:
            from_socket = self._socket_by_id(
                self._existing_nodes[from_id], False, from_sock_id)
            to_socket = self._socket_by_id(
                self._existing_nodes[to_id], True, to_sock_id)
            declared.append((from_socket, to_socket))
            wanted.setdefault(to_socket.as_pointer(), set()).add(
                from_socket.as_pointer())

        present: dict[int, set[int]] = {}
        for link in list(self.node_tree.links):
            to_pointer = link.to_socket.as_pointer()
            from_pointer = link.from_socket.as_pointer()
            if from_pointer in wanted.get(to_pointer, ()):
                present.setdefault(to_pointer, set()).add(from_pointer)
            else:
                self.node_tree.links.remove(link)
                self.stats.links_removed += 1

        for from_socket, to_socket in declared:
            to_pointer = to_socket.as_pointer()
            from_pointer = from_socket.as_pointer()
            linked = present.get(to_pointer)
            if linked is not None and from_pointer in linked:
                continue
            self.node_tree.links.new(to_socket, from_socket)
            self.stats.links_created += 1
            if linked is None or not to_socket.is_multi_input:
                # An input that holds one link drops what it held when the new
                # link lands on it, so the replaced pair is gone.
                present[to_pointer] = {from_pointer}
            else:
                linked.add(from_pointer)

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
            # socket_type is the base type; bl_socket_idname includes the subtype
            # (e.g. NodeSocketFloatFactor) and would churn sockets on every build.
            existing_type = getattr(sock, 'socket_type', sock.bl_socket_idname)
            if instr is None or existing_type != instr.socket_type:
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
                self._write_if_changed(
                    sock, prop, value.value if is_flexible else value)

    # ── Hydration ────────────────────────────────────────────────────

    def _hydrate_existing_nodes(self) -> None:
        for node in self.node_tree.nodes:
            identifier = node_identifier(node)
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

    def _write(self, owner: Any, prop: str, value: Any) -> None:
        setattr(owner, prop, value)
        self.stats.values_written += 1

    def _write_if_changed(self, owner: Any, prop: str, value: Any) -> None:
        """Write *value* onto *owner* unless RNA already holds it.

        Every RNA write on a node tree tags the tree and the materials using
        it for an update, which costs about as much as the tree is large. A
        compile rewrites the whole artifact, so on an unchanged tree those
        no-op writes were most of the cost of an edit.

        A property that cannot be read is written anyway, so this never turns
        a write that used to work into an error.
        """
        current = getattr(owner, prop, _UNREADABLE)
        if current is _UNREADABLE or not same_value(current, value):
            self._write(owner, prop, value)

    def _apply_node_properties(
        self, node: bpy.types.Node, properties: dict[str, Any],
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
                        self._write_if_changed(node, prop, ptr)
            else:
                self._write_if_changed(node, prop, actual_value)

    def _apply_socket_properties(
        self,
        sockets: bpy.types.NodeInputs | bpy.types.NodeOutputs,
        socket_specs: dict[int | str, dict[str, Any]],
        is_new: bool = True,
    ) -> None:
        for socket_id, props in socket_specs.items():
            socket = self._resolve_socket(sockets, socket_id)
            for prop, value in props.items():
                is_flexible = isinstance(value, Flexible)
                if is_flexible and not is_new:
                    continue
                self._write_if_changed(
                    socket, prop, value.value if is_flexible else value)

    def _apply_props_recursive(
        self, idblock: Any, prop_dict: dict[str, Any],
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
                            self._write_if_changed(idblock, key, ptr)
                else:
                    self._write_if_changed(idblock, key, actual_value)

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

                        # No comparison here: the collection was just cleared,
                        # so every element is new and every value is a change.
                        for k, v in item.items():
                            if k not in used_keys:
                                self._write(obj, k, v)
                else:
                    for i, item in enumerate(actual_value):
                        if i < len(collection):
                            self._apply_props_recursive(
                                collection[i], item, is_new
                            )

    def _socket_by_id(
        self, node: bpy.types.Node, is_input: bool, socket_id: int | str,
    ) -> bpy.types.NodeSocket:
        """Like ``_resolve_socket``, but keeps *node*'s socket list around.

        A declared link names each of its sockets by index or by name, and the
        same node is named by many of them, so building the lookup once per
        node turns the name scans into dict reads. Only valid while the socket
        lists cannot change, which is why it is confined to the link phase.
        """
        key = (node.as_pointer(), is_input)
        cached = self._socket_cache.get(key)
        if cached is None:
            sockets = list(node.inputs if is_input else node.outputs)
            by_name: dict[str, bpy.types.NodeSocket] = {}
            for socket in sockets:
                # First match wins, as a scan over the collection would.
                by_name.setdefault(socket.name, socket)
            cached = (sockets, by_name)
            self._socket_cache[key] = cached
        sockets, by_name = cached
        if isinstance(socket_id, int):
            if 0 <= socket_id < len(sockets):
                return sockets[socket_id]
            raise ValueError(
                f"Socket index {socket_id} out of range (0..{len(sockets) - 1})"
            )
        socket = by_name.get(socket_id)
        if socket is None:
            names = [s.name for s in sockets]
            raise ValueError(
                f"Socket name '{socket_id}' not found; available: {names}")
        return socket

    # ── Arrangement ──────────────────────────────────────────────────

    def arrange_nodes(self) -> None:
        """Lay out nodes left-to-right based on link topology.

        Sinks (rightmost) anchor at x=0; predecessors flow left. Only newly
        created nodes get repositioned; pre-existing positions are preserved
        unless they overlap a new node, in which case the smaller upstream or
        downstream cluster (by node count) shifts to make room.
        """
        if not self._newly_created or not self._existing_nodes:
            return

        self.stats.arranged = True
        successors, predecessors = self._build_adjacency()
        new_ids = set(self._newly_created)
        positioned_ids = set(self._existing_nodes) - new_ids

        if not positioned_ids:
            self._full_layout(successors, predecessors)
        else:
            self._incremental_layout(
                successors, predecessors, new_ids, positioned_ids,
            )

    def _build_adjacency(
        self,
    ) -> tuple[dict[str, list], dict[str, list]]:
        """Build (successors, predecessors) maps from link instructions.

        Skips self-loops and links whose endpoints aren't in _existing_nodes.
        """
        successors: dict[str, list] = defaultdict(list)
        predecessors: dict[str, list] = defaultdict(list)
        for from_id, from_sock, to_id, to_sock in self._link_instructions:
            if from_id == to_id:
                continue
            if (
                from_id not in self._existing_nodes
                or to_id not in self._existing_nodes
            ):
                continue
            successors[from_id].append((from_sock, to_id, to_sock))
            predecessors[to_id].append((to_sock, from_id, from_sock))
        return dict(successors), dict(predecessors)

    # ── Geometry helpers ─────────────────────────────────────────

    def _node_height(self, node: bpy.types.Node) -> float:
        """Estimate node height. dimensions can be (0,0) right after creation."""
        try:
            dim_y = float(node.dimensions.y)
        except (AttributeError, TypeError):
            dim_y = 0.0
        if dim_y > 0:
            return dim_y
        socket_count = len(node.inputs) + len(node.outputs)
        return HEADER_HEIGHT + max(1, socket_count) * SOCKET_HEIGHT

    def _node_width(self, node: bpy.types.Node) -> float:
        w = float(getattr(node, "width", DEFAULT_NODE_WIDTH)
                  or DEFAULT_NODE_WIDTH)
        return w if w > 0 else DEFAULT_NODE_WIDTH

    def _node_bbox(
        self, node: bpy.types.Node,
    ) -> tuple[float, float, float, float]:
        """Return (left, top, right, bottom). Y grows upward, so bottom < top."""
        left = float(node.location.x)
        top = float(node.location.y)
        width = self._node_width(node)
        height = self._node_height(node)
        return (left, top, left + width, top - height)

    def _socket_y(
        self, node: bpy.types.Node, socket_idx: int, is_input: bool,
    ) -> float:
        """Approximate world Y of a socket.

        Blender visually shows outputs above inputs regardless of declaration
        order; we mirror that so socket-to-socket alignment is meaningful.
        """
        out_count = len(node.outputs)
        if is_input:
            offset = HEADER_HEIGHT + (out_count + socket_idx) * SOCKET_HEIGHT
        else:
            offset = HEADER_HEIGHT + socket_idx * SOCKET_HEIGHT
        return float(node.location.y) - offset

    @staticmethod
    def _rects_overlap(
        a: tuple[float, float, float, float],
        b: tuple[float, float, float, float],
    ) -> bool:
        """AABB overlap test. Boxes are (left, top, right, bottom), Y up."""
        a_left, a_top, a_right, a_bottom = a
        b_left, b_top, b_right, b_bottom = b
        if a_right <= b_left or b_right <= a_left:
            return False
        if a_top <= b_bottom or b_top <= a_bottom:
            return False
        return True

    def _socket_idx(
        self,
        sockets: bpy.types.bpy_prop_collection,
        sock_id: int | str,
    ) -> int:
        if isinstance(sock_id, int):
            return sock_id
        for i, s in enumerate(sockets):
            if s.name == sock_id:
                return i
        return 0

    # ── Full layout (every node is newly created) ────────────────

    def _compute_depths(
        self, successors: dict[str, list],
    ) -> dict[str, int]:
        """Depth-from-sink. Sinks have depth 0; cycles default to 0."""
        depths: dict[str, int] = {}

        def _depth(nid: str, stack: set[str]) -> int:
            if nid in depths:
                return depths[nid]
            if nid in stack:
                depths[nid] = 0
                return 0
            stack.add(nid)
            succs = successors.get(nid, [])
            if not succs:
                d = 0
            else:
                d = 1 + max(_depth(t[1], stack) for t in succs)
            stack.discard(nid)
            depths[nid] = d
            return d

        for nid in self._existing_nodes:
            _depth(nid, set())
        return depths

    def _connected_components(
        self,
        successors: dict[str, list],
        predecessors: dict[str, list],
    ) -> list[set[str]]:
        """Group nodes into undirected connected components."""
        visited: set[str] = set()
        components: list[set[str]] = []
        for start in self._existing_nodes:
            if start in visited:
                continue
            comp: set[str] = set()
            queue = [start]
            while queue:
                nid = queue.pop()
                if nid in comp:
                    continue
                comp.add(nid)
                for _, neighbor, _ in successors.get(nid, []):
                    if neighbor not in comp:
                        queue.append(neighbor)
                for _, neighbor, _ in predecessors.get(nid, []):
                    if neighbor not in comp:
                        queue.append(neighbor)
            visited |= comp
            components.append(comp)
        return components

    def _full_layout(
        self,
        successors: dict[str, list],
        predecessors: dict[str, list],
    ) -> None:
        depths = self._compute_depths(successors)
        components = self._connected_components(successors, predecessors)

        insertion_order = list(self._node_instructions.keys())
        order_index = {nid: i for i, nid in enumerate(insertion_order)}

        def _order_key(nid: str) -> int:
            return order_index.get(nid, len(insertion_order))

        components.sort(key=lambda c: min(_order_key(n) for n in c))

        max_depth = max(depths.values(), default=0)
        col_max_width: dict[int, float] = {}
        for d in range(max_depth + 1):
            widths = [
                self._node_width(self._existing_nodes[nid])
                for nid, dd in depths.items()
                if dd == d
            ]
            col_max_width[d] = max(widths) if widths else DEFAULT_NODE_WIDTH

        col_right_x: dict[int, float] = {0: 0.0}
        for d in range(1, max_depth + 1):
            col_right_x[d] = col_right_x[d - 1] - \
                col_max_width[d - 1] - H_MARGIN

        for nid, d in depths.items():
            node = self._existing_nodes[nid]
            node.location.x = col_right_x[d] - self._node_width(node)

        y_cursor = 0.0
        for component in components:
            placed: set[str] = set()
            column_occupied: dict[int,
                                  list[tuple[float, float]]] = defaultdict(list)

            sinks = sorted(
                [nid for nid in component if depths.get(nid, 0) == 0],
                key=_order_key,
            )
            for sink_id in sinks:
                if sink_id in placed:
                    continue
                sink_node = self._existing_nodes[sink_id]
                self._place_in_column(
                    sink_node, depth=0, target_top=y_cursor,
                    column_occupied=column_occupied,
                )
                placed.add(sink_id)
                self._place_predecessors_recursive(
                    sink_id, placed, column_occupied, depths, predecessors,
                )
                bottoms = [
                    b for intervals in column_occupied.values()
                    for (_, b) in intervals
                ]
                if bottoms:
                    y_cursor = min(bottoms) - V_MARGIN

            bottoms = [
                b for intervals in column_occupied.values()
                for (_, b) in intervals
            ]
            if bottoms:
                y_cursor = min(bottoms) - V_MARGIN

    def _place_in_column(
        self,
        node: bpy.types.Node,
        depth: int,
        target_top: float,
        column_occupied: dict[int, list[tuple[float, float]]],
    ) -> float:
        """Set node.y so its top is at target_top, shifting down past any
        occupied interval in the same column. Records the new interval."""
        height = self._node_height(node)
        top = target_top
        max_passes = max(8, len(column_occupied[depth]) * 2)
        for _ in range(max_passes):
            bottom = top - height
            overlap = False
            for (other_top, other_bottom) in column_occupied[depth]:
                if not (bottom >= other_top or top <= other_bottom):
                    top = other_bottom - V_MARGIN
                    overlap = True
                    break
            if not overlap:
                break
        bottom = top - height
        node.location.y = top
        column_occupied[depth].append((top, bottom))
        return top

    def _place_predecessors_recursive(
        self,
        node_id: str,
        placed: set[str],
        column_occupied: dict[int, list[tuple[float, float]]],
        depths: dict[str, int],
        predecessors: dict[str, list],
    ) -> None:
        node = self._existing_nodes[node_id]
        preds = predecessors.get(node_id, [])

        annotated = []
        for to_sock, from_id, from_sock in preds:
            to_idx = self._socket_idx(node.inputs, to_sock)
            from_idx = self._socket_idx(
                self._existing_nodes[from_id].outputs, from_sock,
            )
            annotated.append((to_idx, from_id, from_idx))
        annotated.sort(key=lambda t: t[0])

        for to_idx, from_id, from_idx in annotated:
            if from_id in placed:
                continue
            pred_node = self._existing_nodes[from_id]
            pred_depth = depths.get(from_id, 0)

            node_input_y = self._socket_y(node, to_idx, is_input=True)
            pred_out_offset = (
                self._socket_y(pred_node, from_idx, is_input=False)
                - float(pred_node.location.y)
            )
            target_top = node_input_y - pred_out_offset

            self._place_in_column(
                pred_node, pred_depth, target_top, column_occupied,
            )
            placed.add(from_id)
            self._place_predecessors_recursive(
                from_id, placed, column_occupied, depths, predecessors,
            )

    # ── Incremental layout ───────────────────────────────────────

    def _incremental_layout(
        self,
        successors: dict[str, list],
        predecessors: dict[str, list],
        new_ids: set[str],
        positioned_ids: set[str],
    ) -> None:
        # For each new node, compute (anchor, hop_depth) in each direction
        # plus the IMMEDIATE neighbor on the topmost socket (used for Y
        # alignment). Chain depths let us spread chained new nodes across
        # multiple columns instead of piling them up at the same x.
        meta: dict[str, dict] = {}
        for nid in new_ids:
            up_anchor, up_depth = self._chain_depth_and_anchor(
                nid, predecessors, new_ids, positioned_ids,
            )
            down_anchor, down_depth = self._chain_depth_and_anchor(
                nid, successors, new_ids, positioned_ids,
            )
            up_neighbor = self._immediate_neighbor(
                nid, predecessors, is_input_side=True,
            )
            down_neighbor = self._immediate_neighbor(
                nid, successors, is_input_side=False,
            )
            meta[nid] = {
                'up_anchor': up_anchor, 'up_depth': up_depth,
                'down_anchor': down_anchor, 'down_depth': down_depth,
                'up_neighbor': up_neighbor, 'down_neighbor': down_neighbor,
            }

        # Group by (up_anchor, down_anchor) for make-room budgeting.
        groups: dict[tuple[str | None, str | None],
                     list[str]] = defaultdict(list)
        for nid in new_ids:
            m = meta[nid]
            groups[(m['up_anchor'], m['down_anchor'])].append(nid)

        keys = sorted(groups.keys(), key=lambda k: (k == (None, None),))

        for key in keys:
            up_id, down_id = key
            members = groups[key]
            if up_id is None and down_id is None:
                self._place_orphan_group(members, positioned_ids)
            else:
                self._place_insertion_group(
                    members, up_id, down_id, meta,
                    successors, predecessors, positioned_ids,
                )
            positioned_ids.update(members)

    def _chain_depth_and_anchor(
        self,
        start: str,
        adjacency: dict[str, list],
        new_ids: set[str],
        positioned_ids: set[str],
    ) -> tuple[str | None, int | None]:
        """BFS through new nodes to find the closest positioned anchor.

        Returns (anchor_id, hop_count) where hop_count is 1 for a direct
        positioned neighbor, 2 for one new-node hop away, etc.
        Returns (None, None) if no positioned node is reachable.
        """
        visited: set[str] = {start}
        queue: list[tuple[str, int]] = [(start, 0)]
        while queue:
            cur, depth = queue.pop(0)
            for _, other_id, _ in adjacency.get(cur, []):
                if other_id in positioned_ids:
                    return (other_id, depth + 1)
                if other_id in new_ids and other_id not in visited:
                    visited.add(other_id)
                    queue.append((other_id, depth + 1))
        return (None, None)

    def _immediate_neighbor(
        self,
        nid: str,
        adjacency: dict[str, list],
        is_input_side: bool,
    ) -> tuple[str, int, int] | None:
        """Return (neighbor_id, local_idx_on_nid, remote_idx_on_neighbor)
        for the connection on the topmost socket of `nid`. Does NOT walk
        through other nodes — picks the direct neighbor on socket index 0
        (or lowest connected index).
        """
        node = self._existing_nodes[nid]
        sockets = node.inputs if is_input_side else node.outputs

        edges = []
        for local_sock, other_id, remote_sock in adjacency.get(nid, []):
            local_idx = self._socket_idx(sockets, local_sock)
            edges.append((local_idx, other_id, remote_sock))
        if not edges:
            return None
        edges.sort(key=lambda t: t[0])
        local_idx, other_id, remote_sock = edges[0]
        other_sockets = (
            self._existing_nodes[other_id].outputs
            if is_input_side
            else self._existing_nodes[other_id].inputs
        )
        remote_idx = self._socket_idx(other_sockets, remote_sock)
        return (other_id, local_idx, remote_idx)

    def _place_insertion_group(
        self,
        members: list[str],
        up_id: str | None,
        down_id: str | None,
        meta: dict[str, dict],
        successors: dict[str, list],
        predecessors: dict[str, list],
        positioned_ids: set[str],
    ) -> None:
        # column_index from the up side when up_anchor exists, otherwise
        # from the down side. Members with the same column_index are true
        # siblings; different indices go in different columns.
        def _col_idx(nid: str) -> int:
            m = meta[nid]
            if up_id is not None and m['up_depth'] is not None:
                return m['up_depth']
            if down_id is not None and m['down_depth'] is not None:
                return m['down_depth']
            return 1

        columns: dict[int, list[str]] = defaultdict(list)
        for nid in members:
            columns[_col_idx(nid)].append(nid)
        chain_length = max(columns.keys()) if columns else 1

        col_max_width: dict[int, float] = {
            k: max(
                self._node_width(self._existing_nodes[nid]) for nid in nids
            )
            for k, nids in columns.items()
        }
        # Any column index missing widths (shouldn't happen, defensive)
        for k in range(1, chain_length + 1):
            col_max_width.setdefault(k, DEFAULT_NODE_WIDTH)

        # Make-room if both anchors exist and the gap is too small.
        if up_id is not None and down_id is not None:
            up_node = self._existing_nodes[up_id]
            down_node = self._existing_nodes[down_id]
            up_right = float(up_node.location.x) + self._node_width(up_node)
            down_left = float(down_node.location.x)
            available = down_left - up_right
            needed = (
                sum(col_max_width[k] for k in range(1, chain_length + 1))
                + (chain_length + 1) * H_MARGIN
            )
            if available < needed:
                deficit = needed - available
                self._make_room(
                    up_id, down_id, deficit, positioned_ids,
                    successors, predecessors,
                )

        # Compute X per column.
        col_x: dict[int, float] = {}
        if up_id is not None:
            up_node = self._existing_nodes[up_id]
            cursor = (
                float(up_node.location.x)
                + self._node_width(up_node) + H_MARGIN
            )
            for k in range(1, chain_length + 1):
                col_x[k] = cursor
                cursor += col_max_width[k] + H_MARGIN
        else:
            # down-anchor only: right-align columns leftward
            down_node = self._existing_nodes[down_id]
            cursor = float(down_node.location.x) - H_MARGIN
            for k in range(1, chain_length + 1):
                # cursor is the right edge of column k
                col_x[k] = cursor - col_max_width[k]
                cursor = col_x[k] - H_MARGIN

        # Place column by column. When up_id exists we walk left→right
        # (k=1..chain_length) so each member's up_neighbor is already placed
        # by the time we reach it. When only down_id exists we walk
        # right→left (k=1..chain_length) so the down_neighbor is placed first.
        column_intervals: dict[int,
                               list[tuple[float, float]]] = defaultdict(list)
        all_placed_members: list[str] = []
        for k in range(1, chain_length + 1):
            col_members = columns.get(k, [])
            col_members.sort(
                key=lambda nid: self._sibling_sort_key(nid, meta, up_id))

            for nid in col_members:
                node = self._existing_nodes[nid]
                # X: left-align inside the column slot
                node.location.x = col_x[k]
                # Y: align to immediate neighbor on the anchor side
                target_top = self._neighbor_aligned_y(nid, meta, up_id)

                height = self._node_height(node)
                max_passes = max(8, len(column_intervals[k]) * 2)
                for _ in range(max_passes):
                    bottom = target_top - height
                    hit = False
                    for (other_top, other_bottom) in column_intervals[k]:
                        if not (bottom >= other_top
                                or target_top <= other_bottom):
                            target_top = other_bottom - V_MARGIN
                            hit = True
                            break
                    if not hit:
                        break
                node.location.y = target_top
                column_intervals[k].append((target_top, target_top - height))
                all_placed_members.append(nid)

        self._resolve_overlaps_for_group(
            all_placed_members, up_id, down_id,
            positioned_ids, successors, predecessors,
        )

    def _sibling_sort_key(
        self,
        nid: str,
        meta: dict[str, dict],
        up_id: str | None,
    ) -> int:
        """Sort siblings in a column by the socket index on the shared
        immediate neighbor — topmost socket wins."""
        m = meta[nid]
        if up_id is not None and m['up_neighbor'] is not None:
            return m['up_neighbor'][2]  # remote idx on the neighbor
        if m['down_neighbor'] is not None:
            return m['down_neighbor'][2]
        return 0

    def _neighbor_aligned_y(
        self,
        nid: str,
        meta: dict[str, dict],
        up_id: str | None,
    ) -> float:
        """Compute target top-Y for a new node by aligning the socket that
        connects to its IMMEDIATE neighbor (positioned or just-placed new)
        to that neighbor's matching socket."""
        node = self._existing_nodes[nid]
        m = meta[nid]
        # Prefer up-side alignment when up_anchor exists for this group;
        # otherwise use down-side.
        if up_id is not None and m['up_neighbor'] is not None:
            neighbor_id, local_idx, remote_idx = m['up_neighbor']
            neighbor = self._existing_nodes[neighbor_id]
            neighbor_socket_y = self._socket_y(
                neighbor, remote_idx, is_input=False,
            )
            new_socket_offset = (
                self._socket_y(node, local_idx, is_input=True)
                - float(node.location.y)
            )
            return neighbor_socket_y - new_socket_offset
        if m['down_neighbor'] is not None:
            neighbor_id, local_idx, remote_idx = m['down_neighbor']
            neighbor = self._existing_nodes[neighbor_id]
            neighbor_socket_y = self._socket_y(
                neighbor, remote_idx, is_input=True,
            )
            new_socket_offset = (
                self._socket_y(node, local_idx, is_input=False)
                - float(node.location.y)
            )
            return neighbor_socket_y - new_socket_offset
        return 0.0

    def _make_room(
        self,
        up_id: str,
        down_id: str,
        deficit: float,
        positioned_ids: set[str],
        successors: dict[str, list],
        predecessors: dict[str, list],
    ) -> None:
        """Shift either the up-cluster left or the down-cluster right by `deficit`.

        Picks the smaller cluster (by node count) to minimize displacement.
        """
        up_cluster = self._reachable(
            up_id, predecessors, positioned_ids, exclude={down_id},
        )
        down_cluster = self._reachable(
            down_id, successors, positioned_ids, exclude={up_id},
        )

        if len(up_cluster) <= len(down_cluster):
            for nid in up_cluster:
                self._existing_nodes[nid].location.x -= deficit
        else:
            for nid in down_cluster:
                self._existing_nodes[nid].location.x += deficit

    def _reachable(
        self,
        start: str,
        adjacency: dict[str, list],
        universe: set[str],
        exclude: set[str],
    ) -> set[str]:
        """BFS reachability over `adjacency`, restricted to `universe`."""
        result: set[str] = set()
        if start not in universe or start in exclude:
            return result
        queue = [start]
        while queue:
            nid = queue.pop()
            if nid in result or nid in exclude or nid not in universe:
                continue
            result.add(nid)
            for _, neighbor, _ in adjacency.get(nid, []):
                if (
                    neighbor not in result
                    and neighbor in universe
                    and neighbor not in exclude
                ):
                    queue.append(neighbor)
        return result

    def _resolve_overlaps_for_group(
        self,
        group_members: list[str],
        up_id: str | None,
        down_id: str | None,
        positioned_ids: set[str],
        successors: dict[str, list],
        predecessors: dict[str, list],
    ) -> None:
        """Push positioned nodes (other than the up/down anchors) out of the
        way of the just-placed group. Pre-existing overlaps between two
        already-positioned nodes are left alone — only overlaps introduced by
        the new group are resolved.
        """
        excluded = set(group_members)
        if up_id is not None:
            excluded.add(up_id)
        if down_id is not None:
            excluded.add(down_id)

        def _group_bbox() -> tuple[float, float, float, float]:
            boxes = [
                self._node_bbox(self._existing_nodes[nid])
                for nid in group_members
            ]
            return (
                min(b[0] for b in boxes),
                max(b[1] for b in boxes),
                max(b[2] for b in boxes),
                min(b[3] for b in boxes),
            )

        max_iter = max(8, len(positioned_ids) * 2)
        for _ in range(max_iter):
            group_box = _group_bbox()
            worst: str | None = None
            worst_overlap = 0.0
            for nid in positioned_ids:
                if nid in excluded:
                    continue
                other_box = self._node_bbox(self._existing_nodes[nid])
                if not self._rects_overlap(group_box, other_box):
                    continue
                h_overlap = (
                    min(group_box[2], other_box[2])
                    - max(group_box[0], other_box[0])
                )
                if h_overlap > worst_overlap:
                    worst = nid
                    worst_overlap = h_overlap
            if worst is None:
                return

            other_box = self._node_bbox(self._existing_nodes[worst])
            group_center_x = (group_box[0] + group_box[2]) / 2
            other_center_x = (other_box[0] + other_box[2]) / 2
            shift_amount = worst_overlap + H_MARGIN

            if other_center_x <= group_center_x:
                # Worst is on (or aligned with) the left side → shift left
                cluster = self._reachable(
                    worst, predecessors, positioned_ids,
                    exclude=set(group_members),
                )
                for cid in cluster:
                    self._existing_nodes[cid].location.x -= shift_amount
            else:
                cluster = self._reachable(
                    worst, successors, positioned_ids,
                    exclude=set(group_members),
                )
                for cid in cluster:
                    self._existing_nodes[cid].location.x += shift_amount

        log.debug("arrange_nodes: overlap resolution did not converge after %d iterations",
                  max_iter)

    def _place_orphan_group(
        self, members: list[str], positioned_ids: set[str],
    ) -> None:
        """Place new nodes with no graph neighbors in a column to the right
        of every positioned node, stacked vertically."""
        if positioned_ids:
            rightmost = max(
                float(self._existing_nodes[nid].location.x)
                + self._node_width(self._existing_nodes[nid])
                for nid in positioned_ids
            )
            x = rightmost + H_MARGIN
        else:
            x = 0.0
        y = 0.0
        for nid in members:
            node = self._existing_nodes[nid]
            node.location.x = x
            node.location.y = y
            y -= self._node_height(node) + V_MARGIN


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
