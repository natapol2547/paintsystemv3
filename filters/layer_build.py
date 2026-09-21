# SPDX-License-Identifier: GPL-3.0-or-later
"""Building a filter layer's derived image (PS-057).

`steps` is the build, as a generator of bounded units:

1. `filters.layer_plan.resolve_input` says how the stack below can be
   turned into pixels, and refuses before any video memory is spent;
2. `filters.composite` draws that stack into one texture;
3. the layer's kind runs over it -- its passes, or its own build where
   it has one -- followed by the encode below;
4. the result is read back as bytes a band of rows at a time, each band
   going straight into a PNG (`filters.png`);
5. `commit` packs that PNG into the layer's image and stamps it.

A generator rather than a call because only the last unit writes
anything, which is what lets a build be cancelled without anything
changing on screen: the viewport goes on showing the previous result
until the commit, and the commit is one `pack` of a file already encoded
and four ID properties. The textures live in the generator's own frame,
so abandoning it gives them back as well.

The PNG is the build's own rather than Blender's for time. Writing the
pixels into the image and calling `Image.pack` encodes the whole picture
in one call, about 0.9 s at 4K, which was the longest freeze of any
build; encoding band by band spreads that over the readback units, at
level 1, and the packed file is then the only copy of the pixels until
the next draw decodes it.

Colour space is the step that is easy to miss. Everything up to the
encode is scene linear and straight, which is what the render engines
feed a shader and what the composite reproduces. The derived image is
byte sRGB like a painted layer image, so the values are encoded on the
way out and the compiled Image Texture node decodes them back. Doing it
as a pass rather than in numpy keeps a 4K build in milliseconds rather
than in tenths of a second, and the pass draws into a byte target, so
the GPU rounds each value to the byte the file stores.
"""
from __future__ import annotations

import hashlib
import logging

import bpy
import gpu

from ..compiler.bake import create_managed_image
from ..compiler.core import build_ir, mark_dirty
from ..compiler.ir import hash_payload
from ..gpu_passes.core import read_color_bytes
from . import composite, derived, freshness, layer_plan
from .core import BAND_ROWS, FilterSpec, PixelSource, Refused, run_pass
from .layer_specs import layer_filter_kind
from .png import RGBAStream
from .registry import ENCODE_SRGB

log = logging.getLogger(__name__)

