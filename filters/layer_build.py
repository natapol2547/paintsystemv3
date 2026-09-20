# SPDX-License-Identifier: GPL-3.0-or-later
"""Building a filter layer's derived image (PS-057).

`steps` is the build, as a generator of bounded units:

1. `filters.layer_plan.resolve_input` says how the stack below can be
   turned into pixels, and refuses before any video memory is spent;
2. `filters.composite` draws that stack into one texture;
3. the layer's kind runs over it, followed by the encode below;
4. the result is read back a band of rows at a time;
5. `commit` writes it into the layer's image, packs it and stamps it.

A generator rather than a call because only the last unit writes
anything, which is what lets a build be cancelled without anything
changing on screen: the viewport goes on showing the previous result
until the commit, and the commit is one `foreach_set` and four ID
properties. The textures live in the generator's own frame, so abandoning
it gives them back as well.

Colour space is the step that is easy to miss. Everything up to the
encode is scene linear and straight, which is what the render engines
feed a shader and what the composite reproduces. The derived image is
byte sRGB like a painted layer image, so the values are encoded on the
way out and the compiled Image Texture node decodes them back. Doing it
as a pass rather than in numpy keeps a 4K build in milliseconds rather
than in tenths of a second, and costs no texture: it draws into one the
pool has already handed back.
"""
from __future__ import annotations

import hashlib
import logging

import bpy
import numpy as np

from ..compiler.bake import create_managed_image
from ..compiler.core import build_ir, mark_dirty
from ..compiler.ir import hash_payload
from ..gpu_passes.core import read_color
from . import composite, derived, freshness, layer_plan
from .core import BAND_ROWS, FilterSpec, PixelSource, Refused, ResultImage, run_pass
from .layer_specs import layer_filter_kind

log = logging.getLogger(__name__)

# Rows per readback. One read of 4096 rows stalls for about a second on
# the probe machine, which is far too long to hold between modal events;
# a band of this many is the same order as `run_pass` draws in.
READ_ROWS = BAND_ROWS


ENCODE_SRGB = FilterSpec(
    name="layer_encode_srgb",
    apply_source="""
/* Scene linear in, the derived image's own storage out. Values outside 0
   to 1 have no sRGB encoding and are clamped into one; eight bits could
   not have carried them anyway. */
vec4 apply(ivec2 texel, vec4 c)
{
  return vec4(ps_to_srgb(clamp(c.rgb, 0.0, 1.0)), clamp(c.a, 0.0, 1.0));
}
""",
)


def build_layer(context, tree, node, *, plan=None) -> bpy.types.Image:
    """Build *node*'s derived image, running `steps` to the end.

    For a script and for a test. An operator drives `steps` itself, so
    that it can show progress and be cancelled.
    """
    run = steps(context, tree, node, plan=plan)
    while True:
        try:
            next(run)
        except StopIteration as done:
            return done.value


def steps(context, tree, node, *, plan=None):
    """The build as bounded units, yielding ``(label, fraction)`` after each.

    *plan* is a `filters.layer_plan.InputPlan` already resolved for this
    layer, or None to resolve one. Raises `filters.core.Refused` with the
    message for the UI, and returns the built image.

    Nothing outside the layer changes until the last unit, so a caller
    that stops driving this leaves the previous result bit-identical. The
    textures live in the generator's own frame, which is what makes that
    true for video memory too: abandoning it raises `GeneratorExit` at
    whichever yield it reached, and the pool is closed on the way out.

    Three textures are in flight at the widest point -- the composite,
    the filter's output and the encode's -- and the pool hands the first
    one back for the encode to draw into, so a filter costs one target
    more than the composite it reads.
    """
    if plan is None:
        plan = layer_plan.resolve_input(context, tree, node)
    if not plan.is_composite:
        raise Refused(f"Filtering the layers below '{node.name}' needs a Cycles bake, "
                      f"because {plan.reason}")
    kind = layer_filter_kind(node)
    width, height = size = (int(node.resolution), int(node.resolution))
    yield f"{kind.label}: compositing the layers below", 0.0

    pool = composite.Pool(size)
    try:
        below = composite.composite_below(plan.chain, size, pool=pool)
        yield f"{kind.label}: filtering", 0.2
        framebuffer, filtered = _draw(kind.filter, below, kind.params_of(node), pool)
        pool.release(below)
        framebuffer, encoded = _draw(ENCODE_SRGB, filtered, {}, pool)
        pool.release(filtered)

        bands = []
        for first in range(0, height, READ_ROWS):
            last = min(height, first + READ_ROWS)
            bands.append(read_color(framebuffer, width, height, rows=(first, last)))
            yield f"{kind.label}: reading the result back", 0.3 + 0.6 * last / height
        values = bands[0] if len(bands) == 1 else np.concatenate(bands)
    finally:
        pool.close()

    yield f"{kind.label}: writing the result", 0.9
    return commit(tree, node, plan, values, size)


