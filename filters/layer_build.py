# SPDX-License-Identifier: GPL-3.0-or-later
"""Builds a filter layer's derived image (PS-057).

`steps` is the build, as a generator of short units:

1. `filters.layer_plan.resolve_input` decides how the stack below can be
   turned into pixels. It refuses before any video memory is spent.
2. `filters.composite` draws that stack into one texture.
3. The layer's kind runs over it: its list of passes, or its own build
   where it has one. Then the result is encoded to sRGB.
4. The result is read back as bytes, one band of rows at a time. Each
   band goes straight into a PNG (`filters.png`).
5. `commit` packs that PNG into the layer's image and stamps it.

The build is a generator because only the last unit writes anything,
apart from the layer's Object, which the first unit fills in. So a build
can be cancelled without anything changing on screen. The
viewport keeps showing the previous result until the commit, and the
commit is one `pack` of an already encoded file plus four ID properties.
The textures live in the generator's frame, so abandoning the generator
frees them too.

The build writes its own PNG instead of using Blender's, for speed.
Writing the pixels into the image and calling `Image.pack` encodes the
whole picture in one call, about 0.9 s at 4K, the longest freeze a build
would have. Encoding band by band at zlib level 1 spreads that cost over
the readback units. The packed file is then the only copy of the pixels
until the next draw decodes it.

Colour space is easy to get wrong. Everything before the encode is scene
linear with straight alpha, which is what the render engines feed a
shader and what the composite reproduces. The derived image is byte
sRGB like a painted layer image, so the values are encoded on the way
out, and the compiled Image Texture node decodes them back. The encode
is a GPU pass, not numpy, which keeps a 4K build in milliseconds instead
of tenths of a second. The pass draws into a byte target, so the GPU
rounds each value to the byte the file stores.
"""
from __future__ import annotations

import hashlib
import logging

import bpy

from ..compiler.bake import create_managed_image
from ..compiler.core import build_ir, mark_dirty
from ..compiler.ir import hash_payload
from ..gpu_passes.core import BAND_ROWS, read_color_bytes
from . import composite, derived, freshness, layer_plan
from .core import FilterSpec, Refused, new_texture, run_pass
from .layer_specs import layer_filter_kind
from .png import RGBAStream
from .registry import ENCODE_SRGB

log = logging.getLogger(__name__)

# Rows per readback. Reading 4096 rows at once stalls for about a second
# on the test machine, far too long between modal events. This is the
# same band size that `run_pass` draws in.
READ_ROWS = BAND_ROWS


def build_layer(context, tree, node) -> bpy.types.Image:
    """Build *node*'s derived image, running `steps` to the end.

    For scripts and tests. An operator drives `steps` itself, so it can
    show progress and be cancelled.
    """
    run = steps(context, tree, node)
    while True:
        try:
            next(run)
        except StopIteration as done:
            return done.value


