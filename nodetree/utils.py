import bpy


def find_node(node_tree: bpy.types.NodeTree, properties: dict, start_node: bpy.types.Node | None = None) -> bpy.types.Node | None:
    """Traverse the node tree to find a node with the given properties.

    Args:
        node_tree (bpy.types.NodeTree): The node tree to traverse.
        properties (dict): The properties to search for.
        start_node (bpy.types.Node | None, optional): The node to start the search from. Defaults to None.

    Returns:
        bpy.types.Node | None: _description_
    """
    nodes = node_tree.nodes
    if start_node is None:
        for node in nodes:
            if all(getattr(node, key) == value for key, value in properties.items()):
                return node
    else:
        visited = set()
        queue = [start_node]
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            if all(getattr(node, key) == value for key, value in properties.items()):
                return node

            # Only continue traversing if we haven't found a match yet
            for input_socket in node.inputs:
                for link in input_socket.links:
                    if link.from_node not in visited:
                        queue.append(link.from_node)

            for output_socket in node.outputs:
                for link in output_socket.links:
                    if link.to_node not in visited:
                        queue.append(link.to_node)
    return None
