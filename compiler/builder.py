from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import bpy

from .layout import NodeLayout
from .profile import phase
from .values import same_value

if TYPE_CHECKING:
    # Only for type hints. A real import would be circular, because
    # ``ir`` imports this module.
    from .ir import IR, IRNode, IRSocket

# Custom property that stores each artifact node's IR identifier. Rebuilds
# find nodes by it, so a node still matches after it is renamed.
IDENTIFIER_KEY = "ps_identifier"


def node_identifier(node) -> str:
    return node.get(IDENTIFIER_KEY) or node.name


# Marks a property that cannot be read back. Such a property is always
# written.
_UNREADABLE = object()


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
                layout = NodeLayout(
                    self._existing_nodes, self._newly_created,
                    self._link_instructions, list(self._node_instructions),
                )
                self.stats.arranged = layout.arrange()

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

        # A renamed channel changes socket names and nothing else. The IR
        # only knows names, so a side whose sockets still line up by
        # position and type is taken as renamed, and its sockets are
        # renamed in place. They keep their identifiers, so group nodes
        # keep their links. Removing and creating them would drop the
        # links.
        current = _flat_sockets()
        for in_out in ('OUTPUT', 'INPUT'):
            have = [sock for sock in current if sock.in_out == in_out]
            want = [instr for instr in self._socket_instructions if instr.in_out == in_out]
            if len(have) != len(want) or any(
                    sock.socket_type != instr.socket_type for sock, instr in zip(have, want)):
                continue
            have_names = {sock.name for sock in have}
            for sock, instr in zip(have, want):
                if (sock.name, in_out) not in desired_keys and instr.name not in have_names:
                    sock.name = instr.name

        # Remove sockets that are no longer desired, and retype the rest
        for sock in _flat_sockets():
            key: SocketKey = (sock.name, sock.in_out)
            instr = desired_keys.get(key)
            if instr is None:
                interface.remove(sock)
                continue
            # Compare ``socket_type``, the base type. ``bl_socket_idname``
            # includes the subtype, such as NodeSocketFloatFactor, so it
            # would differ on every build.
            if sock.socket_type != instr.socket_type:
                # Retyped in place, the socket keeps its identifier, so
                # group nodes keep their links. A new socket would drop
                # them. The subtype and range reset, and the declared
                # properties are written again below. The Python object
                # still has the old type afterwards, so the lookup below
                # reads the sockets again.
                sock.socket_type = instr.socket_type

        # Rebuild lookup after removals and retypes
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
