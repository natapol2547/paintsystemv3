"""Check that every Blender API the addon relies on still exists.

Two layers:

1. A declared surface: the ``bl_ui`` helpers, RNA properties, operators
   and ``gpu`` entry points the addon (and the planned ports from v2) call
   into. Each entry may be gated with ``since`` / ``until`` so version
   specific alternatives are checked on the right versions.
2. A source scan: every ``bpy.types.X``, ``bpy.ops.x.y``,
   ``bpy.app.handlers.x`` and ``from bl_ui.x import y`` found in the addon
   source must resolve on the running Blender.

Run:  blender -b --factory-startup --python tests/test_api_surface.py
"""
import importlib
import os
import re
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (  # noqa: E402
    REPO, VERSION, check, section, register_addon, finish,
)


# --- declared surface --------------------------------------------------

def _resolve(dotted):
    """Resolve ``module.attr.attr``; returns (ok, value)."""
    parts = dotted.split(".")
    for i in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:i]))
        except ImportError:
            continue
        for attr in parts[i:]:
            if not hasattr(obj, attr):
                return False, None
            obj = getattr(obj, attr)
        return True, obj
    return False, None


def attr(dotted):
    ok, _ = _resolve(dotted)
    return ok, dotted


def rna_prop(type_name, prop):
    t = getattr(bpy.types, type_name, None)
    ok = t is not None and prop in t.bl_rna.properties
    return ok, f"bpy.types.{type_name}.{prop} (property)"


def rna_func(type_name, func):
    t = getattr(bpy.types, type_name, None)
    ok = t is not None and func in t.bl_rna.functions
    return ok, f"bpy.types.{type_name}.{func}()"


def op(idname):
    mod, name = idname.split(".")
    ok = hasattr(bpy.ops, mod) and name in dir(getattr(bpy.ops, mod))
    return ok, f"bpy.ops.{idname}"


def any_of(*items):
    """Pass when at least one alternative resolves (renamed API)."""
    labels = [label for _, label in items]
    ok = any(ok for ok, _ in items)
    return ok, " | ".join(labels)


def gated(item, since_v=None, until_v=None):
    """Only check ``item`` inside the version window."""
    if since_v is not None and VERSION < since_v:
        return None
    if until_v is not None and VERSION >= until_v:
        return None
    return item


PPC = "bl_ui.properties_paint_common"

