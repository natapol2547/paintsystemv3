"""Lay out the nodes of a compiled node tree.

The builder runs this after it has created a build's nodes and links.
Only the nodes that build created are placed. Nodes that already existed
keep their positions, which may be where the user moved them, and move
only to make room for a new node.
"""
from __future__ import annotations

import logging
from collections import defaultdict

import bpy

from .values import as_float32

log = logging.getLogger(__name__)

H_MARGIN = 50.0       # horizontal gap between columns
V_MARGIN = 30.0       # vertical gap between siblings in a column
HEADER_HEIGHT = 35.0  # approximate node header height in px
SOCKET_HEIGHT = 22.0  # approximate per-socket row height in px
DEFAULT_NODE_WIDTH = 140.0


class NodeLayout:
    """Places the nodes one build created."""

    def __init__(
        self,
        nodes: dict[str, bpy.types.Node],
        new_ids: set[str],
        links: set[tuple[str, int | str, str, int | str]],
        order: list[str],
    ):
        """*nodes* maps each identifier to its node in the tree, and *new_ids*
        names the ones the build created. *links* holds the declared links
        as (from id, from socket, to id, to socket). *order* is the IR's node
        order, which decides where unconnected groups of nodes go.
        """
        self._existing_nodes = nodes
        self._newly_created = new_ids
        self._link_instructions = links
        self._node_order = order
        # node pointer -> the node's box. See ``_node_bbox``.
        self._bbox_cache: dict[int, tuple[float, float, float, float]] = {}

    def arrange(self) -> bool:
        """Lay out nodes from left to right, following the links.

        Sinks, the nodes that feed nothing, sit at the right at x=0, and
        the nodes feeding them go to the left. Only newly created nodes are
        placed. Existing nodes keep their positions unless a new node
        overlaps them. Then the smaller of the upstream or downstream
        cluster, by node count, moves to make room.

        Returns False when there was no new node to place.
        """
        if not self._newly_created or not self._existing_nodes:
            return False

        successors, predecessors = self._build_adjacency()
        new_ids = set(self._newly_created)
        positioned_ids = set(self._existing_nodes) - new_ids

        if not positioned_ids:
            self._full_layout(successors, predecessors)
        else:
            self._incremental_layout(
                successors, predecessors, new_ids, positioned_ids,
            )
        return True

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
        if location[axis] != as_float32(value):
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
            if as_float32(current + delta) != current:
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

        insertion_order = self._node_order
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

        log.debug("arrange: overlap resolution did not converge after %d iterations",
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
