"""The 3D view selection tools: outline geometry and the operators' execute (PS-093).

`tools.shapes` is plain Python and is checked value by value. The
operators are run with `EXEC_DEFAULT` in Texture Paint mode, the way
Adjust Last Operation runs them, with the points and the view a drag
would have stored. Building the mask is the session's job and is not
checked here; the windowed test drags the tools for real.

Run:  blender -b --factory-startup --python tests/test_selection_tools.py
"""
import math
import os
import sys

import bpy
from mathutils import Euler, Matrix

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import before, check, finish, guarded, import_from, register_addon, section  # noqa: E402

register_addon()
tools = import_from("tools")
shapes = import_from("tools.shapes")
select_ops = import_from("tools.select_ops")
workspace_tools = import_from("tools.workspace_tools")
undo = import_from("undo")
session = import_from("selection.session")
raster = import_from("selection.raster")

OPERATORS = (
    (bpy.ops.paint_system.select_box, select_ops.PAINTSYSTEM_OT_select_box, 'BOX'),
    (bpy.ops.paint_system.select_ellipse, select_ops.PAINTSYSTEM_OT_select_ellipse, 'ELLIPSE'),
    (bpy.ops.paint_system.select_lasso, select_ops.PAINTSYSTEM_OT_select_lasso, 'LASSO'),
)


def close(a, b, tol=1e-9):
    return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))


def close_points(a, b, tol=1e-9):
    return len(a) == len(b) and all(close(p, q, tol) for p, q in zip(a, b))


def cube():
    return bpy.data.objects["Cube"]


def tree():
    return cube().active_material.paint_system.tree


def test_box_corners():
    section("box corners")
    start, end = (10.0, 20.0), (30.0, 50.0)
    check(shapes.box_corners(start, end, False, False) == ((10.0, 20.0), (30.0, 50.0)),
          "a plain drag gives the start and end as corners")
    check(shapes.box_corners(start, end, True, False) == ((10.0, 20.0), (40.0, 50.0)),
          "square makes both sides as long as the longer one")
    check(shapes.box_corners(start, (0.0, 5.0), True, False) == ((10.0, 20.0), (-5.0, 5.0)),
          "square keeps the direction of the drag")
    check(shapes.box_corners(start, end, False, True) == ((-10.0, -10.0), (30.0, 50.0)),
          "centre makes the start the middle of the box")
    check(shapes.box_corners(start, end, True, True) == ((-20.0, -10.0), (40.0, 50.0)),
          "square and centre together")


def test_ellipse_outline():
    section("ellipse outline")
    points = shapes.ellipse_outline((10.0, 20.0), (50.0, 40.0))
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    check(len(points) == 64, f"64 segments by default ({len(points)})")
    check(len(shapes.ellipse_outline((0, 0), (1, 1), segments=16)) == 16, "segments sets the count")
    check(math.isclose(min(xs), 10.0) and math.isclose(max(xs), 50.0)
          and math.isclose(min(ys), 20.0, abs_tol=0.1) and max(ys) <= 40.0 and max(ys) > 39.9,
          f"the outline touches the box and stays inside it (x {min(xs)}..{max(xs)}, y {min(ys)}..{max(ys)})")
    check(close_points(shapes.ellipse_outline((50.0, 40.0), (10.0, 20.0)), points),
          "the corner order does not matter")


