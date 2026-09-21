"""Artifact parity: an edited artifact equals one compiled from scratch (PS-081).

Every edit patches ``tree.compiled`` in place instead of rebuilding it, so the
artifact can only be trusted as far as the patching is exact. This walks a
40-layer tree of images, solids, folders and clip runs through a long edit
sequence and checks three things after every step:

* the fingerprint stored on the artifact equals a fresh ``build_ir``;
* a second compile of the unchanged tree changes nothing;
* the patched artifact holds what a compile from scratch would have produced.

Node locations and link order are left out of the comparison. Incremental
layout keeps nodes where they already were, and link order follows set
iteration, so both differ from a from-scratch build without meaning anything.

Run:  blender -b --factory-startup --python tests/test_parity.py
"""
import os
import sys
import traceback

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, finish, import_from, register_addon, section  # noqa: E402

register_addon()
core = import_from("compiler.core")
builder = import_from("compiler.builder")
stack_ops = import_from("nodetree.stack_ops")

IMAGE = 'PaintSystemImageLayerNode'
SOLID = 'PaintSystemSolidColorLayerNode'
FOLDER = 'PaintSystemFolderLayerNode'

LAYERS = 40
BLEND_MODES = ('MIX', 'MULTIPLY', 'SCREEN', 'OVERLAY')
FILL_COLORS = ((0.8, 0.2, 0.2, 1.0), (0.2, 0.7, 0.3, 1.0),
               (0.2, 0.3, 0.9, 0.6), (0.9, 0.8, 0.1, 1.0))

# One pass of the pattern is ten layers: a clip run of two over an image,
# a folder with content, and plain layers around them.
PATTERN = (
    (IMAGE, False, ()),
    (SOLID, False, ()),
    (IMAGE, False, ()),      # the base the two layers above clip onto
    (IMAGE, True, ()),
    (SOLID, True, ()),
    (IMAGE, False, ()),
    (FOLDER, False, (IMAGE, SOLID)),
    (IMAGE, False, ()),
)

# Base Node properties the compiler never writes, kept in the snapshot so a
# stray write to one of them shows up as a difference.
BASE_PROPS = ('mute', 'hide', 'label', 'width')


# ── Snapshots ────────────────────────────────────────────────────────


def value(raw):
    """An RNA value in a form two artifacts can be compared by.

    Datablocks compare by name, since the two artifacts must point at the
    same ones; anything else without a stable value compares by type only.
    """
    if raw is None or isinstance(raw, (bool, int, float, str)):
        return raw
    if isinstance(raw, bpy.types.ID):
        return ("id", type(raw).__name__, raw.name_full)
    if isinstance(raw, set):
        return tuple(sorted(raw))
    if isinstance(raw, bpy.types.ColorRamp):
        return ("ramp", raw.color_mode, raw.interpolation,
                tuple((element.position, tuple(element.color)) for element in raw.elements))
    try:
        return tuple(value(item) for item in raw)
    except TypeError:
        return f"<{type(raw).__name__}>"


def socket_index(socket):
    sockets = socket.node.outputs if socket.is_output else socket.node.inputs
    return next(i for i, other in enumerate(sockets) if other == socket)


def socket_state(socket):
    """A socket's identity and, where the shader reads it, its value.

    The value of a linked socket is dead data: the IR sets values only for
    the inputs it leaves unlinked, so a socket that was a literal before an
    edit keeps that literal once something feeds it, while a compile from
    scratch leaves the socket at its library default. Whether a socket is
    linked at all is compared, here and through the link set.
    """
    state = {'identifier': socket.identifier, 'name': socket.name, 'type': socket.bl_idname,
             'enabled': socket.enabled, 'hide': socket.hide, 'hide_value': socket.hide_value,
             'linked': socket.is_linked}
    if hasattr(socket, 'default_value') and not socket.is_linked:
        state['default_value'] = value(socket.default_value)
    return state