# Rows per readback. One read of 4096 rows stalls for about a second on
# the probe machine, which is far too long to hold between modal events;
# a band of this many is the same order as `run_pass` draws in.
READ_ROWS = BAND_ROWS


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

    Two of the pool's textures are in flight at the widest point of a
    list of passes -- the one a pass reads and the one it draws into --
    and three for an unsharp mask, which holds the composite as well. A
    kind with a build of its own decides for itself; the painter holds
    four while it analyses the picture, as `filters.painter.build`
    explains. The encode draws into a byte target of its own, and the
    pool is closed before the readback, so what the readback holds is
    that target alone.
    """
    if plan is None:
        plan = layer_plan.resolve_input(context, tree, node)
    if not plan.is_composite:
        raise Refused(f"Filtering the layers below '{node.name}' needs a Cycles bake, "
                      f"because {plan.reason}")
    kind = layer_filter_kind(node)
    width, height = size = (int(node.resolution), int(node.resolution))
    # Taken down before anything is read, and what the commit stamps. A
    # setting moved or a stroke landed partway through then leaves the
    # layer out of date, rather than under a stamp for pixels it never saw.
    # Everything the build reads off the node is read here too, so the
    # stamp and the pixels agree on the settings: `plan` already holds
    # the stack below as values.
    read = inputs_of(tree, node, plan)
    settings = kind.settings_of(node) if kind.build is not None else kind.passes_of(node)
    with freshness.reading(node.uuid):
        yield f"{kind.label}: compositing the layers below", 0.0

        # Where the filtering ends on the progress bar. A list of passes
        # is over in a moment and the readback is the long part; a kind
        # that builds for itself, such as the painter, is the other way
        # round.
        filtered = 0.3 if kind.build is None else 0.8
        pool = composite.Pool(size)
        try:
            current = composite.composite_below(plan.chain, size, pool=pool)
            if kind.build is not None:
                current = yield from _built(kind, settings, current, pool, 0.2, filtered)
            else:
                current = yield from _filtered(kind, settings, current, pool, 0.2, filtered)
            yield f"{kind.label}: filtering", filtered
            framebuffer, encoded = _encoded(current, size)
            pool.release(current)
            pool.close()

            # PNG rows run from the top and the framebuffer's from the
            # bottom, so the bands are read top first and each is turned
            # over. The digest is what makes the build stamp change when
            # the picture does (`derived.BUILD_KEY`), and takes the bands
            # as they come off the GPU: all it needs is the same order
            # every time.
            png = RGBAStream(width, height)
            digest = hashlib.blake2b(digest_size=16)
            firsts = range(0, height, READ_ROWS)
            for index, first in enumerate(reversed(firsts)):
                band = read_color_bytes(framebuffer, width, height,
                                        rows=(first, min(height, first + READ_ROWS)))
                digest.update(band)
                png.add(band[::-1])
                yield (f"{kind.label}: reading the result back",
                       filtered + (0.9 - filtered) * (index + 1) / len(firsts))
        finally:
            pool.close()

        yield f"{kind.label}: writing the result", 0.9
        return commit(tree, node, plan, png.finish(), digest.hexdigest(), size, read)


def inputs_of(tree, node, plan) -> tuple[str, int]:
    """What a build of *node* started now reads, to compare with later.

    The structural fingerprint, and the count of strokes below it noticed
    so far (`filters.freshness.changes`). Two of these differ exactly when
    something the build depends on has moved in between.
    """
    return _fingerprint(tree, node, plan), freshness.changes(node.uuid)


def _filtered(kind, passes, current, pool, start, end):
    """Run *passes*, *kind*'s for the layer, over *current*, and return what they leave.

    A kind runs as many passes as its parameters call for -- a separable
    blur is two per iteration -- so each one is a unit of its own, and a
    wide blur is cancellable partway through rather than one long stall.
    The list is empty for a kind whose parameters ask for nothing, such
    as a blur set to zero, and the stack below is then the result.
    """
    # An unsharp mask reads the stack below alongside the blur of it, so
    # the composite is held out of the pool for the whole chain rather
    # than reused by the second pass. One texture more, and only for a
    # kind that asks.
    original = current if any(spec.reads_second for spec, _ in passes) else None
    for index, (spec, params) in enumerate(passes):
        yield f"{kind.label}: filtering", start + (end - start) * index / len(passes)
        _framebuffer, result = _draw(spec, current, params, pool,
                                     second=original if spec.reads_second else None)
        if current is not original:
            pool.release(current)
        current = result
    if original is not None and current is not original:
        pool.release(original)
    return current


def _built(kind, settings, current, pool, start, end):
    """Run *kind*'s own build with *settings* over *current*, placing its progress on the bar.

    The hook owns *current* from here on and returns a texture of the
    pool's. It is closed explicitly when the build is abandoned, so that
    its textures go back to the pool before the pool itself is closed.
    """
    run = kind.build(settings, current, pool)
    try:
        while True:
            try:
                label, fraction = next(run)
            except StopIteration as done:
                return done.value
            yield f"{kind.label}: {label}", start + (end - start) * fraction
    finally:
        run.close()


def _draw(spec: FilterSpec, texture, params: dict, pool, *, second=None):
    """Run *spec* over *texture* into a target from *pool*.

    The framebuffer comes back with the target because `run_pass` says
    so: reading one whose texture has been freed gives zeroes rather than
    an error.
    """
    source = PixelSource.from_texture(texture)
    try:
        return run_pass(spec, source, pool.acquire(), second=second, params=params)
    finally:
        # The texture belongs to the pool; this only drops the reference
        # the source was holding to it.
        source.release()


def _encoded(texture, size):
    """*texture* encoded to sRGB in an `RGBA8` target of its own, with its framebuffer.

    Bytes because that is what the file stores: the GPU rounds each value
    once, where it writes it, and `read_color_bytes` can only read a
    texture that holds bytes.
    """
    try:
        target = gpu.types.GPUTexture(size, format='RGBA8')
    except RuntimeError as error:
        log.warning("Could not allocate a %sx%s result target: %s", *size, error)
        raise Refused("The GPU could not allocate the textures for this filter; "
                      "try a lower resolution") from error
    source = PixelSource.from_texture(texture)
    try:
        return run_pass(ENCODE_SRGB, source, target, params={})
    finally:
        source.release()


def commit(tree, node, plan, data, digest, size, read):
    """Pack *data*, the result as a PNG, into *node*'s derived image and stamp it.

    *digest* is a digest of the bytes the PNG holds. *read* is what
    `inputs_of` said when the build started, and the stamp is that rather
    than what the layer asks for now: the pixels are what was read then.

    The pack comes before the stamps for the reason `filters.derived`
    gives: a stamp written first could end up describing pixels that are
    gone. Here the packed file is the pixels, so the order is the only
    way it could.

    A build can come out with exactly the pixels the image already holds,
    for example after a stroke below is undone before the refresh runs.
    Nothing can tell that without building, but the commit can: it then
    skips the pack, keeps the decoded pixels, and does not mark the
    filter layers above as changed.
    """
    fingerprint, changes = read
    stamps = {
        derived.OWNER_KEY: f"{tree.uuid}:{node.uuid}",
        derived.FINGERPRINT_KEY: fingerprint,
        derived.UV_MAP_KEY: plan.uv_map,
        derived.BUILD_KEY: hash_payload([fingerprint, digest]),
    }
    image = node.derived_image
    unchanged = _holds(image, stamps)
    if not unchanged:
        image = _pack(tree, node, image, data)
        for key, value in stamps.items():
            image[key] = value
        derived.note_packed(image)
        node.derived_image = image
    # Cleared only when no stroke below has landed since the build read
    # the pixels, so whatever moved them before is accounted for. Cleared
    # after the write rather than before, so a build abandoned partway
    # leaves the layer still asking for one.
    if freshness.changes(node.uuid) == changes:
        node.derived_stale_pixels = False
    if not unchanged:
        # A filter layer feeding another one is a source image that just
        # changed, and the depsgraph does not report this write.
        freshness.note_image_changed([image.session_uid])
    # Reusing the datablock leaves the pointer unchanged, so the node's
    # own update callback does not fire and the new stamps would not
    # reach a recompile on their own. An unchanged build compiles too:
    # the compile is what tells the auto job the layer settled.
    mark_dirty(tree)
    log.debug("built %s for %s at %dx%d%s", image.name, node.name, *size,
              " (unchanged)" if unchanged else "")
    return image


def _holds(image, stamps) -> bool:
    """Whether *image* already holds the build *stamps* describe.

    The build stamp includes a digest of the pixels, so equal stamps mean
    equal pixels. The image must still hold them untouched: packed, and
    not painted on since.
    """
    if image is None or image.packed_file is None or image.is_dirty:
        return False
    return all(image.get(key) == value for key, value in stamps.items())


def _pack(tree, node, image, data) -> bpy.types.Image:
    """Pack the PNG *data* into *image*, making the image first if there is none."""
    if image is None:
        # One texel: the packed file brings its own size, and a generated
        # buffer the size of the result would only be thrown away.
        image = create_managed_image(f"{tree.name} {node.name} Filter", 1, 1)
    # The datablock is reused rather than replaced, as `bake_node_cache`
    # does: every replacement is a name Blender has to make unique and an
    # orphan for the next sweep to find. Nothing here asks for its size or
    # scales it -- either would decode what was just packed -- and none is
    # needed: a PNG of another size resizes the image as it is packed.
    image.pack(data=data, data_len=len(data))
    # A generated image ignores a packed file and would come back from an
    # undo or a copy as black; a file image reads it. The first build's
    # switch is also what drops its generated buffer.
    if image.source != 'FILE':
        image.source = 'FILE'
    # The previous result is still in the image's buffer. Freed, the next
    # read decodes the new file. `reload()` would do it too, but looks for
    # a file on disk first and reports the empty path as missing.
    image.buffers_free()
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