def steps(context, tree, node, *, plan=None):
    """The build as short units, yielding ``(label, fraction)`` after each.

    *plan* is a `filters.layer_plan.InputPlan` already resolved for this
    layer, or None to resolve one here. Raises `filters.core.Refused` with
    the message for the UI. Returns the built image.

    Nothing outside the layer changes until the last unit. A caller that
    stops driving the generator leaves the previous result bit-identical.
    The same holds for video memory, because the textures live in the
    generator's frame. Abandoning it raises `GeneratorExit` at the
    current yield, and the pool is closed on the way out.

    Peak texture use: a list of passes holds two pool textures at once,
    the one a pass reads and the one it draws into. An unsharp mask holds
    three, because it also keeps the composite. A kind with its own build
    decides for itself. The painter holds four while it analyses the
    picture, as `filters.painter.build` explains. The encode draws into a
    byte target of its own, and the pool is closed before the readback,
    so the readback holds only that target.
    """
    if plan is None:
        plan = layer_plan.resolve_input(context, tree, node)
    if not plan.is_composite:
        raise Refused(f"Filtering the layers below '{node.name}' needs a Cycles bake, "
                      f"because {plan.reason}")
    kind = layer_filter_kind(node)
    # The build goes ahead, so the layer remembers the mesh it needed.
    # This is the one write before the commit. It is made here, not in
    # `commit`, so the refresh job's checks while the build runs
    # (`_Job.moved`) resolve against the same mesh whatever is selected
    # meanwhile. A cancelled Update keeps it outside any undo step, which
    # is harmless: it names the mesh the plan resolved to, which the next
    # build would store anyway.
    layer_plan.keep_surface(node, plan)
    width, height = size = (int(node.resolution), int(node.resolution))
    # Recorded before anything is read, and this is what the commit
    # stamps. If a setting changes or a stroke lands during the build,
    # the layer stays out of date, instead of being stamped for pixels
    # the build never saw. The node's settings are also read here, so the
    # stamp and the pixels agree on them. `plan` already holds the stack
    # below as values.
    read = inputs_of(tree, node, plan)
    settings = kind.settings_of(node) if kind.build is not None else kind.passes_of(node)
    with freshness.reading(node.uuid):
        yield f"{kind.label}: compositing the layers below", 0.0

        # Where filtering ends on the progress bar. A list of passes is
        # quick and the readback is the slow part. A kind with its own
        # build, such as the painter, is the other way round.
        filtered = 0.3 if kind.build is None else 0.8
        pool = composite.Pool(size)
        try:
            current = composite.composite_below(plan.chain, pool)
            if kind.build is not None:
                current = yield from _built(kind, settings, current, pool, 0.2, filtered)
            else:
                current = yield from _filtered(kind, settings, current, pool, 0.2, filtered)
            yield f"{kind.label}: filtering", filtered
            framebuffer, encoded = _encoded(current, size)
            # Dropping the last reference frees the float texture, so the
            # readback below holds only the byte target.
            pool.release(current)
            current = None
            pool.close()

            # PNG rows start at the top and framebuffer rows at the
            # bottom, so the bands are read top first and each is flipped.
            # The digest makes the build stamp change when the picture
            # does (`derived.BUILD_KEY`). It takes the bands unflipped, as
            # they come off the GPU, because it only needs the same order
            # every time.
            png = RGBAStream(width, height)
            digest = hashlib.blake2b(digest_size=16)
            firsts = range(0, height, READ_ROWS)
            for index, first in enumerate(reversed(firsts)):
                band = read_color_bytes(framebuffer, width, first, min(height, first + READ_ROWS))
                digest.update(band)
                png.add(band[::-1])
                yield (f"{kind.label}: reading the result back",
                       filtered + (0.9 - filtered) * (index + 1) / len(firsts))
        finally:
            pool.close()

        yield f"{kind.label}: writing the result", 0.9
        return commit(tree, node, plan, png.finish(), digest.hexdigest(), size, read)


def inputs_of(tree, node, plan) -> tuple[str, int]:
    """What a build of *node* started now would read, to compare later.

    Returns the structural fingerprint and the number of strokes noticed
    below the layer so far (`filters.freshness.changes`). Two results
    differ exactly when something the build depends on changed between
    them.
    """
    return _fingerprint(tree, node, plan), freshness.changes(node.uuid)


def _filtered(kind, passes, current, pool, start, end):
    """Run *kind*'s *passes* over *current* and return the resulting texture.

    A kind runs as many passes as its parameters need. A separable blur
    is two per iteration. Each pass is its own unit, so a wide blur can
    be cancelled partway instead of being one long stall. The list is
    empty when the parameters ask for nothing, such as a blur set to
    zero, and the stack below is then the result.
    """
    # An unsharp mask reads the stack below next to its blur, so the
    # composite is kept out of the pool for the whole chain, instead of
    # being reused by the second pass. This costs one more texture, and
    # only for a kind that needs it.
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
    """Run *kind*'s own build with *settings* over *current*.

    Its progress is mapped between *start* and *end* on the progress bar.
    The build hook owns *current* from here on and returns a pool
    texture. The hook is closed explicitly when the build is abandoned,
    so its textures go back to the pool before the pool itself is closed.
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

    Returns the framebuffer with the target, as `run_pass` does. Reading
    a framebuffer whose texture has been freed gives zeroes, not an
    error.
    """
    return run_pass(spec, texture, pool.acquire(), second=second, params=params)