def interface_state(artifact):
    items = []
    for item in artifact.interface.items_tree:
        entry = {'item_type': item.item_type, 'name': item.name}
        if item.item_type == 'SOCKET':
            entry['in_out'] = item.in_out
            for attr in ('socket_type', 'default_value', 'min_value', 'max_value',
                         'subtype', 'hide_value'):
                if hasattr(item, attr):
                    entry[attr] = value(getattr(item, attr))
        items.append(entry)
    return items


def snapshot(artifact):
    """What a compile from scratch has to reproduce, keyed by ps_identifier.

    ``node_state`` is the compiler's own idea of which properties reach the
    IR, so the snapshot cannot drift from what the fingerprint covers.
    """
    identifiers = {node.as_pointer(): builder.node_identifier(node) for node in artifact.nodes}
    nodes = {}
    for node in artifact.nodes:
        nodes[identifiers[node.as_pointer()]] = {
            'bl_idname': node.bl_idname,
            'props': {name: value(raw) for name, raw in core.node_state(node).items()},
            'base': {name: value(getattr(node, name)) for name in BASE_PROPS},
            'inputs': [socket_state(socket) for socket in node.inputs],
            'outputs': [socket_state(socket) for socket in node.outputs],
        }
    links = sorted(
        (identifiers[link.from_node.as_pointer()], socket_index(link.from_socket),
         identifiers[link.to_node.as_pointer()], socket_index(link.to_socket), link.is_muted)
        for link in artifact.links)
    return {'node_count': len(artifact.nodes), 'nodes': nodes, 'links': links,
            'interface': interface_state(artifact)}


def brief(raw, width=90):
    text = repr(raw)
    return text if len(text) <= width else text[:width] + "..."


def first_difference(expected, got):
    """Where two snapshot fields disagree, as one readable line."""
    if isinstance(expected, dict) and isinstance(got, dict):
        for key in sorted(set(expected) | set(got)):
            if expected.get(key) != got.get(key):
                return f"{key}: {brief(expected.get(key))} != {brief(got.get(key))}"
    if isinstance(expected, list) and isinstance(got, list):
        if len(expected) != len(got):
            return f"{len(expected)} entries != {len(got)}"
        for index, (one, other) in enumerate(zip(expected, got)):
            if one != other:
                return f"[{index}] {first_difference(one, other)}"
    return f"{brief(expected)} != {brief(got)}"


def differences(expected, got, limit=3):
    """Up to *limit* reasons why two snapshots are not the same artifact."""
    found = []
    if expected['interface'] != got['interface']:
        found.append(f"interface: {first_difference(expected['interface'], got['interface'])}")
    for identifier in sorted(set(expected['nodes']) ^ set(got['nodes'])):
        found.append(f"{'missing' if identifier in expected['nodes'] else 'unexpected'} "
                     f"node {identifier}")
    for identifier in sorted(set(expected['nodes']) & set(got['nodes'])):
        one, other = expected['nodes'][identifier], got['nodes'][identifier]
        for field in ('bl_idname', 'props', 'base', 'inputs', 'outputs'):
            if one[field] != other[field]:
                found.append(f"{identifier} {field}: {first_difference(one[field], other[field])}")
    if expected['links'] != got['links']:
        missing = [link for link in expected['links'] if link not in got['links']]
        extra = [link for link in got['links'] if link not in expected['links']]
        found.append(f"links: {len(missing)} missing, {len(extra)} unexpected, "
                     f"e.g. {brief((missing or extra)[0])}")
    if expected['node_count'] != got['node_count'] and not found:
        found.append(f"node count {expected['node_count']} != {got['node_count']} "
                     "(identifiers are not unique)")
    return found[:limit]


def compile_from_scratch(tree):
    """Snapshot of a compile into an empty artifact, leaving the live one alone.

    The patched artifact is put back afterwards so the next step keeps
    building on it, and any drift keeps accumulating across the sequence.
    """
    patched = tree.compiled
    tree.compiled = None
    try:
        core.compile_tree(tree)
        return snapshot(tree.compiled)
    finally:
        scratch = tree.compiled
        tree.compiled = patched
        if scratch is not None and scratch != patched:
            bpy.data.node_groups.remove(scratch)


