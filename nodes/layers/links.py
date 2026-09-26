"""Linked layers: layer nodes of one type that share their settings (PS-016).

Layers are linked when they are nodes of the same type in the same tree
and have the same ``link_id``. Each node keeps a full set of settings,
so it compiles like any other layer and needs nothing from the others.
Changing a linked setting on one node copies the new value to the
others.

Every property a layer class declares is linked, except the ones its
``ps_unlinked_props`` names. Those hold state that belongs to one place
in one stack, such as a bake cache or a filter result, which are built
from the layers below. A linked settings group, such as a filter
layer's ``node.blur``, is linked as a whole: changing one of its
settings copies the group.

Values are copied from the properties' ``update`` callbacks. Drivers and
keyframes set values without calling them, so animated values stay per
node.
"""
from collections import Counter
import uuid

import bpy

from ...compiler.values import same_value
from ...compiler.core import ps_trees, suspend_compile

# The type ``bpy.props.*Property(...)`` returns in a class body, before
# Blender registers the class.
_DEFERRED = type(bpy.props.BoolProperty())

# True while ``copy_settings`` writes. The writes run the targets' own
# callbacks, and those must not copy the value on again.
_copying = False

# Node class -> names of its Image pointer properties. See _image_props.
_image_prop_names: dict[type, tuple[str, ...]] = {}


def _copies_on_change(update, holder):
    """An ``update`` callback that runs *update*, then copies the changed setting to the linked layers.

    *holder* takes what the property belongs to and returns the layer and
    the name of its setting to copy, or ``(None, "")``.
    """
    def callback(self, context):
        layer, name = (None, "") if _copying else holder(self)
        if layer is None or not layer.link_id:
            if update is not None:
                update(self, context)
            return
        # Each write compiles the tree on its own. Suspended, the change
        # and its copies compile once.
        with suspend_compile(layer.id_data):
            if update is not None:
                update(self, context)
            copy_settings(layer, linked_layers(layer), (name,))
    callback.ps_linked = True
    return callback


def _own_setting(name: str):
    """A *holder* for `_copies_on_change`: the layer's own property *name*."""
    return lambda layer: (layer, name)


def _group_holder(group):
    """A *holder* for `_copies_on_change`: the layer holding the settings group *group*.

    Blender cannot make a path to a group inside a node, so the tree is
    searched for it. A tree holds few enough nodes for that to be cheap.
    """
    for node in group.id_data.nodes:
        for name in getattr(type(node), 'ps_linked_groups', ()):
            if getattr(node, name) == group:
                return node, name
    return None, ""


def _is_group(prop) -> bool:
    """Whether the deferred property *prop* points at a settings group."""
    return (prop.function is bpy.props.PointerProperty
            and issubclass(prop.keywords['type'], bpy.types.PropertyGroup))


def _link_group(group) -> None:
    """Make each property of the settings group class *group* copy the group on change."""
    if group.__dict__.get('ps_linked_group'):
        return
    annotations = group.__dict__.get('__annotations__', {})
    for name, prop in list(annotations.items()):
        if isinstance(prop, _DEFERRED):
            update = _copies_on_change(prop.keywords.get('update'), _group_holder)
            annotations[name] = prop.function(**{**prop.keywords, 'update': update})
    group.ps_linked_group = True


def link_settings(cls) -> None:
    """Make the properties *cls* declares copy on change, and record what is linked.

    Runs on the class body before Blender registers the class, and on
    the settings groups it points at before they are registered. A class
    lists in ``ps_unlinked_props`` only properties it declares itself.
    ``cls.ps_linked_props`` then names every linked property *cls* has,
    inherited ones included, and ``cls.ps_linked_groups`` the linked
    settings groups among them.
    """
    annotations = cls.__dict__.get('__annotations__', {})
    unlinked = set(cls.__dict__.get('ps_unlinked_props', ()))
    for name, prop in list(annotations.items()):
        if (isinstance(prop, _DEFERRED) and name not in unlinked
                and prop.function is not bpy.props.CollectionProperty):
            if _is_group(prop):
                _link_group(prop.keywords['type'])
            update = _copies_on_change(prop.keywords.get('update'), _own_setting(name))
            annotations[name] = prop.function(**{**prop.keywords, 'update': update})
    # Blender registers the declaration nearest in the MRO, so a subclass
    # that declares a property again decides whether it is linked.
    declared = {}
    for klass in reversed(cls.__mro__):
        for name, prop in klass.__dict__.get('__annotations__', {}).items():
            if isinstance(prop, _DEFERRED):
                declared[name] = prop
    cls.ps_linked_props = tuple(name for name, prop in declared.items()
                                if getattr(prop.keywords.get('update'), 'ps_linked', False))
    cls.ps_linked_groups = tuple(name for name in cls.ps_linked_props if _is_group(declared[name]))


