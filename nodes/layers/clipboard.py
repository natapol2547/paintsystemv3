"""Copy and paste layers, the layer list's Copy and Paste (PS-017).

Copying records which layers were copied, not what they held. A paste
then copies them as they are at that moment, each folder with the layers
inside it. So a layer changed after the copy pastes as it is now, and one
deleted since is passed over. Layers are recorded by their tree's uuid
and their own. Those survive renames and undo, which pointers do not.

A paste goes where Add Layer puts a new layer, and the pasted layers
keep their order. Masks among them are linked again. A mask from a
layer that was not copied is left out.

A plain paste makes layers that stand alone. Each gets its own copy of
content such as images, and pasted layers are linked only with each
other, when the layers they copy were linked. A linked paste links each
new layer with the layer it copies, so it only works in the tree the
layers were copied from.
"""
from collections import defaultdict
import uuid

import bpy

from . import links
from ...compiler.core import ps_trees, suspend_compile
from ...nodetree import stack_ops

# (tree uuid, layer uuid) of each copied layer, top first.
_copied: list[tuple[str, str]] = []

# The properties every node has. Most of them place the node in its
# tree, which the paste decides. The pasted layer takes the few in
# ``_NODE_PROPS`` from the copied one.
_BASE_PROPS = {prop.identifier for prop in bpy.types.Node.bl_rna.properties}
_NODE_PROPS = ('name', 'label', 'hide', 'mute', 'width', 'use_custom_color', 'color')
# A pasted layer gets its own uuid, and the paste decides its links.
_NOT_COPIED = {'uuid', 'link_id'}


def copy_layers(nodes) -> None:
    """Put *nodes*, layers of one Paint System tree, on the clipboard, top first."""
    _copied[:] = [(node.id_data.uuid, node.uuid) for node in nodes]


def copied_layers() -> list:
    """The copied layers that still exist, top first."""
    # ``compiler.core.normalize_tree`` keeps tree uuids unique, and node
    # uuids unique in their tree.
    trees = {tree.uuid: tree for tree in ps_trees()}
    found = []
    for tree_uuid, node_uuid in _copied:
        tree = trees.get(tree_uuid)
        node = None if tree is None else next(
            (node for node in tree.nodes if getattr(node, 'uuid', "") == node_uuid), None)
        if node is not None:
            found.append(node)
    return found


def can_paste_linked(tree) -> bool:
    """Whether the copied layers can be pasted linked into *tree*: they are all in it."""
    copied = copied_layers()
    return bool(copied) and all(node.id_data == tree for node in copied)


def _copy_node(source, tree):
    """A new node in *tree* with *source*'s settings, outside any stack."""
    node = tree.nodes.new(source.bl_idname)
    names = [prop.identifier for prop in source.bl_rna.properties
             if prop.identifier not in _BASE_PROPS and prop.identifier not in _NOT_COPIED
             and not prop.is_readonly and prop.type != 'COLLECTION']
    for name in (*_NODE_PROPS, *names):
        setattr(node, name, getattr(source, name))
    # The hook Blender runs on a node it copies. It gives the layer a new
    # uuid and drops the cache, as it does for Shift+D.
    node.copy(source)
    return node


def _fill(tree, folder, content) -> None:
    """Put the new layers *content*, a list of (layer, its content), into the empty *folder*, top first."""
    for node, inner in content:
        stack_ops.insert_into(tree, folder, node, at_top=False)
        _fill(tree, node, inner)


def _link_among_copies(pairs) -> None:
    """Link the new layers whose copied layers were linked with each other, and nothing else.

    Each new layer then gets its own copy of shared content. One layer of
    each linked group makes the copy, and linking gives it to the rest.
    """
    groups = defaultdict(list)
    for source, node in pairs:
        if source.link_id:
            groups[(source.bl_idname, source.link_id)].append(node)
        else:
            node.own_content()
    for nodes in groups.values():
        link_id = str(uuid.uuid4()) if len(nodes) > 1 else ""
        for node in nodes:
            node.link_id = link_id
        nodes[0].own_content()


def paste_layers(tree, target=None, *, linked: bool = False) -> list:
    """Paste the copied layers into *tree*'s active channel, and return the new top-level layers.

    They go above *target*, into it when it is a folder, or on top of the
    stack with no *target*, as ``PaintSystemNodeTree.insert_layer_node``
    places a new layer. The first becomes the active layer. With
    *linked*, each new layer is linked with the layer it copies, which
    must be in *tree* (``can_paste_linked``).
    """
    # Every copy is made before any is placed, so pasting a folder into
    # itself copies its content as it was.
    pairs = []
    visited: set[str] = set()

    def copy_branch(source):
        node = _copy_node(source, tree)
        pairs.append((source, node))
        content = []
        if stack_ops.is_folder(source):
            children = list(stack_ops.layers_down_from(stack_ops.content_input(source), visited))
            content = [copy_branch(child) for child in children]
        return node, content

    with suspend_compile(tree):
        copied = copied_layers()
        # A layer moved into a copied folder since the copy is pasted
        # once, with the folder.
        inside = {node.name for source in copied if stack_ops.is_folder(source)
                  for node in stack_ops.descendants(source) if node != source}
        branches = []
        for source in copied:
            if source.name not in inside:
                visited.add(source.name)
                branches.append(copy_branch(source))
        if not branches:
            return []

        previous = None
        for node, content in branches:
            if previous is None:
                tree.place_layer_node(node, target=target)
            else:
                stack_ops.insert_below(tree, node, previous)
            previous = node
            _fill(tree, node, content)

        new_of = {source.as_pointer(): node for source, node in pairs}
        for source, node in pairs:
            link = stack_ops.producing_link(source.inputs['Mask'])
            mask = new_of.get(link.from_node.as_pointer()) if link is not None else None
            if mask is not None:
                tree.links.new(mask.outputs[link.from_socket.identifier], node.inputs['Mask'])

        if linked:
            for source, node in pairs:
                links.link([node], source)
        else:
            _link_among_copies(pairs)

        first = branches[0][0]
        stack_ops.arrange_stack(tree, tree.active_channel.name)
        tree.activate_layer_node(first)
        tree.reveal_layer_node(first)
    return [node for node, _content in branches]