def _draw(spec: FilterSpec, texture, params: dict, pool):
    """Run *spec* over *texture* into a target from *pool*.

    The framebuffer comes back with the target because `run_pass` says
    so: reading one whose texture has been freed gives zeroes rather than
    an error.
    """
    source = PixelSource.from_texture(texture)
    try:
        return run_pass(spec, source, pool.acquire(), params=params)
    finally:
        # The texture belongs to the pool; this only drops the reference
        # the source was holding to it.
        source.release()


def commit(tree, node, plan, values, size):
    """Write *values* into *node*'s derived image, pack it and stamp it.

    The pack comes before the stamps for the reason `filters.derived`
    gives: an unpacked generated image loses its pixels to `image.copy()`
    and to undo-then-redo while its ID properties survive both, so a
    stamp written first could end up describing pixels that are gone.
    """
    image = _result_image(tree, node, size)
    ResultImage(image).commit(values)
    image.pack()

    fingerprint = _fingerprint(tree, node, plan)
    image[derived.OWNER_KEY] = f"{tree.uuid}:{node.uuid}"
    image[derived.FINGERPRINT_KEY] = fingerprint
    image[derived.UV_MAP_KEY] = plan.uv_map
    image[derived.BUILD_KEY] = hash_payload([fingerprint, _digest(values)])

    node.derived_image = image
    # The pixels below are what was just read, so whatever moved them is
    # accounted for. Cleared after the write rather than before, so a
    # build abandoned partway leaves the layer still asking for one.
    node.derived_stale_pixels = False
    # A filter layer feeding another one is a source image that just
    # changed, and the depsgraph does not report this write.
    freshness.note_image_changed([image.session_uid])
    # Reusing the datablock leaves the pointer unchanged, so the node's
    # own update callback does not fire and the new stamps would not
    # reach a recompile on their own.
    mark_dirty(tree)
    log.debug("built %s for %s at %dx%d", image.name, node.name, *size)
    return image


def _result_image(tree, node, size):
    """*node*'s derived image, made or resized to *size*.

    The datablock is reused rather than replaced, as `bake_node_cache`
    does: every replacement is a name Blender has to make unique and an
    orphan for the next sweep to find.
    """
    image = node.derived_image
    if image is None:
        return create_managed_image(f"{tree.name} {node.name} Filter", *size)
    if tuple(image.size) != size:
        image.scale(*size)
    return image


def _fingerprint(tree, node, plan) -> str:
    """What the build was asked for: the structural half of freshness.

    It records the *inputs* to the build rather than its result, so a
    later compile can tell that nothing structural has moved without
    looking at a pixel. Building the IR is how a context to hash against
    is come by; the compile that `commit` asks for afterwards makes the
    same one and compares.
    """
    ctx = build_ir(tree).ctx
    return freshness.stamp(freshness.fingerprint_parts(ctx, node, plan.source))


def _digest(values) -> str:
    """A short digest of the pixels a build produced.

    This is what makes the build stamp change when the picture does. The
    structural fingerprint cannot: painting on a layer below moves no
    property, so two builds around a brush stroke ask for exactly the
    same thing and produce different pixels.
    """
    array = np.ascontiguousarray(values, dtype=np.float32)
    return hashlib.blake2b(array, digest_size=16).hexdigest()