def test_lasso_append():
    section("lasso points")
    points = [(0.0, 0.0)]
    check(shapes.lasso_append(points, (1.0, 1.0), 2.0) == 2.0 and len(points) == 1,
          "a point closer than the step is not appended")
    check(shapes.lasso_append(points, (2.0, 0.0), 2.0) == 2.0 and points[-1] == (2.0, 0.0),
          "a point at the step is appended")
    points = [(0.0, 0.0)]
    for i in range(1, shapes.LASSO_MAX_POINTS):
        shapes.lasso_append(points, (float(i), 0.0), 1.0)
    check(len(points) == shapes.LASSO_MAX_POINTS, f"up to the limit every point is kept ({len(points)})")
    step = shapes.lasso_append(points, (float(shapes.LASSO_MAX_POINTS), 0.0), 1.0)
    check(len(points) <= shapes.LASSO_MAX_POINTS // 2 + 1 and points[0] == (0.0, 0.0)
          and points[-1] == (float(shapes.LASSO_MAX_POINTS), 0.0) and step == 2.0,
          f"past the limit every other point goes, keeping the first and the newest, and the step doubles "
          f"({len(points)} points, step {step})")

    # A circle of radius 400 traced twelve times, about 30,000 pixels at
    # a step of 2: several overflows, each of which must thin the whole
    # lasso evenly rather than the start again and again.
    step, points = 2.0, [(400.0, 0.0)]
    count = 15000
    for i in range(1, count + 1):
        angle = 24.0 * math.pi * i / count
        step = shapes.lasso_append(points, (400.0 * math.cos(angle), 400.0 * math.sin(angle)), step)
    gaps = [math.dist(a, b) for a, b in zip(points, points[1:])]
    check(step >= 8.0 and max(gaps) <= 2.0 * step + 1.0 and len(points) <= shapes.LASSO_MAX_POINTS,
          f"after {int(math.log2(step / 2.0))} overflows no gap is longer than twice the step "
          f"(largest {max(gaps):.1f} px at the start {max(gaps[:50]):.1f} px, step {step})")


def test_degenerate():
    section("degenerate outlines")
    for kind in ('BOX', 'ELLIPSE'):
        check(shapes.degenerate(kind, [(1, 1), (1, 9)]), f"a {kind} with no width")
        check(shapes.degenerate(kind, [(1, 1), (9, 1)]), f"a {kind} with no height")
        check(shapes.degenerate(kind, [(1, 1)]), f"a {kind} with one corner")
        check(not shapes.degenerate(kind, [(9, 9), (1, 1)]), f"a {kind} dragged up and left is not degenerate")
    check(shapes.degenerate('LASSO', [(0, 0), (5, 5)]), "a two-point lasso")
    check(shapes.degenerate('LASSO', [(0, 0), (5, 5), (0, 0), (5, 5)]), "a lasso with two distinct points")
    check(shapes.degenerate('LASSO', [(0, 0), (1, 2), (2, 4), (-3, -6)]), "a collinear lasso")
    check(not shapes.degenerate('LASSO', [(0, 0), (4, 0), (4, 3)]), "a triangle is not degenerate")
    check(not shapes.degenerate('LASSO', [(0, 0), (4, 4), (4, 0), (0, 4)]),
          "a self-crossing lasso with zero signed area is not degenerate")


def test_clockwise_and_quads():
    section("preview geometry")
    ccw = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    check(shapes.signed_area(ccw) > 0.0, "a counter-clockwise square has a positive area")
    check(shapes.clockwise(ccw) == list(reversed(ccw)), "clockwise reverses a counter-clockwise outline")
    cw = list(reversed(ccw))
    check(shapes.clockwise(cw) == cw, "and keeps a clockwise one")
    positions, tangents = shapes.quad_strip(ccw, True, 2.0)
    check(len(positions) == 24 and len(tangents) == 24, f"a closed square is four quads ({len(positions)})")
    positions, _ = shapes.quad_strip(ccw, False, 2.0)
    check(len(positions) == 18, f"an open outline has one segment fewer ({len(positions)})")
    positions, tangents = shapes.quad_strip([(0.0, 0.0), (10.0, 0.0)], False, 2.0)
    xs, ys = [p[0] for p in positions], [p[1] for p in positions]
    check((min(xs), max(xs), min(ys), max(ys)) == (-1.0, 11.0, -1.0, 1.0),
          f"a segment is as wide as asked and extends half the width at each end ({min(xs)}..{max(xs)})")
    check(all(t == (1.0, 0.0) for t in tangents), "every vertex carries the segment's unit tangent")
    positions, _ = shapes.quad_strip([(0.0, 0.0), (0.0, 0.0), (5.0, 0.0)], False, 2.0)
    check(len(positions) == 6, "a zero-length segment is skipped")