SURFACE = [
    # Brush panel helpers appended from Blender's own UI (v2 extras_panels).
    attr(f"{PPC}.UnifiedPaintPanel"),
    attr(f"{PPC}.UnifiedPaintPanel.get_brush_mode"),
    attr(f"{PPC}.UnifiedPaintPanel.prop_unified"),
    attr(f"{PPC}.UnifiedPaintPanel.prop_unified_color"),
    attr(f"{PPC}.UnifiedPaintPanel.prop_unified_color_picker"),
    any_of(attr(f"{PPC}.UnifiedPaintPanel.paint_settings"),
           attr(f"{PPC}.UnifiedPaintPanel.paint_settings_from_active_tool")),
    attr(f"{PPC}.brush_settings"),
    attr(f"{PPC}.brush_settings_advanced"),
    attr(f"{PPC}.brush_basic_texpaint_settings"),
    attr(f"{PPC}.draw_color_settings"),
    attr(f"{PPC}.ColorPalettePanel"),
    attr(f"{PPC}.StrokePanel"),
    attr(f"{PPC}.FalloffPanel"),
    gated(attr(f"{PPC}.BrushAssetShelf"), since_v=(4, 3)),
    # The brush section's asset picker; a local brush picker before 4.3.
    gated(attr(f"{PPC}.BrushAssetShelf.draw_popup_selector"), since_v=(4, 3)),
    gated(rna_func("UILayout", "template_asset_shelf_popover"), since_v=(4, 3)),
    gated(rna_func("UILayout", "template_ID_preview"), until_v=(4, 3)),
    gated(op("brush.add"), until_v=(4, 3)),
    gated(any_of(attr(f"{PPC}.draw_color_jitter_panel"),
                 attr(f"{PPC}.color_jitter_panel")), since_v=(5, 0)),
    attr("bl_ui.space_toolsystem_common.ToolSelectPanelHelper.tool_active_from_context"),
    attr("bl_ui.properties_material.EEVEE_MATERIAL_PT_context_material"),

    # Brush and paint settings the panels read and write.
    rna_prop("Brush", "color"),
    rna_prop("Brush", "secondary_color"),
    rna_prop("Brush", "size"),
    rna_prop("Brush", "strength"),
    rna_prop("Brush", "blend"),
    rna_prop("Brush", "use_pressure_size"),
    rna_prop("Brush", "use_pressure_strength"),
    rna_prop("Brush", "image_paint_capabilities"),
    rna_prop("Brush", "color_type"),
    rna_prop("Brush", "use_alpha"),
    rna_prop("BrushCapabilitiesImagePaint", "has_color"),
    rna_prop("ImagePaint", "use_occlude"),
    rna_prop("ImagePaint", "use_backface_culling"),
    rna_prop("ImagePaint", "use_normal_falloff"),
    rna_prop("ImagePaint", "normal_angle"),
    rna_prop("Paint", "palette"),
    rna_func("UILayout", "template_palette"),
    op("palette.new"),
    rna_prop("Object", "active_material_index"),
    attr("bpy.msgbus.subscribe_rna"),
    rna_prop("ImagePaint", "brush"),
    rna_prop("ImagePaint", "canvas"),
    rna_prop("ImagePaint", "mode"),
    rna_prop("ImagePaint", "use_clone_layer"),
    rna_prop("ImagePaint", "seam_bleed"),
    rna_prop("ImagePaint", "missing_uvs"),
    rna_prop("ImagePaint", "missing_texture"),
    rna_prop("ImagePaint", "missing_materials"),
    any_of(rna_prop("ToolSettings", "unified_paint_settings"),
           rna_prop("ImagePaint", "unified_paint_settings")),
    rna_prop("UnifiedPaintSettings", "color"),
    rna_prop("UnifiedPaintSettings", "size"),
    rna_prop("UnifiedPaintSettings", "strength"),
    rna_prop("UnifiedPaintSettings", "use_unified_color"),
    rna_prop("UnifiedPaintSettings", "use_unified_size"),
    rna_prop("UnifiedPaintSettings", "use_unified_strength"),
    rna_prop("ToolSettings", "image_paint"),
    gated(op("brush.asset_activate"), since_v=(4, 3)),
    gated(op("paint.brush_select"), until_v=(4, 3)),
    op("paint.image_paint"),
    op("paint.sample_color"),
    op("paint.project_image"),
    op("paint.image_from_view"),
    op("paint.brush_colors_flip"),

    # Selection stencil (PS-091).
    rna_prop("ImagePaint", "use_stencil_layer"),
    rna_prop("ImagePaint", "invert_stencil"),
    rna_prop("ImagePaint", "stencil_image"),
    rna_prop("Mesh", "uv_layer_stencil_index"),
    rna_prop("Mesh", "uv_layer_stencil"),
    rna_prop("Image", "packed_file"),
    rna_func("Image", "unpack"),
    rna_func("Image", "reload"),
    attr("bpy.types.IMAGE_HT_tool_header"),

    # Images.
    rna_prop("Image", "pixels"),
    rna_prop("Image", "tiles"),
    rna_prop("Image", "is_dirty"),
    rna_prop("Image", "colorspace_settings"),
    rna_func("Image", "update"),
    rna_func("Image", "pack"),
    rna_func("ID", "update_tag"),
    op("image.save_all_modified"),
    op("image.save_as"),
    op("image.tile_add"),
    op("image.invert"),

    # Node trees the compiler targets.
    attr("bpy.types.NodeCustomGroup"),
    attr("bpy.types.ShaderNodeMix"),
    rna_prop("NodeTree", "interface"),
    rna_func("NodeTreeInterface", "new_socket"),
    rna_prop("NodeTreeInterfaceSocket", "identifier"),
    rna_prop("NodeTreeInterfaceSocket", "in_out"),
    rna_prop("ShaderNodeTexImage", "image"),
    rna_prop("Material", "node_tree"),
    op("object.bake"),
    rna_prop("BakeSettings", "margin"),

    # UI building blocks.
    rna_func("UILayout", "panel"),
    rna_func("UILayout", "popover"),
    rna_func("UILayout", "template_list"),
    rna_func("UILayout", "template_ID"),
    rna_func("UILayout", "template_icon"),
    rna_func("UILayout", "template_color_picker"),
    rna_func("UILayout", "template_curve_mapping"),
    rna_func("UILayout", "template_color_ramp"),
    rna_prop("Region", "active_panel_category"),
    attr("bpy.types.WorkSpaceTool"),
    attr("bpy.utils.register_tool"),
    attr("bpy.utils.previews.new"),
    attr("bpy.types.SpaceView3D.draw_handler_add"),
    attr("bpy.types.SpaceImageEditor.draw_handler_add"),
    attr("bpy.types.SpaceNodeEditor.draw_handler_add"),
    op("wm.call_panel"),
    op("wm.redraw_timer"),
    op("ed.undo_push"),
    op("object.mode_set"),
    op("uv.smart_project"),

    # Handlers and timers used by the compiler.
    attr("bpy.app.timers.register"),
    attr("bpy.app.timers.is_registered"),
    attr("bpy.app.handlers.undo_post"),
    attr("bpy.app.handlers.redo_post"),
    attr("bpy.app.handlers.load_post"),
    attr("bpy.app.handlers.depsgraph_update_post"),
    attr("bpy.app.handlers.save_pre"),
    # Frame changes mark cached surfaces suspect (PS-093).
    attr("bpy.app.handlers.frame_change_post"),

    # GPU entry points for the image filters (PS-050) and overlays.
    attr("gpu.shader.create_from_info"),
    attr("gpu.shader.from_builtin"),
    attr("gpu.types.GPUShaderCreateInfo"),
    attr("gpu.types.GPUStageInterfaceInfo"),
    attr("gpu.types.GPUOffScreen"),
    attr("gpu.types.GPUTexture"),
    attr("gpu.types.GPUFrameBuffer"),
    attr("gpu.texture.from_image"),
    attr("gpu.state.blend_set"),
    attr("gpu.state.depth_test_set"),
    attr("gpu.matrix.push_pop"),
    attr("gpu.platform.backend_type_get"),
    attr("gpu_extras.batch.batch_for_shader"),
    attr("bpy_extras.view3d_utils.location_3d_to_region_2d"),
    attr("mathutils.bvhtree.BVHTree.FromObject"),
    # The selection overlay's polygon offset (PS-091).
    rna_prop("RegionView3D", "window_matrix"),
    rna_prop("RegionView3D", "view_distance"),
    rna_prop("RegionView3D", "view_perspective"),
    # Surface content keys and position batches (PS-092).
    rna_prop("Mesh", "attributes"),
    rna_prop("Attribute", "data_type"),
    rna_prop("Attribute", "domain"),
    rna_prop("Mesh", "corner_normals"),
    rna_prop("Mesh", "has_custom_normals"),
    rna_prop("MeshPolygon", "loop_start"),
    attr("gpu.types.GPUVertFormat"),
    attr("gpu.types.GPUVertBuf"),
    attr("gpu.types.GPUBatch"),
    # Selections drawn in the 3D view (PS-093). GPUFrameBuffer's depth_slot
    # has no signature to check on 5.2; the view raster tests build one.
    attr("gpu.types.GPUUniformBuf"),
    rna_prop("Object", "matrix_world"),
    rna_prop("ViewLayer", "objects"),
    rna_prop("Object", "mode"),
    # Selection tools in the 3D view (PS-093); event_simulate is for the tests only.
    attr("bpy.utils.unregister_tool"),
    rna_prop("WorkSpaceTool", "idname"),
    rna_prop("WorkSpace", "tools"),
    rna_func("wmTools", "from_space_view3d_mode"),
    rna_func("WindowManager", "event_timer_add"),
    rna_func("WindowManager", "event_timer_remove"),
    rna_prop("Event", "mouse_prev_press_x"),
    rna_func("Window", "event_simulate"),
    op("wm.tool_set_by_id"),
    op("ed.undo_redo"),
    rna_prop("Screen", "is_temporary"),
]


