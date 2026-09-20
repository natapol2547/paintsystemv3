"""Layer list, moves and layer types (PS-011, PS-012, PS-029, PS-034)."""
import os
import sys
import traceback
from types import SimpleNamespace

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, finish, import_from, register_addon, section  # noqa: E402

register_addon()
core = import_from("compiler.core")
stack_ops = import_from("nodetree.stack_ops")
registry = import_from("nodes.layers.registry")
layer_ops = import_from("ops.layer_ops")
layers_panels = import_from("panels.layers_panels")
icon_kwargs = import_from("common").icon_kwargs
node_categories = import_from("nodetree").node_categories

SOLID = 'PaintSystemSolidColorLayerNode'
FOLDER = 'PaintSystemFolderLayerNode'
FIXTURE = [("A", 0), ("F", 0), ("G", 1), ("C", 2), ("B", 1), ("H", 0), ("D", 0)]


def new_tree(name):
    tree = bpy.data.node_groups.new(name, 'PaintSystemNodeTree')
    tree.initialize()
    return tree


def add(tree, bl_idname, name, target=None):
    node = tree.insert_layer_node(bl_idname, target=target)
    node.name = name
    return node


def build(name):
    """A, F [G [C], B], H [], D: nested folders, an empty folder and root layers."""
    tree = new_tree(name)
    with core.suspend_compile(tree):
        add(tree, SOLID, "D")
        add(tree, FOLDER, "H")
        f = add(tree, FOLDER, "F")
        b = add(tree, SOLID, "B", target=f)
        g = add(tree, FOLDER, "G", target=b)
        add(tree, SOLID, "C", target=g)
        add(tree, SOLID, "A")
    return tree


def layout(tree):
    return [(item.node.name, item.level) for item in tree.stack()]


def options_of(tree, name, direction):
    return [(option.action, option.target.name, option.placement,
             option.folder.name if option.folder is not None else None)
            for option in stack_ops.movement_options(tree.stack(), tree.nodes[name], direction)]


def check_current(tree, label):
    check(core.artifact_fingerprint(tree) == core.build_ir(tree).fingerprint(), f"{label}: artifact is current")


def check_alpha_mirrors(tree, label):
    check(stack_ops.repair_alpha_links(tree) == 0, f"{label}: alpha links follow colour links")


compiles = []
real_compile_tree = core.compile_tree


def counting_compile_tree(tree, **kwargs):
    compiles.append(tree.name)
    return real_compile_tree(tree, **kwargs)