# ── The tree under test ──────────────────────────────────────────────


def stack_plan(total):
    """``(bl_idname, clip, child idnames)`` for a tree of *total* layers."""
    plan, count = [], 0
    while count < total:
        for kind, clip, children in PATTERN:
            if count >= total:
                break
            if count + 1 + len(children) > total:
                kind, clip, children = IMAGE, False, ()
            plan.append((kind, clip, children))
            count += 1 + len(children)
    return plan


def configure(node, index, images):
    """Give layer *index* its content, blend mode and opacity, deterministically."""
    node.blend_mode = BLEND_MODES[index % len(BLEND_MODES)]
    node.opacity = (index % 5 + 6) / 10.0
    if node.bl_idname == IMAGE:
        node.image = images[index % len(images)]
    elif node.bl_idname == SOLID:
        node.fill_color = FILL_COLORS[index % len(FILL_COLORS)]


def build_tree(name, total, images):
    tree = bpy.data.node_groups.new(name, 'PaintSystemNodeTree')
    tree.initialize()
    index = 0
    with core.suspend_compile(tree):
        for kind, clip, children in stack_plan(total):
            node = tree.insert_layer_node(kind)
            configure(node, index, images)
            node.is_clip = clip
            index += 1
            for child_kind in children:
                child = tree.insert_layer_node(child_kind, target=node)
                configure(child, index, images)
                index += 1
    return tree