def test_keymap_and_tools():
    section("tools and keymap")
    check([tool.bl_label for tool in workspace_tools.TOOLS]
          == ["Lasso Selection", "Rectangle Selection", "Ellipse Selection"], "tool labels, Lasso Selection first")
    check([tool.bl_idname for tool in workspace_tools.TOOLS]
          == ["paint_system.select_lasso", "paint_system.select_box", "paint_system.select_ellipse"],
          "each tool runs the operator of the same id")
    check([tool.bl_icon for tool in workspace_tools.TOOLS]
          == ["ops.generic.select_lasso", "ops.generic.select_box", "ops.generic.select_circle"],
          "Blender's own select icons")
    items = workspace_tools.shape_keymap("paint_system.select_box")
    summary = [(idname, event.get("shift", False), event.get("ctrl", False), event["value"], dict(props["properties"]))
               for idname, event, props in items]
    check(summary == [
        ("paint_system.select_box", False, False, 'CLICK_DRAG', {"mode": 'REPLACE'}),
        ("paint_system.select_all", False, False, 'CLICK', {"action": 'DESELECT', "from_tool_click": True}),
        ("paint_system.select_box", True, False, 'CLICK_DRAG', {"mode": 'ADD'}),
        ("paint_system.select_box", False, True, 'CLICK_DRAG', {"mode": 'SUBTRACT'}),
        ("paint_system.select_box", True, True, 'CLICK_DRAG', {"mode": 'INTERSECT'}),
    ], f"five items: a drag per mode and, neither first nor last, a click that deselects ({summary})")
    marker = bpy.ops.paint_system.select_all.get_rna_type().properties["from_tool_click"]
    check(marker.is_hidden and marker.is_skip_save,
          "from_tool_click stays out of Adjust Last Operation and is not remembered")
    check(all(event["type"] == 'LEFTMOUSE' and not event.get("alt") for _, event, _ in items),
          "every item is the left mouse button without Alt")
    for _, cls, kind in OPERATORS:
        check(cls.bl_options == undo.UNDO_OPTIONS, f"{kind} uses the selection undo options")
    check(tools.default_brush_tool() == ('builtin_brush.Draw' if before(4, 3) else 'builtin.brush'),
          f"default brush tool id ({tools.default_brush_tool()})")


def setup():
    obj = cube()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.paint_system.setup_material('EXEC_DEFAULT')
    bpy.ops.paint_system.add_layer('EXEC_DEFAULT', layer_type='IMAGE', resolution='1024')
    obj.matrix_world = Matrix.LocRotScale((0.5, -1.0, 0.25), Euler((0.3, 0.2, 0.1)), (1.0, 2.0, 1.0))
    bpy.ops.object.mode_set(mode='TEXTURE_PAINT')


VIEW = Matrix.LocRotScale((0.1, -0.2, -8.0), Euler((1.1, 0.0, 0.8)), None)
WINDOW = Matrix(((2.0, 0.0, 0.0, 0.0), (0.0, 2.6, 0.0, 0.0), (0.0, 0.0, -1.0, -0.2), (0.0, 0.0, -1.0, 0.0)))


def view_props(points):
    # Blender converts a list of dicts to a collection only when each has a name.
    return dict(points=[{"name": "", "co": point} for point in points], region_size=(640, 480),
                view_matrix=VIEW, projection_matrix=WINDOW)


def test_flat():
    section("matrices are stored column first")
    check(select_ops._flat(VIEW) == [v for column in VIEW.col for v in column], "_flat reads columns")
    op = tree().selection.add_op('BOX', space='VIEW', points=[(0, 0), (1, 1)], view_matrix=select_ops._flat(VIEW))
    check(op.view_matrix == VIEW, "a flattened matrix reads back as the same Matrix")
    tree().selection.clear()


