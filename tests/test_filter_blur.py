"""The gaussian blur and the unsharp mask on it, against numpy (PS-051).

The shaders are checked at two levels. The passes run over a texture the
test builds, so their arithmetic is compared with a kernel written out in
numpy, exactly -- same truncation, same clamped edges, same premultiplied
sum. Then a filter layer is built with each, which puts the composite,
the passes and the sRGB encode in one line and compares the result
against a filter of what the same layer built at sigma zero. That second
form is deliberate: it never models what the composite does, so it stays
true if the composite changes.

The properties worth stating outright, because a blur that gets them
wrong still looks like a blur:

- a flat picture blurs to itself, which only holds if the weights
  normalise and the edges clamp rather than reading zero past them;
- colour does not bleed towards black across a transparent edge, because
  the sum is premultiplied;
- a blur wider than one kernel is run again rather than widened, so the
  schedule is checked separately from the pixels;
- an unsharp mask leaves a flat picture and the alpha channel alone, and
  the combine reads the stack the blur was made from rather than the
  blur -- which only the layer build can get wrong, because it is the
  one that has to hold that texture out of the pool.

These need a GPU context, as `tests/test_filter_build.py` explains.
"""
import os
import sys
import traceback

import bpy
import gpu
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (check, finish, fmt, import_from, register_addon,  # noqa: E402
                     section, skip)

register_addon()
gpu_core = import_from("gpu_passes.core")
core = import_from("compiler.core")
filters_core = import_from("filters.core")
layer_build = import_from("filters.layer_build")
registry = import_from("filters.registry")
create_managed_image = import_from("compiler.bake").create_managed_image

IMAGE = 'PaintSystemImageLayerNode'
FILTER = 'PaintSystemFilterLayerNode'

# The passes run in the source's own format, which is RGBA32F for the
# textures built here, so only the shader's own float arithmetic is in
# the way.
TOL = 2e-4
# A built layer lands in a byte image, and the reference goes through the
# byte image the same layer built at sigma zero, so both ends quantise.
BYTE_TOL = 2.0 / 255.0
SIZE = 1024


def to_srgb(value):
    c = np.clip(np.asarray(value, dtype=np.float64), 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1.0 / 2.4) - 0.055)