def copy_settings(source, targets, names=None) -> None:
    """Give each of *targets* *source*'s value of every linked setting, or of *names*.

    Only values that differ are written, so each write's callbacks run
    only for a real change.
    """
    global _copying
    names = type(source).ps_linked_props if names is None else names
    previous = _copying
    _copying = True
    try:
        for name in names:
            for target in targets:
                copy_setting(source, target, name)
    finally:
        _copying = previous


def copy_setting(source, target, name: str) -> None:
    """Give *target* *source*'s value of the property *name*, when it differs.

    A settings group cannot be assigned, so its settings are copied one
    at a time.
    """
    value = getattr(source, name)
    if isinstance(value, bpy.types.PropertyGroup):
        into = getattr(target, name)
        for prop in value.bl_rna.properties:
            if not prop.is_readonly:
                copy_setting(value, into, prop.identifier)
    elif not same_value(getattr(target, name), value):
        setattr(target, name, value)


def linked_layers(node) -> list:
    """The other layers linked with *node*, in the tree's node order."""
    if not node.link_id:
        return []
    return [other for other in node.id_data.nodes
            if other != node and other.bl_idname == node.bl_idname
            and getattr(other, 'link_id', "") == node.link_id]


def linked_names(tree) -> set[str]:
    """Names of the layers in *tree* that are linked with at least one other."""
    groups = Counter((node.bl_idname, node.link_id) for node in tree.nodes
                     if getattr(node, 'link_id', ""))
    return {node.name for node in tree.nodes
            if getattr(node, 'link_id', "") and groups[(node.bl_idname, node.link_id)] > 1}


def link_candidates(node) -> list:
    """The layers *node* can be linked with: the same type, in the same tree, not yet linked with it."""
    return [other for other in node.id_data.nodes
            if other != node and other.bl_idname == node.bl_idname
            and not (node.link_id and other.link_id == node.link_id)]


def _image_props(cls) -> tuple[str, ...]:
    names = _image_prop_names.get(cls)
    if names is None:
        names = tuple(prop.identifier for prop in cls.bl_rna.properties
                      if prop.type == 'POINTER' and prop.fixed_type.identifier == 'Image')
        _image_prop_names[cls] = names
    return names


def lost_images(nodes, source) -> list[tuple]:
    """(node, image) for each image that linking *nodes* to *source* takes out of use.

    Linking gives each node *source*'s linked settings, images included.
    An image that no Paint System node points at afterwards has no user
    left, and Blender does not save it with the file. Images with a fake
    user are saved anyway. A generated image that was never painted,
    packed or saved holds nothing to lose. Both are left out.
    """
    changing = {node.as_pointer() for node in nodes}
    kept = set()
    for tree in ps_trees():
        for other in tree.nodes:
            linked = getattr(type(other), 'ps_linked_props', ()) if other.as_pointer() in changing else ()
            for name in _image_props(type(other)):
                kept.add(getattr(source if name in linked else other, name))
    lost = []
    for node in nodes:
        for name in type(node).ps_linked_props:
            image = getattr(node, name)
            if (not isinstance(image, bpy.types.Image) or image in kept or image.use_fake_user
                    or any(image == seen for _node, seen in lost)):
                continue
            if image.source == 'GENERATED' and not image.is_dirty and image.packed_file is None:
                continue
            lost.append((node, image))
    return lost


def link(nodes, source) -> None:
    """Link each of *nodes* with *source*, and give it *source*'s settings."""
    if not source.link_id:
        source.link_id = str(uuid.uuid4())
    left = {node.link_id for node in nodes if node.link_id and node.link_id != source.link_id}
    for node in nodes:
        node.link_id = source.link_id
    with suspend_compile(source.id_data):
        copy_settings(source, nodes)
    _drop_lone_ids(source.id_data, left)


def unlink(node) -> None:
    """Take *node* out of its link group and give it its own copy of shared content."""
    link_id = node.link_id
    node.link_id = ""
    node.own_content()
    _drop_lone_ids(node.id_data, {link_id})


def _drop_lone_ids(tree, link_ids) -> None:
    """Clear each of *link_ids* that only one layer in *tree* still has.

    A lone id links nothing. Clearing it keeps a later copy of that layer
    from reading as linked with it.
    """
    for link_id in link_ids:
        members = [node for node in tree.nodes if getattr(node, 'link_id', "") == link_id]
        if len(members) == 1:
            members[0].link_id = ""
