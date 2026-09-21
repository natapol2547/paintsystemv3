from __future__ import annotations

import logging
import struct
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import bpy

from .profile import phase

if TYPE_CHECKING:
    # Only for type hints. A real import would be circular, because
    # ``ir`` imports this module.
    from .ir import IR, IRNode, IRSocket

log = logging.getLogger(__name__)

# Custom property that stores each artifact node's IR identifier. Rebuilds
# find nodes by it, so a node still matches after it is renamed.
IDENTIFIER_KEY = "ps_identifier"


def node_identifier(node) -> str:
    return node.get(IDENTIFIER_KEY) or node.name


# ── Value comparison ─────────────────────────────────────────────────

_pack_float32 = struct.Struct('f').pack
_unpack_float32 = struct.Struct('f').unpack

# Marks a property that cannot be read back. Such a property is always
# written.
_UNREADABLE = object()


def _as_float32(value: float) -> float:
    """Round *value* to float32 precision, the way Blender stores floats."""
    try:
        return _unpack_float32(_pack_float32(value))[0]
    except (OverflowError, struct.error):
        return value


def _same_id(current: Any, desired: Any) -> bool:
    """True when both values point to the same datablock, or both are None.

    Two Python wrappers of one datablock are different objects, so they are
    compared by memory address.
    """
    if current is None or desired is None:
        return current is None and desired is None
    if not (isinstance(current, bpy.types.ID) and isinstance(desired, bpy.types.ID)):
        return False
    return current.as_pointer() == desired.as_pointer()