# --- source scan -------------------------------------------------------

SKIP_DIRS = {"tests", "docs", ".git", "__pycache__", ".github", "dist", "build"}

RE_TYPES = re.compile(r"\bbpy\.types\.([A-Z][A-Za-z0-9_]*)")
RE_OPS = re.compile(r"\bbpy\.ops\.([a-z_0-9]+)\.([a-z_0-9]+)")
RE_HANDLERS = re.compile(r"\bbpy\.app\.handlers\.([a-z_]+)")
# Either a parenthesised name list, which may span lines, or the rest of one line.
RE_BL_UI = re.compile(r"^\s*from\s+(bl_ui(?:\.[a-z_0-9]+)+)\s+import\s+(?:\(([^)]*)\)|([^(\n]*)$)",
                      re.MULTILINE)


def addon_sources():
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(root, f)


def scan_sources():
    types, ops, handlers, bl_ui = {}, {}, {}, {}
    for path in addon_sources():
        rel = os.path.relpath(path, REPO)
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for m in RE_TYPES.finditer(src):
            types.setdefault(m.group(1), rel)
        for m in RE_OPS.finditer(src):
            ops.setdefault(f"{m.group(1)}.{m.group(2)}", rel)
        for m in RE_HANDLERS.finditer(src):
            handlers.setdefault(m.group(1), rel)
        for m in RE_BL_UI.finditer(src):
            names = [n.strip() for n in (m.group(2) or m.group(3)).replace("\n", ",").split(",")]
            for n in names:
                n = n.split(" as ")[0].strip()
                if n:
                    bl_ui.setdefault(f"{m.group(1)}.{n}", rel)
    return types, ops, handlers, bl_ui


# --- run ---------------------------------------------------------------

section("register addon")
register_addon()
check(True, f"addon registered on Blender {bpy.app.version_string}")

section("declared API surface")
for item in SURFACE:
    if item is None:
        continue
    ok, label = item
    check(ok, label)

section("scanned API surface")
types, ops, handlers, bl_ui = scan_sources()
for name, where in sorted(types.items()):
    check(hasattr(bpy.types, name), f"bpy.types.{name} ({where})")
for idname, where in sorted(ops.items()):
    mod, name = idname.split(".")
    check(hasattr(bpy.ops, mod) and name in dir(getattr(bpy.ops, mod)),
          f"bpy.ops.{idname} ({where})")
for name, where in sorted(handlers.items()):
    check(hasattr(bpy.app.handlers, name), f"bpy.app.handlers.{name} ({where})")
for dotted, where in sorted(bl_ui.items()):
    ok, _ = _resolve(dotted)
    check(ok, f"{dotted} ({where})")
check(len(types) + len(ops) + len(handlers) + len(bl_ui) > 0, "source scan found API references")

finish("API SURFACE TEST")