def test_execute():
    section("execute appends a VIEW op")
    selection = tree().selection
    selection.clear()
    selection.add_op('ALL')
    points = [(12.5, 20.5), (200.5, 150.5)]
    result = bpy.ops.paint_system.select_box('EXEC_DEFAULT', mode='ADD', feather=6.0, antialias=False,
                                            through=True, **view_props(points))
    check(result == {'FINISHED'} and len(selection.ops) == 2, f"an ADD box is appended ({result})")
    op = selection.ops[-1]
    check((op.kind, op.mode, op.space) == ('BOX', 'ADD', 'VIEW'), f"kind, mode and space ({op.kind} {op.mode})")
    check(close_points(op.get_points(), points), f"points in region pixels ({op.get_points()})")
    check((op.feather, op.antialias, op.through) == (6.0, False, True), "feather, anti-alias and through")
    check(tuple(op.region_size) == (640, 480), f"region size ({tuple(op.region_size)})")
    object_to_view = VIEW @ cube().matrix_world
    check(close(select_ops._flat(op.view_matrix), select_ops._flat(object_to_view), 1e-5),
          "the view matrix is stored as object-to-view")
    check(close(select_ops._flat(op.projection_matrix), select_ops._flat(WINDOW), 1e-6),
          "the projection matrix is the window matrix")
    check(op.object == cube() and op.uv_map == "UVMap", f"object and UV map ({op.object}, {op.uv_map!r})")
    check(bpy.app.timers.is_registered(session._tick), "the session is notified")

    result = bpy.ops.paint_system.select_ellipse('EXEC_DEFAULT', **view_props([(10.5, 10.5), (90.5, 60.5)]))
    check(result == {'FINISHED'} and len(selection.ops) == 1 and selection.ops[0].kind == 'ELLIPSE'
          and selection.ops[0].mode == 'REPLACE',
          f"a REPLACE ellipse drops the earlier ops ({[o.kind for o in selection.ops]})")
    lasso = [(10.5, 10.5), (90.5, 12.5), (60.5, 80.5), (20.5, 50.5)]
    result = bpy.ops.paint_system.select_lasso('EXEC_DEFAULT', mode='SUBTRACT', **view_props(lasso))
    check(result == {'FINISHED'} and selection.ops[-1].kind == 'LASSO'
          and close_points(selection.ops[-1].get_points(), lasso),
          "a lasso keeps every point")

    section("execute cancels what it cannot use")
    count = len(selection.ops)
    for operator, _, kind in OPERATORS:
        flat = [(5.5, 5.5), (5.5, 40.5)] if kind != 'LASSO' else [(0.5, 0.5), (4.5, 4.5), (8.5, 8.5)]
        result = operator('EXEC_DEFAULT', **view_props(flat))
        check(result == {'CANCELLED'} and len(selection.ops) == count, f"a degenerate {kind} is cancelled")
    layer = tree().nodes.active
    image, layer.image = layer.image, None
    try:
        result = bpy.ops.paint_system.select_box('EXEC_DEFAULT', **view_props(points))
        check(result == {'CANCELLED'} and len(selection.ops) == count, "a layer without an image is cancelled")
    finally:
        layer.image = image
    scale = cube().scale.copy()
    cube().scale = (1.0, 1.0, 0.0)
    bpy.context.view_layer.update()
    try:
        result = bpy.ops.paint_system.select_box('EXEC_DEFAULT', **view_props(points))
        check(result == {'CANCELLED'} and len(selection.ops) == count,
              f"an object scaled to zero is cancelled, as its view could not be inverted ({result})")
    finally:
        cube().scale = scale
        bpy.context.view_layer.update()
    selection.clear()


def test_poll():
    section("poll")
    for operator, _, kind in OPERATORS:
        check(operator.poll(), f"{kind} polls in Texture Paint with a tree")
    material, scene = cube().active_material, bpy.context.scene
    tree_pointer, material.paint_system.tree = material.paint_system.tree, None
    scene_tree, scene.paint_system.active_node_tree = scene.paint_system.active_node_tree, None
    try:
        check(not any(operator.poll() for operator, _, _ in OPERATORS), "no tree: poll fails")
    finally:
        material.paint_system.tree = tree_pointer
        scene.paint_system.active_node_tree = scene_tree
    bpy.ops.object.mode_set(mode='OBJECT')
    try:
        check(not any(operator.poll() for operator, _, _ in OPERATORS), "Object Mode: poll fails")
    finally:
        bpy.ops.object.mode_set(mode='TEXTURE_PAINT')


guarded(test_box_corners)
guarded(test_ellipse_outline)
guarded(test_lasso_append)
guarded(test_degenerate)
guarded(test_clockwise_and_quads)
guarded(test_keymap_and_tools)
setup()
guarded(test_flat)
guarded(test_execute)
guarded(test_poll)

session.release()
raster.release()
finish("SELECTION TOOLS TEST")