def same_value(current: Any, desired: Any) -> bool:
    """True when writing *desired* over *current* would store the same value.

    Blender stores floats as float32, so *desired* is rounded to float32
    before the comparison. Otherwise ``0.1``, which reads back as
    ``0.10000000149011612``, would look changed on every build. Any
    difference that float32 can hold still compares unequal. Datablocks
    compare by identity, and sequences compare element by element.

    A wrong False only costs an extra write. A wrong True would leave the
    artifact out of date for good. So any type not handled here gives
    False.
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
class BuildStats:
    """Counts of what a build actually changed in the tree.

    Tests and the profiler use it to tell a small patch from a full
    rebuild. No add-on code depends on it. ``values_written`` counts only
    the writes that happened, not the ones skipped as unchanged.
    """
    nodes_created: int = 0
    values_written: int = 0
    links_created: int = 0
    links_removed: int = 0
    arranged: bool = False


class NodeTreeBuilder:
    """Updates a node tree so its nodes, links and interface match an IR."""

    def __init__(self, node_tree: bpy.types.NodeTree, ir: IR):
        """Prepare to build *node_tree* from *ir*. *ir* is never modified.

        Its links are copied into a set, which also drops duplicate links.
        Its sockets are sorted into a new list.
        """
        # ``bl_use_group_interface`` exists from Blender 4.3. Earlier
        # versions give every node tree an interface.
        if ir.sockets and not getattr(node_tree, "bl_use_group_interface", True):
            raise ValueError("Node tree does not use group interface")
        self.node_tree = node_tree
        self._node_instructions: dict[str, IRNode] = ir.nodes
        self._link_instructions: set[tuple[str, int | str, str, int | str]] = {
            (link.from_id, link.from_socket, link.to_id, link.to_socket) for link in ir.links
        }
        # Outputs first, as Blender lists them in the interface. Sorted into
        # a copy, because the order of the IR's list is part of its
        # fingerprint.
        self._socket_instructions: list[IRSocket] = sorted(
            ir.sockets, key=lambda sock: sock.in_out == 'OUTPUT', reverse=True)
        self._existing_nodes: dict[str, bpy.types.Node] = {}
        # Nodes that repeat an earlier node's identifier. See
        # ``_hydrate_existing_nodes``.
        self._duplicate_nodes: list[bpy.types.Node] = []
        self._newly_created: set[str] = set()
        # (node pointer, is_input) -> (sockets, name -> socket). Filled only
        # during the link phase. See ``_socket_by_id``.
        self._socket_cache: dict[
            tuple[int, bool],
            tuple[list[bpy.types.NodeSocket], dict[str, bpy.types.NodeSocket]],
        ] = {}
        # node pointer -> the node's box. See ``_node_bbox``.
        self._bbox_cache: dict[int, tuple[float, float, float, float]] = {}
        self.stats = BuildStats()
        self._hydrate_existing_nodes()

    # ── Build ────────────────────────────────────────────────────────

    def build(self, *, arrange: bool = True) -> None:
        with phase("upsert", self.node_tree):
            self._sync_interface_sockets()

            desired_ids = set(self._node_instructions.keys())

            # Remove unwanted and duplicate nodes. Blender removes their
            # links too.
            for identifier in list(self._existing_nodes.keys()):
                if identifier not in desired_ids:
                    self.node_tree.nodes.remove(
                        self._existing_nodes.pop(identifier))
            for node in self._duplicate_nodes:
                self.node_tree.nodes.remove(node)
            self._duplicate_nodes.clear()

            # Create missing nodes, replace nodes of the wrong type, then set
            # properties and socket values.
            for identifier, instr in self._node_instructions.items():
                node = self._existing_nodes.get(identifier)

                if node is not None and node.bl_idname != instr.bl_idname:
                    self.node_tree.nodes.remove(node)
                    node = None

                if node is None:
                    node = self.node_tree.nodes.new(instr.bl_idname)
                    node[IDENTIFIER_KEY] = identifier
                    self._existing_nodes[identifier] = node
                    self._newly_created.add(identifier)
                    self.stats.nodes_created += 1

                self._apply_node_properties(node, instr.properties)
                self._apply_socket_properties(node.inputs, instr.inputs)
                self._apply_socket_properties(node.outputs, instr.outputs)

        with phase("links", self.node_tree):
            self._sync_links()

        if arrange:
            with phase("arrange", self.node_tree):
                self.arrange_nodes()

    # ── Link sync ────────────────────────────────────────────────────

    def _sync_links(self) -> None:
        """Make the tree's links exactly match the declared links.

        Links are matched by the memory addresses of the two sockets they
        connect, never by ``ps_identifier``. When a user duplicates an
        artifact node by hand, the copy gets the same tag. Matching by
        identifier could then mistake the copy's link for the real one, and
        never create the real link.

        Matching by address also avoids ``NodeSocket.links``, a Python
        property that scans every link in the tree on each read. Instead,
        one pass over ``node_tree.links`` decides every removal, and each
        creation only looks up a socket address in a dict.
        """
        # The socket cache may only be filled from here on. Before this
        # point, a changed ``bl_idname`` could still recreate a node, which
        # frees its sockets.
        self._socket_cache.clear()

        # Target socket address -> source socket addresses. ``wanted`` holds
        # the declared links, and ``present`` the ones the tree already has.
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
                # An input that holds one link drops its old link when a new
                # one is added, so only the new pair is present now.
                present[to_pointer] = {from_pointer}
            else:
                linked.add(from_pointer)

    # ── Interface socket sync ────────────────────────────────────────

    def _sync_interface_sockets(self) -> None:
        """Make the tree's interface sockets match ``_socket_instructions``.

        The sockets end up in the same order as the instructions.
        """
        interface = self.node_tree.interface

        def _flat_sockets() -> list[bpy.types.NodeTreeInterfaceSocket]:
            return [item for item in interface.items_tree
                    if item.item_type == 'SOCKET']

        # Sockets are keyed by (name, in_out), which must be unique.
        SocketKey = tuple[str, str]
        desired_keys: dict[SocketKey, IRSocket] = {
            (instr.name, instr.in_out): instr
            for instr in self._socket_instructions
        }

        # Remove sockets that are no longer desired or have a changed type
        for sock in _flat_sockets():
            key: SocketKey = (sock.name, sock.in_out)
            instr = desired_keys.get(key)
            # Compare ``socket_type``, the base type. ``bl_socket_idname``
            # includes the subtype, such as NodeSocketFloatFactor, so it
            # would remove and re-create sockets on every build.
            existing_type = getattr(sock, 'socket_type', sock.bl_socket_idname)
            if instr is None or existing_type != instr.socket_type:
                interface.remove(sock)

        # Rebuild lookup after removals
        existing: dict[SocketKey, bpy.types.NodeTreeInterfaceSocket] = {
            (s.name, s.in_out): s for s in _flat_sockets()
        }

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

        # Apply declared properties
        for instr in self._socket_instructions:
            sock = existing[(instr.name, instr.in_out)]
            for prop, value in instr.properties.items():
                self._write_if_changed(sock, prop, value)

    # ── Hydration ────────────────────────────────────────────────────

    def _hydrate_existing_nodes(self) -> None:
        """Map the tree's existing nodes by identifier.

        A node copied by hand carries the original's ``ps_identifier``. The
        copy is added after the original, so the first node with an
        identifier keeps it. Later nodes with the same identifier are listed
        for the build to remove.
        """
        for node in self.node_tree.nodes:
            identifier = node_identifier(node)
            if identifier in self._existing_nodes:
                self._duplicate_nodes.append(node)
            else:
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
        """Write *value* to *owner*, unless the property already holds it.

        Every property write on a node tree tags the tree, and the materials
        that use it, for an update. That update takes time in proportion to
        the tree's size. A compile sets every value in the artifact. Without
        this check, writes that change nothing would be most of the cost of
        an edit.

        A property that cannot be read is written anyway. So this check never
        causes an error where a plain write would work.
        """
        current = getattr(owner, prop, _UNREADABLE)
        if current is _UNREADABLE or not same_value(current, value):
            self._write(owner, prop, value)

    def _apply_node_properties(
        self, node: bpy.types.Node, properties: dict[str, Any],
    ) -> None:
        for prop, value in properties.items():
            # A property this node type does not have is skipped, not an error.
            if node.bl_rna.properties.get(prop) is None:
                continue
            self._write_if_changed(node, prop, value)

    def _apply_socket_properties(
        self,
        sockets: bpy.types.NodeInputs | bpy.types.NodeOutputs,
        socket_specs: dict[int | str, dict[str, Any]],
    ) -> None:
        for socket_id, props in socket_specs.items():
            socket = self._resolve_socket(sockets, socket_id)
            for prop, value in props.items():
                self._write_if_changed(socket, prop, value)

    def _socket_by_id(
        self, node: bpy.types.Node, is_input: bool, socket_id: int | str,
    ) -> bpy.types.NodeSocket:
        """Like ``_resolve_socket``, but caches *node*'s sockets for reuse.

        Each declared link names its sockets by index or by name, and many
        links use the same node. Building the lookup once per node turns
        name scans into dict reads. The cache is only valid while socket
        lists cannot change, so it is only used in the link phase.
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
        """Lay out nodes from left to right, following the links.

        Sinks, the nodes that feed nothing, sit at the right at x=0, and
        the nodes feeding them go to the left. Only newly created nodes are
        placed. Existing nodes keep their positions unless a new node
        overlaps them. Then the smaller of the upstream or downstream
        cluster, by node count, moves to make room.
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
        """Build (successors, predecessors) maps from the declared links.

        Self-loops are skipped. Both ends of every link exist by now,
        because the link phase looked each of them up.
        """
        successors: dict[str, list] = defaultdict(list)
        predecessors: dict[str, list] = defaultdict(list)
        for from_id, from_sock, to_id, to_sock in self._link_instructions:
            if from_id == to_id:
                continue
            successors[from_id].append((from_sock, to_id, to_sock))
            predecessors[to_id].append((to_sock, from_id, from_sock))
        return dict(successors), dict(predecessors)

    # ── Geometry helpers ─────────────────────────────────────────

    def _node_height(self, node: bpy.types.Node) -> float:
        """Return the node's height, or an estimate when it is not known yet.

        ``node.dimensions`` can be (0, 0) right after the node is created.
        """
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

    def _set_loc(self, node: bpy.types.Node, axis: int, value: float) -> None:
        """Set one axis of *node*'s location to *value* if it differs.

        Layout gives a position to every node it visits, and most nodes are
        already there. A location write tags the tree and its materials for
        an update, like any other property write. So a write that moves
        nothing costs as much as a real move.
        """
        location = node.location
        if location[axis] != _as_float32(value):
            location[axis] = value
            self._bbox_cache.pop(node.as_pointer(), None)

    def _shift_x(self, node_ids, delta: float) -> None:
        """Move the nodes *node_ids* sideways by *delta*.

        A node whose stored float32 position would not change is not
        written.
        """
        if not delta:
            return
        for nid in node_ids:
            node = self._existing_nodes[nid]
            location = node.location
            current = location[0]
            if _as_float32(current + delta) != current:
                location[0] = current + delta
                self._bbox_cache.pop(node.as_pointer(), None)

    def _node_bbox(
        self, node: bpy.types.Node,
    ) -> tuple[float, float, float, float]:
        """Return (left, top, right, bottom). Y grows upward, so bottom < top.

        Overlap checks read the same boxes many times per build but move few
        nodes, and each box costs four Blender property reads. So boxes are
        cached for the build. ``_set_loc`` and ``_shift_x`` drop a node's
        entry when they move it. They are the only code that moves a node
        during a build.
        """
        key = node.as_pointer()
        box = self._bbox_cache.get(key)
        if box is not None:
            return box
        left = float(node.location.x)
        top = float(node.location.y)
        width = self._node_width(node)
        height = self._node_height(node)
        box = (left, top, left + width, top - height)
        self._bbox_cache[key] = box
        return box

    def _socket_y(
        self, node: bpy.types.Node, socket_idx: int, is_input: bool,
    ) -> float:
        """Return the approximate Y position of a socket in the editor.

        Blender draws outputs above inputs, whatever order they are declared
        in. This does the same, so that lining up sockets across nodes works.
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
        """True when two boxes overlap. A box is (left, top, right, bottom)."""
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
        """Return each node's depth, the longest path of links to a sink.

        Sinks have depth 0. In a cycle, a link back to a node that is still
        being walked counts as depth 0.

        The walk uses an explicit stack, not recursion. An artifact is one
        long chain of layers, so recursion could raise RecursionError when
        there are many layers. Any error in layout stops the compile before
        the new fingerprint is stored on the artifact.
        """
        depths: dict[str, int] = {}
        for start in self._existing_nodes:
            if start in depths:
                continue
            on_path = {start}
            stack = [(start, iter(successors.get(start, ())))]
            while stack:
                nid, pending = stack[-1]
                for _from_sock, target, _to_sock in pending:
                    if target in depths:
                        continue
                    if target in on_path:
                        # Cut the cycle here. This 0 is temporary. It is
                        # overwritten when the node's own frame finishes.
                        depths[target] = 0
                        continue
                    on_path.add(target)
                    stack.append((target, iter(successors.get(target, ()))))
                    break
                else:
                    stack.pop()
                    on_path.discard(nid)
                    succs = successors.get(nid, ())
                    depths[nid] = (
                        1 + max(depths.get(t[1], 0) for t in succs)
                        if succs else 0
                    )
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
            self._set_loc(node, 0, col_right_x[d] - self._node_width(node))

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
                self._place_predecessors(
                    sink_id, placed, column_occupied, depths, predecessors,
                )
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
        """Place *node*'s top at *target_top*, or lower if that spot is taken.

        The node moves down past any occupied interval in its column. Its
        own interval is then recorded. Returns the top it was placed at.
        """
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
        self._set_loc(node, 1, top)
        column_occupied[depth].append((top, bottom))
        return top

    def _sorted_predecessors(
        self, node_id: str, predecessors: dict[str, list],
    ) -> list[tuple[int, str, int]]:
        """Predecessors of *node_id* as (input index, id, output index).

        Ordered by the input they feed, so a node's upstream neighbours are
        placed top to bottom in the order its sockets appear.
        """
        node = self._existing_nodes[node_id]
        annotated = []
        for to_sock, from_id, from_sock in predecessors.get(node_id, []):
            to_idx = self._socket_idx(node.inputs, to_sock)
            from_idx = self._socket_idx(
                self._existing_nodes[from_id].outputs, from_sock,
            )
            annotated.append((to_idx, from_id, from_idx))
        annotated.sort(key=lambda t: t[0])
        return annotated

    def _place_predecessors(
        self,
        node_id: str,
        placed: set[str],
        column_occupied: dict[int, list[tuple[float, float]]],
        depths: dict[str, int],
        predecessors: dict[str, list],
    ) -> None:
        """Place everything upstream of *node_id*, depth first.

        Like ``_compute_depths``, this uses an explicit stack, not recursion.
        A long layer chain could exceed Python's recursion limit, and an
        error in layout would stop the compile before the fingerprint is
        stored.
        """
        stack = [(node_id, iter(self._sorted_predecessors(node_id, predecessors)))]
        while stack:
            current_id, pending = stack[-1]
            node = self._existing_nodes[current_id]
            for to_idx, from_id, from_idx in pending:
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
                stack.append(
                    (from_id,
                     iter(self._sorted_predecessors(from_id, predecessors))),
                )
                break
            else:
                stack.pop()

    # ── Incremental layout ───────────────────────────────────────

    def _incremental_layout(
        self,
        successors: dict[str, list],
        predecessors: dict[str, list],
        new_ids: set[str],
        positioned_ids: set[str],
    ) -> None:
        # For each new node, find the nearest positioned node (its anchor)
        # and the number of hops to it, both upstream and downstream. Also
        # find its direct neighbour on the topmost socket, which sets its Y.
        # The hop counts spread a chain of new nodes over several columns
        # instead of piling them up at the same X.
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

        # Group new nodes by (up_anchor, down_anchor), so each group makes
        # room between its anchors once.
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
        """Find the nearest positioned node, walking only through new nodes.

        The search is breadth first. Returns (anchor_id, hop_count).
        hop_count is 1 for a direct neighbour, 2 when one new node lies in
        between, and so on. Returns (None, None) when no positioned node can
        be reached.
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
        """Return the direct neighbour on *nid*'s topmost linked socket.

        The result is (neighbour id, socket index on *nid*, socket index on
        the neighbour), or None when *nid* has no links on this side. It
        does not walk past the direct neighbour.
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
        # A member's column index is its hop count from the up anchor when
        # there is one, otherwise from the down anchor. Members with the
        # same index are siblings and share a column.
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
        # Give any empty column a default width. This should not happen,
        # and is only a safeguard.
        for k in range(1, chain_length + 1):
            col_max_width.setdefault(k, DEFAULT_NODE_WIDTH)

        # With both anchors, make room when the gap between them is too
        # small.
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
            # Only a down anchor: fill columns leftwards, right-aligned.
            down_node = self._existing_nodes[down_id]
            cursor = float(down_node.location.x) - H_MARGIN
            for k in range(1, chain_length + 1):
                # cursor is the right edge of column k
                col_x[k] = cursor - col_max_width[k]
                cursor = col_x[k] - H_MARGIN

        # Place column by column, for k = 1 to chain_length, moving away
        # from the anchor. With an up anchor that is left to right. With
        # only a down anchor it is right to left. Either way, each member's
        # neighbour on the anchor side is placed before the member.
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
                self._set_loc(node, 0, col_x[k])
                # Y: line up with the direct neighbour on the anchor side
                target_top = self._neighbor_aligned_y(nid, meta, up_id)
                self._place_in_column(node, k, target_top, column_intervals)
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
        """Sort key that orders siblings by the neighbour socket they use.

        A lower socket index, which is higher on the node, comes first.
        """
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
        """Return a top Y that lines up *nid*'s socket with its neighbour's.

        The neighbour is the direct one on the anchor side. It may be an
        existing node, or a new node placed just before this one.
        """
        node = self._existing_nodes[nid]
        m = meta[nid]
        # Line up with the up side when this group has an up anchor,
        # otherwise with the down side.
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
        """Move the up cluster left or the down cluster right by *deficit*.

        The smaller cluster, by node count, is moved, so fewer nodes change
        place.
        """
        up_cluster = self._reachable(
            up_id, predecessors, positioned_ids, exclude={down_id},
        )
        down_cluster = self._reachable(
            down_id, successors, positioned_ids, exclude={up_id},
        )

        if len(up_cluster) <= len(down_cluster):
            self._shift_x(up_cluster, -deficit)
        else:
            self._shift_x(down_cluster, deficit)

    def _reachable(
        self,
        start: str,
        adjacency: dict[str, list],
        universe: set[str],
        exclude: set[str],
    ) -> set[str]:
        """Nodes in *universe* reachable from *start*, avoiding *exclude*."""
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
        """Move positioned nodes out of the way of the group just placed.

        The group's up and down anchors are not tested for overlap. Overlaps
        between two nodes that were already positioned are left alone. Only
        overlaps with the new group are fixed.
        """
        excluded = set(group_members)
        if up_id is not None:
            excluded.add(up_id)
        if down_id is not None:
            excluded.add(down_id)

        # The group does not move in this loop. Only positioned nodes are
        # shifted, and the group's members are new nodes, which are not in
        # positioned_ids yet. So the group's box and the list of nodes to
        # test are the same on every pass, and are built once here.
        boxes = [
            self._node_bbox(self._existing_nodes[nid])
            for nid in group_members
        ]
        group_box = (
            min(b[0] for b in boxes),
            max(b[1] for b in boxes),
            max(b[2] for b in boxes),
            min(b[3] for b in boxes),
        )
        candidates = [nid for nid in positioned_ids if nid not in excluded]
        group_member_set = set(group_members)

        max_iter = max(8, len(positioned_ids) * 2)
        for _ in range(max_iter):
            worst: str | None = None
            worst_overlap = 0.0
            for nid in candidates:
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
                # The worst node is left of the group's centre, or level
                # with it, so it and its upstream shift left.
                cluster = self._reachable(
                    worst, predecessors, positioned_ids,
                    exclude=group_member_set,
                )
                self._shift_x(cluster, -shift_amount)
            else:
                cluster = self._reachable(
                    worst, successors, positioned_ids,
                    exclude=group_member_set,
                )
                self._shift_x(cluster, shift_amount)

        log.debug("arrange_nodes: overlap resolution did not converge after %d iterations",
                  max_iter)

    def _place_orphan_group(
        self, members: list[str], positioned_ids: set[str],
    ) -> None:
        """Place new nodes whose links reach no positioned node.

        They are stacked in one column, to the right of every positioned
        node.
        """
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
            self._set_loc(node, 0, x)
            self._set_loc(node, 1, y)
            y -= self._node_height(node) + V_MARGIN