try:
    section("build")
    images = [bpy.data.images.new(f"PS Parity {i}", 8, 8, alpha=True) for i in range(3)]
    tree = build_tree("Parity", LAYERS, images)
    items = tree.stack()
    check(len(items) == LAYERS, f"{len(items)} layers in the stack, expected {LAYERS}")
    check(tree.compiled is not None, "the tree compiled on leaving suspend_compile")

    kinds = {item.node.bl_idname for item in items}
    check(kinds == {IMAGE, SOLID, FOLDER}, f"the stack mixes the layer types {sorted(kinds)}")
    check(sum(1 for item in items if item.node.is_clip) >= 2, "the stack holds clipped layers")
    check(sum(1 for item in items if item.level > 0) >= 2, "the stack holds folder content")

    # The layers the edit sequence works on. A plain layer near the middle
    # carries the property edits; the clip run, solid and folder cover the
    # emit paths that a plain layer does not reach.
    middle = len(items) // 2
    plain = [(abs(index - middle), item.node) for index, item in enumerate(items)
             if item.level == 0 and item.node.bl_idname == IMAGE
             and not item.node.is_clip and not stack_ops.feeds_clip_run(item.node)]
    mid = min(plain, key=lambda entry: entry[0])[1]
    base = next(item.node for item in items
                if stack_ops.feeds_clip_run(item.node) and not item.node.is_clip)
    clipped_top = next(item.node for item in items
                       if item.node.is_clip and not stack_ops.feeds_clip_run(item.node))
    solid = next(item.node for item in items if item.node.bl_idname == SOLID)
    folder = next(item.node for item in items if item.node.bl_idname == FOLDER)
    textured = next(item.node for item in reversed(items)
                    if item.node.bl_idname == IMAGE and item.node != mid)

    steps = 0

    def step(label, edit=None):
        """Run one edit, then check the artifact three ways."""
        global steps
        steps += 1
        if edit is not None:
            edit()
        # A node editor edit leaves its build to a timer that never fires headless.
        core.flush_now()
        patched = snapshot(tree.compiled)
        stored = tree.compiled.get(core.ARTIFACT_FINGERPRINT_KEY)
        check(stored == core.build_ir(tree).fingerprint(),
              f"{label}: the artifact carries the fingerprint of the current tree")

        core.mark_dirty(tree)
        core.flush_now()
        again = snapshot(tree.compiled)
        check(again == patched and tree.compiled.get(core.ARTIFACT_FINGERPRINT_KEY) == stored,
              f"{label}: an unchanged recompile changes nothing"
              f"{'' if again == patched else ' - ' + '; '.join(differences(patched, again))}")

        fresh = compile_from_scratch(tree)
        reasons = differences(fresh, patched)
        check(not reasons, f"{label}: matches a compile from scratch"
                           f"{'' if not reasons else ' - ' + '; '.join(reasons)}")

    def move_layer(node, direction, action=None):
        """Take the move the stack offers *node*, or the named one."""
        options = stack_ops.movement_options(tree.stack(), node, direction)
        if action is not None:
            options = [option for option in options if option.action == action]
        moved = bool(options) and tree.move_layer_node(node, direction, options[0].action)
        check(moved, f"{node.name} moves {direction.lower()}"
                     f"{'' if action is None else ' (' + action + ')'}")

    def move_into_folder():
        """Take the row under the new folder into it."""
        move_layer(tree.stack()[1].node, 'UP', 'MOVE_INTO')

    def relink_color_by_hand():
        """A node editor edit that moves a Color link and leaves Alpha behind.

        Odd but valid: the next stack edit repairs the alpha links.
        """
        top, second = (item.node for item in tree.stack()[:2])
        output = tree.get_output_node()
        tree.links.new(second.outputs['Color'], output.inputs['Color'])
        tree.links.new(top.outputs['Color'], second.inputs['Color'])

    held = {}

    section("no edit")
    step("built")

    section("property edits")
    step("enabled off", lambda: setattr(mid, 'enabled', False))
    step("opacity while disabled", lambda: setattr(mid, 'opacity', 0.25))
    step("enabled on", lambda: setattr(mid, 'enabled', True))
    step("opacity 1/3", lambda: setattr(mid, 'opacity', 1.0 / 3.0))
    step("opacity 0.123456789", lambda: setattr(mid, 'opacity', 0.123456789))
    step("blend mode", lambda: setattr(mid, 'blend_mode', 'MULTIPLY'))
    step("clip base opacity", lambda: setattr(base, 'opacity', 0.6))
    step("clipped top opacity", lambda: setattr(clipped_top, 'opacity', 0.3))
    step("solid fill colour", lambda: setattr(solid, 'fill_color', (0.3, 0.123456789, 0.9, 0.5)))
    step("folder opacity", lambda: setattr(folder, 'opacity', 0.7))
    step("folder collapsed", lambda: setattr(folder, 'is_expanded', False))
    step("uv map set", lambda: setattr(textured, 'uv_map', "UVMap"))
    step("uv map cleared", lambda: setattr(textured, 'uv_map', ""))
    held['image'] = textured.image
    step("image cleared", lambda: setattr(textured, 'image', None))
    step("image restored", lambda: setattr(textured, 'image', held.pop('image')))

    section("structural edits")
    step("solid inserted", lambda: held.__setitem__('solid', tree.insert_layer_node(SOLID, target=mid)))
    step("inserted solid filled", lambda: setattr(held['solid'], 'fill_color', (0.1, 0.2, 0.3, 0.4)))
    step("inserted solid clipped", lambda: setattr(held['solid'], 'is_clip', True))
    step("new clip base opacity", lambda: setattr(mid, 'opacity', 0.9))
    step("inserted solid unclipped", lambda: setattr(held['solid'], 'is_clip', False))
    step("solid removed", lambda: tree.remove_layer_node(held.pop('solid')))
    step("moved up", lambda: move_layer(mid, 'UP'))
    step("moved down", lambda: move_layer(mid, 'DOWN'))
    step("folder inserted", lambda: held.__setitem__('folder', tree.insert_layer_node(FOLDER)))
    step("moved into the folder", move_into_folder)
    step("folder removed", lambda: tree.remove_layer_node(held.pop('folder')))

    section("hand edits and repair")
    step("colour relinked by hand", relink_color_by_hand)
    step("repaired by the next insert", lambda: tree.insert_layer_node(SOLID))
    step("forced recompile", lambda: core.compile_tree(tree, force=True))

    section("coverage")
    check(steps == 30, f"{steps} edit steps checked")

except Exception:
    traceback.print_exc()
    check(False, "exception")

finish("PARITY TEST")
