"""Shared helpers for the Blender-side tests. Not a test itself.

Every test file does::

    from harness import *          # noqa: F401,F403
    register_addon()
    ...
    finish("name of test")

The addon package is imported from the repository checkout, whatever the
folder is called, so tests do not depend on the extension id.
"""
import importlib
import os
import sys
import traceback

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.basename(REPO)
VERSION = tuple(bpy.app.version)
BACKGROUND = bpy.app.background

_failures = []
_checks = 0
_addon = None


def since(major, minor=0, patch=0):
    """True when the running Blender is at least the given version."""
    return VERSION >= (major, minor, patch)


def before(major, minor=0, patch=0):
    return VERSION < (major, minor, patch)


def register_addon():
    """Import the repository as a package and register it once."""
    global _addon
    if _addon is not None:
        return _addon
    parent = os.path.dirname(REPO)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    _addon = importlib.import_module(PACKAGE)
    _addon.register()
    return _addon


def import_from(relpath):
    """Import a submodule of the addon, e.g. ``import_from("compiler.core")``."""
    return importlib.import_module(f"{PACKAGE}.{relpath}")


def check(cond, msg):
    global _checks
    _checks += 1
    status = "ok  " if cond else "FAIL"
    print(f"  [{status}] {msg}")
    if not cond:
        _failures.append(msg)
    return bool(cond)


def fail(msg):
    return check(False, msg)


def section(title):
    print(f"\n== {title}")


def guarded(fn):
    """Run ``fn`` and record an exception as a failure instead of aborting."""
    try:
        fn()
    except Exception:
        traceback.print_exc()
        _failures.append(f"exception in {getattr(fn, '__name__', fn)}")


# ── Pixel sampling ───────────────────────────────────────────────────

BAKE_PLANE_NAME = "PS Test Bake Plane"


def bake_plane():
    """A unit plane whose UV map covers 0..1 exactly, created on first use.

    Pixel (u, v) of a bake maps to the same (u, v) of any image layer that
    uses the default UV map, so tests can check where paint lands.
    """
    obj = bpy.data.objects.get(BAKE_PLANE_NAME)
    if obj is not None:
        return obj
    mesh = bpy.data.meshes.new(BAKE_PLANE_NAME)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name="UVMap")
    for loop, co in zip(uv.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
        loop.uv = co
    obj = bpy.data.objects.new(BAKE_PLANE_NAME, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def bake_group(node_group, *, color="Color", alpha="Color Alpha", inputs=None, size=8):
    """Bake a shader group's colour and alpha outputs on the bake plane.

    Returns ``size * size`` RGBA rows (linear values, row-major from the
    bottom-left). ``inputs`` sets unlinked group node input values by name.
    """
    import numpy as np

    obj = bake_plane()
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    mat = bpy.data.materials.new("PS Test Bake")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    group = nt.nodes.new('ShaderNodeGroup')
    group.node_tree = node_group
    for name, value in (inputs or {}).items():
        group.inputs[name].default_value = value
    emission = nt.nodes.new('ShaderNodeEmission')
    output = nt.nodes.new('ShaderNodeOutputMaterial')
    nt.links.new(emission.outputs['Emission'], output.inputs['Surface'])
    target = nt.nodes.new('ShaderNodeTexImage')
    nt.nodes.active = target
    obj.data.materials.clear()
    obj.data.materials.append(mat)

    images = []
    for name in (color, alpha):
        image = bpy.data.images.new(f"PS Test Bake {name}", size, size, alpha=True, float_buffer=True)
        image.colorspace_settings.name = 'Non-Color'
        images.append(image)

    scene.render.engine = 'CYCLES'
    scene.cycles.samples = 1
    scene.cycles.device = 'CPU'
    scene.render.bake.target = 'IMAGE_TEXTURES'
    scene.render.bake.margin = 0
    previous_active = view_layer.objects.active
    previous_selected = [o for o in view_layer.objects if o.select_get()]
    try:
        for o in view_layer.objects:
            o.select_set(o == obj)
        view_layer.objects.active = obj
        with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
            for socket, image in zip((color, alpha), images):
                for link in list(emission.inputs['Color'].links):
                    nt.links.remove(link)
                nt.links.new(group.outputs[socket], emission.inputs['Color'])
                target.image = image
                bpy.ops.object.bake(type='EMIT')
        px = []
        for image in images:
            buf = np.empty(size * size * 4, dtype=np.float32)
            image.pixels.foreach_get(buf)
            px.append(buf.reshape(size * size, 4))
        rgba = px[0].copy()
        rgba[:, 3] = px[1][:, 0]
        return rgba
    finally:
        # Give the selection back so operators that act on the active
        # object still find the object the test was working on.
        for o in view_layer.objects:
            o.select_set(o in previous_selected)
        view_layer.objects.active = previous_active
        obj.data.materials.clear()
        bpy.data.materials.remove(mat)
        for image in images:
            bpy.data.images.remove(image)


def pixel_at(rgba, u, v, size=8):
    """The baked pixel nearest to UV ``(u, v)`` as a tuple of four floats."""
    x = min(int(u * size), size - 1)
    y = min(int(v * size), size - 1)
    return tuple(float(c) for c in rgba[y * size + x])


def close(a, b, tol=0.02):
    return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))


def fmt(values):
    return "(" + ", ".join(f"{v:.3f}" for v in values) + ")"


def mix_blend(cb, cs):
    return cs


def multiply_blend(cb, cs):
    return tuple(b * s for b, s in zip(cb, cs))


def over(backdrop, layer, opacity=1.0, clip=False, blend=mix_blend):
    """*layer* (straight RGBA) composited over *backdrop* by the PS-001 coverage rule."""
    cb, ab = backdrop[:3], backdrop[3]
    cs, es = layer[:3], layer[3] * opacity
    weights = (es * ab, 0.0 if clip else es * (1.0 - ab), ab * (1.0 - es))
    colors = (blend(cb, cs), cs, cb)
    alpha = sum(weights)
    if alpha == 0.0:
        return cb + (0.0,)
    return tuple(sum(w * c[i] for w, c in zip(weights, colors)) / alpha for i in range(3)) + (alpha,)


def summary(name):
    print()
    print(f"Blender {bpy.app.version_string}, {_checks} checks")
    if _failures:
        print(f"{name} FAILED ({len(_failures)}):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"{name} PASSED")
    return 0


def finish(name):
    """Print the summary and exit the process with a status code."""
    code = summary(name)
    sys.stdout.flush()
    sys.stderr.flush()
    if BACKGROUND:
        sys.exit(code)
    # In a windowed session sys.exit only unwinds the script; the window
    # loop keeps running. Exit hard so the shell sees the status.
    os._exit(code)