def to_linear(value):
    c = np.clip(np.asarray(value, dtype=np.float64), 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def available():
    if gpu_core.gpu_available():
        return True
    skip("no GPU context in this session; gpu.init() arrived in Blender 5.2")
    return False


# -- the reference ----------------------------------------------------------


def kernel(sigma, radius):
    """The weights the shader evaluates, as an array.

    Truncated at *radius* and normalised over what is left, which is what
    dividing by the running total in the loop amounts to.
    """
    taps = np.arange(-radius, radius + 1, dtype=np.float64)
    weights = np.exp(-0.5 * (taps / sigma) ** 2)
    return weights / weights.sum()


def blur_axis(straight, sigma, radius, axis):
    """One separable pass over a ``(rows, cols, 4)`` array of straight colour."""
    premultiplied = straight.astype(np.float64).copy()
    premultiplied[..., :3] *= premultiplied[..., 3:4]
    padding = [(0, 0), (0, 0), (0, 0)]
    padding[axis] = (radius, radius)
    padded = np.pad(premultiplied, padding, mode='edge')
    total = np.zeros_like(premultiplied)
    for offset, weight in enumerate(kernel(sigma, radius)):
        window = [slice(None), slice(None), slice(None)]
        window[axis] = slice(offset, offset + premultiplied.shape[axis])
        total += weight * padded[tuple(window)]
    alpha = total[..., 3:4]
    safe = np.where(alpha > 0.0, alpha, 1.0)
    colour = np.where(alpha > 0.0, total[..., :3] / safe, 0.0)
    return np.concatenate([colour, alpha], axis=-1)


def blurred(straight, passes):
    """*straight* through the same schedule `registry.blur_passes` produced."""
    result = straight.astype(np.float64)
    for params in passes:
        axis = 1 if params["direction"][0] else 0
        result = blur_axis(result, params["sigma"], params["radius"], axis)
    return result


def sharpened(straight, radius, strength, encode=True):
    """An unsharp mask of *straight*, as the shader computes it.

    With *encode*, the difference is taken on the sRGB encoding of a blur
    that ran in scene linear, which is what the layer build asks for.
    Without it the values are already an encoding -- a byte layer's own
    pixels -- and the difference is taken as it stands.
    """
    straight = straight.astype(np.float64)
    blur = blurred(straight, registry.blur_passes(radius))
    if encode:
        original, low = to_srgb(straight[..., :3]), to_srgb(blur[..., :3])
    else:
        original, low = straight[..., :3], blur[..., :3]
    sharp = np.clip(original + strength * (original - low), 0.0, 1.0)
    return np.concatenate([to_linear(sharp) if encode else sharp,
                           straight[..., 3:4]], axis=-1)


# -- running the pass on its own --------------------------------------------


def upload(values):
    rows, cols = values.shape[:2]
    array = np.ascontiguousarray(values, dtype=np.float32)
    return gpu.types.GPUTexture(
        (cols, rows), format='RGBA32F',
        data=gpu.types.Buffer('FLOAT', array.size, array.ravel()))


def run_chain(values, passes):
    """*values*, a ``(rows, cols, 4)`` straight array, through *passes*.

    A pass whose spec reads the second sampler gets the array this
    started from, which is what `filters.layer_build` binds for it.
    """
    rows, cols = values.shape[:2]
    original = texture = upload(values)
    framebuffer = None
    for spec, params in passes:
        source = filters_core.PixelSource.from_texture(texture)
        try:
            framebuffer, texture = filters_core.run_pass(
                spec, source, second=original if spec.reads_second else None,
                params=params)
        finally:
            source.release()
    if framebuffer is None:
        return values
    return gpu_core.read_color(framebuffer, cols, rows)


def run_blur(values, sigma):
    """*values* through the real blur passes, and the schedule that ran."""
    passes = [(registry.BLUR, params) for params in registry.blur_passes(sigma)]
    return run_chain(values, passes), [params for _, params in passes]


def agrees(got, want, label, tol=TOL):
    worst = float(np.abs(np.asarray(got, dtype=np.float64)
                         - np.asarray(want, dtype=np.float64)).max())
    check(worst <= tol, f"{label}, worst {worst:.6f} (limit {tol:.6f})")


if available():
    try:
        section("the schedule")
        check(registry.blur_passes(0.0) == [],
              "a blur of zero runs no passes at all, rather than an identity kernel")
        one = registry.blur_passes(4.0)
        check(len(one) == 2 and one[0]["direction"] == (1.0, 0.0)
              and one[1]["direction"] == (0.0, 1.0),
              "a blur inside one kernel is two passes, one per axis")
        check(one[0]["radius"] == 12,
              f"cut off at three sigma ({one[0]['radius']} taps for sigma 4)")
        wide = registry.blur_passes(42.0)
        check(len(wide) == 8, f"a wider one is run again instead ({len(wide)} passes)")
        iterations = len(wide) // 2
        check(abs(wide[0]["sigma"] * iterations ** 0.5 - 42.0) < 1e-6,
              f"at the sigma whose {iterations} iterations compose to the one asked for")
        check(registry.blur_passes(1e6) == registry.blur_passes(
                  registry.BLUR_MAX_EFFECTIVE_SIGMA),
              "and past the cap it is the cap, not more passes without end")

        section("an impulse")
        # The ticket's acceptance: one white texel at sigma 2 against the
        # separable gaussian written out by hand.
        impulse = np.zeros((65, 65, 4), dtype=np.float32)
        impulse[32, 32] = (1.0, 1.0, 1.0, 1.0)
        got, passes = run_blur(impulse, 2.0)
        agrees(got, blurred(impulse, passes), "a white texel spreads as the kernel says")
        middle = float(got[32, 32, 3])
        check(abs(middle - kernel(2.0, 6)[6] ** 2) < TOL,
              f"its centre is the 2D weight, {middle:.6f}")

        section("a flat picture")
        # Only true if the weights normalise and the taps past the edge
        # clamp: reading zero outside would darken the border.
        flat = np.tile(np.array([0.3, 0.7, 0.2, 1.0], dtype=np.float32), (48, 48, 1))
        got, _ = run_blur(flat, 6.0)
        agrees(got, flat, "blurs to itself, edges and all")

        section("across a transparent edge")
        # Premultiplied: the transparent half contributes no colour, so
        # the red stays red as it fades out instead of going to black.
        patch = np.zeros((32, 32, 4), dtype=np.float32)
        patch[:, :16] = (1.0, 0.0, 0.0, 1.0)
        got, passes = run_blur(patch, 3.0)
        agrees(got, blurred(patch, passes), "matches the premultiplied reference")
        edge = got[16, 20]
        check(edge[3] < 0.5 and edge[0] > 0.9 and edge[1] < 0.05,
              f"and colour does not bleed towards black: {fmt(tuple(float(v) for v in edge))}")

        section("an unsharp mask")
        step = np.zeros((32, 48, 4), dtype=np.float32)
        step[:, 24:, :3] = 0.8
        step[:, :24, :3] = 0.1
        step[..., 3] = 1.0
        blur_part = [(registry.BLUR, params) for params in registry.blur_passes(2.0)]
        chain = blur_part + [(registry.SHARPEN, {"strength": 1.5, "encode": 1})]
        check(chain[-1][0].reads_second,
              "the combine asks for the picture the blur was made from")
        got = run_chain(step, chain)
        agrees(got, sharpened(step, 2.0, 1.5), "matches the reference unsharp mask")
        check(float(got[16, 24, 0]) > 0.8 and float(got[16, 23, 0]) < 0.1,
              "with a halo: the bright side overshoots and the dark side undershoots")

        flat_sharp = run_chain(flat, chain)
        agrees(flat_sharp, flat, "and a flat picture comes back unchanged")

        # What an action over a byte layer runs: its pixels are already
        # an encoding, so taking one of them would go round twice.
        plain = blur_part + [(registry.SHARPEN, {"strength": 1.5, "encode": 0})]
        agrees(run_chain(step, plain), sharpened(step, 2.0, 1.5, encode=False),
               "and without the encode the difference is taken as the values stand")

        section("what sharpening leaves alone")
        edged = np.zeros((32, 32, 4), dtype=np.float32)
        edged[:, :16] = (0.9, 0.2, 0.2, 1.0)
        none = [(registry.SHARPEN, {"strength": 0.0, "encode": 1})]
        agrees(run_chain(edged, none), edged, "strength zero is not a filter at all")
        got = run_chain(edged, chain)
        agrees(got[..., 3], edged[..., 3],
               "alpha is the original's, so the silhouette gets no halo")

        section("a filter layer built with it")
        tree = bpy.data.node_groups.new("Blur", 'PaintSystemNodeTree')
        tree.initialize()
        source = create_managed_image("Blur Source", SIZE, SIZE)
        # A hard edge: black and white are exact bytes, so the reference
        # loses nothing to the quantisation of the sigma-zero build.
        pattern = np.zeros((SIZE, SIZE, 4), dtype=np.float32)
        pattern[:, SIZE // 2:, :3] = 1.0
        pattern[..., 3] = 1.0
        source.pixels.foreach_set(pattern.ravel())
        source.update()
        with core.suspend_compile(tree):
            picture = tree.insert_layer_node(IMAGE)
            picture.image = source
            node = tree.insert_layer_node(FILTER)
            node.filter_type = 'BLUR'
            node.resolution = str(SIZE)
            node.blur_sigma = 0.0
        core.flush_now()

        unblurred = layer_build.build_layer(bpy.context, tree, node)
        stored = np.empty(SIZE * SIZE * 4, dtype=np.float32)
        unblurred.pixels.foreach_get(stored)
        stored = stored.reshape(SIZE, SIZE, 4)
        check(float(np.abs(stored[:, :SIZE // 2, :3]).max()) < 1.0 / 255.0
              and float(np.abs(stored[:, SIZE // 2:, :3] - 1.0).max()) < 1.0 / 255.0,
              "at sigma zero the layer builds the stack below unchanged")

        node.blur_sigma = 4.0
        core.flush_now()
        built = layer_build.build_layer(bpy.context, tree, node)
        got = np.empty(SIZE * SIZE * 4, dtype=np.float32)
        built.pixels.foreach_get(got)
        got = got.reshape(SIZE, SIZE, 4)

        # The build blurs scene linear and encodes on the way out, so the
        # reference decodes what the sigma-zero build stored, blurs that,
        # and encodes it again.
        linear = np.concatenate([to_linear(stored[..., :3]), stored[..., 3:4]], axis=-1)
        want = blurred(linear, registry.blur_passes(4.0))
        want = np.concatenate([to_srgb(want[..., :3]), want[..., 3:4]], axis=-1)
        agrees(got, want, "and at sigma 4 it matches a blur of that", tol=BYTE_TOL)

        column = got[SIZE // 2, SIZE // 2 - 20:SIZE // 2 + 20, 0]
        check(bool(np.all(np.diff(column) >= -1.0 / 255.0)) and column[0] < 0.05
              and column[-1] > 0.95,
              "the edge came out as a ramp rather than a step")

        section("a filter layer that sharpens")
        # The one thing the passes cannot be checked for on their own:
        # `layer_build` has to hold the composite out of the pool for the
        # whole chain, or the combine reads a texture the blur overwrote.
        node.filter_type = 'SHARPEN'
        node.sharpen_radius = 2.0
        node.sharpen_strength = 1.5
        core.flush_now()
        built = layer_build.build_layer(bpy.context, tree, node)
        got = np.empty(SIZE * SIZE * 4, dtype=np.float32)
        built.pixels.foreach_get(got)
        got = got.reshape(SIZE, SIZE, 4)
        want = sharpened(linear, 2.0, 1.5)
        want = np.concatenate([to_srgb(want[..., :3]), want[..., 3:4]], axis=-1)
        agrees(got, want, "matches the reference through the whole build", tol=BYTE_TOL)
        # A combine reading its own input rather than the composite would
        # give the blur back, which at these two columns is half grey.
        check(float(got[SIZE // 2, SIZE // 2 - 1, 0]) < 0.05
              and float(got[SIZE // 2, SIZE // 2, 0]) > 0.95,
              "and the step survived, so the combine read the stack and not the blur of it")

    except Exception:
        traceback.print_exc()
        check(False, "unexpected exception")

# Python's own teardown would free them after the GPU context has gone,
# which segfaults a background Blender. The textures `run_blur` chains
# are its own locals and go when it returns.
import_from("filters.composite").release()
import_from("filters.blend_glsl").release()
filters_core.release()

finish("FILTER BLUR TEST")