try:
    section("movement options")
    tree = build("Options")
    check(layout(tree) == FIXTURE, f"fixture stack {layout(tree)}")
    up = {
        "A": [],
        "F": [('SKIP', "A", 'ABOVE', None)],
        "G": [('MOVE_OUT', "F", 'ABOVE', "F")],
        "C": [('MOVE_OUT', "G", 'ABOVE', "G")],
        "B": [('MOVE_ADJACENT', "C", 'BELOW', "G"), ('SKIP', "G", 'ABOVE', None)],
        "H": [('MOVE_ADJACENT', "B", 'BELOW', "F"), ('SKIP', "F", 'ABOVE', None)],
        "D": [('MOVE_INTO', "H", 'INTO_BOTTOM', "H"), ('SKIP', "H", 'ABOVE', None)],
    }
    down = {
        "A": [('MOVE_INTO_TOP', "F", 'INTO_TOP', "F"), ('SKIP', "F", 'BELOW', None)],
        "F": [('MOVE_INTO_TOP', "H", 'INTO_TOP', "H"), ('SKIP', "H", 'BELOW', None)],
        "G": [('SKIP', "B", 'BELOW', None)],
        "C": [('MOVE_OUT_BOTTOM', "G", 'BELOW', "G")],
        "B": [('MOVE_INTO_TOP', "H", 'INTO_TOP', "H"), ('MOVE_OUT_BOTTOM', "F", 'BELOW', "F")],
        "H": [('SKIP', "D", 'BELOW', None)],
        "D": [],
    }
    for direction, table in (('UP', up), ('DOWN', down)):
        for name, want in table.items():
            got = options_of(tree, name, direction)
            check(got == want, f"{name} {direction}: {got}")
    with core.suspend_compile(tree):
        stack_ops.move(tree, tree.active_channel.name, tree.nodes["B"], 'UP', 'SKIP')
    check(options_of(tree, "C", 'DOWN') == [
        ('MOVE_INTO_TOP', "H", 'INTO_TOP', "H"),
        ('MOVE_OUT_BOTTOM', "G", 'BELOW', "G"),
        ('MOVE_ADJACENT', "H", 'ABOVE', None),
    ], f"last layer two folders deep may leave one or both {options_of(tree, 'C', 'DOWN')}")
    labels = [layer_ops.move_label(option) for option in
              stack_ops.movement_options(tree.stack(), tree.nodes["C"], 'DOWN')]
    check(labels == ["Move into 'H'", "Move out of 'G'", "Move to top level"], f"labels {labels}")

    section("moves")
    core.compile_tree = counting_compile_tree
    moves = [
        ("B", 'UP', 'MOVE_ADJACENT', [("A", 0), ("F", 0), ("G", 1), ("C", 2), ("B", 2), ("H", 0), ("D", 0)]),
        ("B", 'UP', 'SKIP', [("A", 0), ("F", 0), ("B", 1), ("G", 1), ("C", 2), ("H", 0), ("D", 0)]),
        ("D", 'UP', 'MOVE_INTO', [("A", 0), ("F", 0), ("G", 1), ("C", 2), ("B", 1), ("H", 0), ("D", 1)]),
        ("G", 'UP', 'MOVE_OUT', [("A", 0), ("G", 0), ("C", 1), ("F", 0), ("B", 1), ("H", 0), ("D", 0)]),
        ("A", 'DOWN', 'SKIP', [("F", 0), ("G", 1), ("C", 2), ("B", 1), ("A", 0), ("H", 0), ("D", 0)]),
        ("C", 'DOWN', 'MOVE_OUT_BOTTOM', [("A", 0), ("F", 0), ("G", 1), ("C", 1), ("B", 1), ("H", 0), ("D", 0)]),
        ("F", 'DOWN', 'MOVE_INTO_TOP', [("A", 0), ("H", 0), ("F", 1), ("G", 2), ("C", 3), ("B", 2), ("D", 0)]),
    ]
    for index, (name, direction, action, want) in enumerate(moves):
        tree = build(f"Move {index}")
        core.compile_tree(tree)
        compiles.clear()
        label = f"{name} {direction} {action}"
        check(tree.move_layer_node(tree.nodes[name], direction, action), f"{label}: moved")
        check(layout(tree) == want, f"{label}: {layout(tree)}")
        check(compiles == [tree.name], f"{label}: compiled once {compiles}")
        check_current(tree, label)
        check_alpha_mirrors(tree, label)

    tree = build("Leap")
    tree.move_layer_node(tree.nodes["B"], 'UP', 'SKIP')
    check(tree.move_layer_node(tree.nodes["C"], 'DOWN', 'MOVE_ADJACENT'), "leap moved")
    check(layout(tree) == [("A", 0), ("F", 0), ("B", 1), ("G", 1), ("C", 0), ("H", 0), ("D", 0)],
          f"MOVE_ADJACENT down leaves both folders {layout(tree)}")

    tree = build("Refused")
    check(not tree.move_layer_node(tree.nodes["A"], 'UP', 'SKIP'), "a move not on offer is refused")
    check(not tree.move_layer_node(tree.nodes["C"], 'DOWN', 'SKIP'), "no SKIP without a next sibling")
    check(layout(tree) == FIXTURE, "a refused move changes nothing")

    tree = build("Reveal")
    tree.nodes["F"].is_expanded = False
    tree.move_layer_node(tree.nodes["B"], 'UP', 'SKIP')
    check(tree.nodes["F"].is_expanded, "moving a layer expands the folder it lands in")
    tree.nodes["G"].is_expanded = False
    add(tree, SOLID, "E", target=tree.nodes["G"])
    check(tree.nodes["G"].is_expanded, "adding into a collapsed folder expands it")
    core.compile_tree = real_compile_tree

    section("move operators")
    tree = build("Operators")
    core.compile_tree(tree)
    bpy.context.scene.paint_system.active_node_tree = tree
    tree.nodes.active = tree.nodes["A"]
    check(not bpy.ops.paint_system.move_layer_up.poll(), "the top layer cannot move up")
    check(bpy.ops.paint_system.move_layer_down.poll(), "the top layer can move down")
    check(bpy.ops.paint_system.move_layer_down() == {'CANCELLED'}, "several moves on offer need a choice")
    check(bpy.ops.paint_system.move_layer_down(action='SKIP') == {'FINISHED'}, "a chosen move runs")
    check(layout(tree)[:5] == [("F", 0), ("G", 1), ("C", 2), ("B", 1), ("A", 0)], f"A skipped F {layout(tree)}")
    tree.nodes.active = tree.nodes["G"]
    check(bpy.ops.paint_system.move_layer_up() == {'FINISHED'}, "the only move on offer runs without asking")
    check(layout(tree)[:2] == [("G", 0), ("C", 1)], f"G moved out of F {layout(tree)}")
    check_current(tree, "after move operators")

    bpy.ops.ed.undo_push(message="Layers test start")
    before = layout(tree)
    check(bpy.ops.paint_system.move_layer_down('EXEC_DEFAULT', True, action='SKIP') == {'FINISHED'},
          "move with an undo push")
    check(layout(tree) != before, "moved before undo")
    bpy.ops.ed.undo()
    tree = bpy.data.node_groups["Operators"]
    check(layout(tree) == before, f"undo restores the stack {layout(tree)}")
    check_current(tree, "after undo move")

    section("layer rows")
    tree = build("Rows")
    rows = layers_panels.layer_rows(tree)
    check([(name, row.level) for name, row in sorted(rows.items(), key=lambda kv: kv[1].order)] == FIXTURE,
          "rows follow the stack")
    check(all(row.visible and row.parent_enabled for row in rows.values()), "expanded, enabled folders show every row")
    tree.nodes["G"].is_expanded = False
    tree.nodes["F"].enabled = False
    rows = layers_panels.layer_rows(tree)
    check(not rows["C"].visible and rows["G"].visible and rows["B"].visible, "a collapsed folder hides its content")
    check(not rows["C"].parent_enabled and not rows["B"].parent_enabled and not rows["G"].parent_enabled,
          "content of a disabled folder is greyed, however deep")
    check(rows["F"].parent_enabled and rows["H"].parent_enabled, "the disabled folder itself is not")

    shown_flag = 1
    flags, order = layers_panels.PAINTSYSTEM_UL_layers.filter_items(
        SimpleNamespace(bitflag_filter_item=shown_flag), bpy.context, tree, "nodes")
    names = [node.name for node in tree.nodes]
    check(sorted(order) == list(range(len(names))), f"the new order is a permutation {order}")
    ordered = [name for _position, name in sorted(zip(order, names))]
    check(ordered[:len(FIXTURE)] == [name for name, _level in FIXTURE], f"list order is stack order {ordered}")
    shown = {name for name, flag in zip(names, flags) if flag & shown_flag}
    check(shown == {"A", "F", "G", "B", "H", "D"}, f"collapsed content and group nodes are filtered {shown}")

    section("active layer index")
    tree = build("Index")
    # Undo above freed every ID, so the scene is looked up again.
    scene = bpy.context.scene
    scene.paint_system.active_node_tree = tree
    image_node = registry.layer_type('IMAGE').create(tree, resolution='1024')
    core.compile_tree(tree)
    fingerprint = core.artifact_fingerprint(tree)
    b = tree.nodes["B"]
    tree.active_layer_index = tree.nodes.find("B")
    check(tree.nodes.active == b and tree.active_layer_index == tree.nodes.find("B"), "the index selects a layer")
    check(b.select and not image_node.select, "only the active layer is selected")
    tree.active_layer_index = tree.nodes.find(tree.get_output_node().name)
    check(tree.nodes.active == b, "an index outside the stack's layers is ignored")
    scene.tool_settings.image_paint.canvas = None
    tree.active_layer_index = tree.nodes.find(image_node.name)
    check(scene.tool_settings.image_paint.canvas == image_node.image, "selecting an image layer paints on it")
    check(core.artifact_fingerprint(tree) == fingerprint, "selection does not recompile")

    section("duplicating a layer")
    tree = build("Duplicate")
    original = tree.nodes["A"]
    cache_image = bpy.data.images.new("Cache For A", 8, 8)
    original.cache_image = cache_image
    original.cache_enabled = True
    original.cache_hash = "deadbeef"
    original.cache_uv_map = "UVMap"
    duplicate_tree = tree.copy()
    duplicate = duplicate_tree.nodes["A"]
    check(duplicate.uuid != original.uuid, "the copy gets its own uuid")
    check(duplicate.cache_image is None and not duplicate.cache_enabled
          and duplicate.cache_hash == "" and duplicate.cache_uv_map == "",
          "and no cache, so baking it cannot overwrite the original's pixels")
    check(original.cache_image == cache_image and original.cache_enabled
          and original.cache_hash == "deadbeef", "the original keeps its own")
    plain = duplicate_tree.nodes["B"]
    check(plain.cache_image is None and plain.uuid != tree.nodes["B"].uuid,
          "a layer that had no cache copies unchanged")
    bpy.data.node_groups.remove(duplicate_tree)
    bpy.data.images.remove(cache_image)

    section("layer type registry")
    types = registry.layer_types()
    check([cls.ps_type for cls in types] == ['FOLDER', 'SOLID_COLOR', 'IMAGE', 'FILTER'], "menu order")
    enum = bpy.ops.paint_system.add_layer.get_rna_type().properties['layer_type'].enum_items
    check([item.identifier for item in enum] == [cls.ps_type for cls in types], "add_layer offers the registry")
    for cls in types:
        check(cls.ps_label and cls.ps_description and cls.ps_menu_section, f"{cls.ps_type} has menu text")
        check(icon_kwargs(*cls.ps_icon) != {'icon': 'NONE'}, f"{cls.ps_type} icon resolves {icon_kwargs(*cls.ps_icon)}")
        description = layer_ops.PAINTSYSTEM_OT_add_layer.description(bpy.context, SimpleNamespace(layer_type=cls.ps_type))
        check(description.startswith(cls.ps_description), f"{cls.ps_type} tooltip")
    check(registry.layer_type('IMAGE').bl_idname == 'PaintSystemImageLayerNode' and registry.layer_type('NOPE') is None,
          "lookup by type")
    category_items = [item.nodetype for item in node_categories()[0].items(None)]
    check(all(cls.bl_idname in category_items for cls in types), f"node editor Layers category {category_items}")

    tree = new_tree("Types")
    scene.paint_system.active_node_tree = tree
    added = {}
    for cls in types:
        result = bpy.ops.paint_system.add_layer(layer_type=cls.ps_type, resolution='1024')
        check(result == {'FINISHED'} and tree.nodes.active.bl_idname == cls.bl_idname, f"add {cls.ps_type}")
        added[cls.ps_type] = tree.nodes.active
    image = added['IMAGE'].image
    check(image is not None and tuple(image.size) == (1024, 1024), "the image layer gets an image at the resolution")
    check([(name.split()[0], level) for name, level in layout(tree)]
          == [("Folder", 0), ("Filter", 1), ("Image", 1), ("Solid", 1)],
          f"into the active folder, then above the active layer {layout(tree)}")
    check_current(tree, "after adding every type")
except Exception:
    traceback.print_exc()
    check(False, "unexpected exception")
finally:
    core.compile_tree = real_compile_tree

finish("LAYERS TEST")