def _encoded(texture, size):
    """*texture* encoded to sRGB in a new `RGBA8` target, with its framebuffer.

    The target holds bytes because the file stores bytes. The GPU then
    rounds each value once, when it writes it. Also, `read_color_bytes`
    can only read a byte texture.
    """
    return run_pass(ENCODE_SRGB, texture, new_texture(size, 'RGBA8'), params={})


def commit(tree, node, plan, data, digest, size, read):
    """Pack *data*, the result as a PNG, into *node*'s derived image and stamp it.

    *digest* is a digest of the pixel bytes in the PNG. *read* is what
    `inputs_of` returned when the build started. The stamp uses *read*,
    not what the layer asks for now, because the pixels come from what
    was read then.

    The pack comes before the stamps, for the reason `filters.derived`
    gives: a stamp written first could describe pixels that are gone.
    Here the packed file is the only copy of the pixels, so the order is
    what prevents that.

    A build can produce exactly the pixels the image already holds, for
    example when a stroke below is undone before the refresh runs. Only
    a build can find that out, and the commit then acts on it. It skips
    the pack, keeps the decoded pixels, and does not mark the filter
    layers above as changed.
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
    # Clear the flag only when no stroke below has landed since the build
    # read the pixels. Earlier strokes are then in the result. It is
    # cleared after the write, not before, so a build abandoned partway
    # leaves the layer still asking for a build.
    if freshness.changes(node.uuid) == changes:
        node.derived_stale_pixels = False
    if not unchanged:
        # This image may be a source for a filter layer above, and the
        # depsgraph does not report this write.
        freshness.note_image_changed([image.session_uid])
    # The datablock is reused, so the node's pointer does not change and
    # its update callback does not fire. Without this, the new stamps
    # would not trigger a recompile. An unchanged build compiles too,
    # because the compile is what tells the auto job the layer settled.
    mark_dirty(tree)
    log.debug("built %s for %s at %dx%d%s", image.name, node.name, *size,
              " (unchanged)" if unchanged else "")
    return image


def _holds(image, stamps) -> bool:
    """True when *image* already holds the build *stamps* describe.

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
        # One texel is enough. The packed file brings its own size, and a
        # full-size generated buffer would only be thrown away.
        image = create_managed_image(f"{tree.name} {node.name} Filter", 1, 1)
    # Reuse the datablock instead of replacing it, as `bake_node_cache`
    # does. Each replacement is a name Blender has to make unique and an
    # orphan for the next cleanup to find. Do not read the size or scale
    # the image here, because either would decode what was just packed.
    # Neither is needed: packing a PNG of another size resizes the image.
    image.pack(data=data, data_len=len(data))
    # A generated image ignores its packed file and would come back black
    # from an undo or a copy. A file image reads it. Switching the source
    # on the first build also drops the generated buffer.
    if image.source != 'FILE':
        image.source = 'FILE'
    # The image's buffer still holds the previous result. Once it is
    # freed, the next read decodes the new file. `reload()` would also
    # work, but it looks for a file on disk first and reports the empty
    # path as missing.
    image.buffers_free()
    return image


def _fingerprint(tree, node, plan) -> str:
    """What the build was asked for: the structural half of freshness.

    It records the build's inputs, not its result, so a later compile can
    tell that nothing structural has changed without reading a pixel. The
    IR is built only to get a context to hash with. The compile that
    `commit` asks for afterwards makes the same context and compares.
    """
    ctx = build_ir(tree).ctx
    return freshness.stamp(freshness.fingerprint_parts(ctx, node, plan.source, plan.surface))
