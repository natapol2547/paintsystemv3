# SPDX-License-Identifier: GPL-3.0-or-later
"""Clear, Fill, Invert, Blur and Sharpen on the active layer (PS-052, PS-051).

An action edits exactly what the brush could paint: the active layer's
image, limited to the live selection, and never a layer the brush cannot
reach either. Everything it will not do it refuses by name, so the user
reads why instead of watching nothing happen.

The scope rule, which is the safety rule:

- no selection on the tree: the whole layer;
- a selection: inside its mask, weighted by coverage, so soft edges blend;
- a selection whose mask covers no texel of this layer: nothing at all,
  with a message. `selection.session` treats that mask as no selection
  (`NOTHING_SELECTED`) because painting through it is harmless. Clearing
  a whole layer because a box was dragged over empty background is not,
  so these actions part company with that rule here.

Each action is one write through `undo.pixels`, so one Ctrl+Z takes it
back, and it changes no document data in the same step (PS-090). An
action of several passes is still one write: `apply_passes` runs the
chain on the GPU and reads back once.
"""
from ..gpu_passes.core import read_color
from ..selection import raster
from ..undo import pixels as undo_pixels
from . import brush_color, core, registry

CLEAR, FILL, INVERT = 'CLEAR', 'FILL', 'INVERT'
BLUR, SHARPEN = 'BLUR', 'SHARPEN'

NOTHING_COVERED = "The selection covers no pixels of this layer"
"""Refusal of a selection that is live but misses the layer."""

NOTHING_TO_DO = "That radius is too small to change a pixel"
"""Refusal of a blur or a sharpen whose radius rounds to nothing."""


class ActionTarget:
    """The layer and image one action runs on, resolved once."""

    __slots__ = ('tree', 'layer', 'image')

    def __init__(self, tree, layer, image):
        self.tree = tree
        self.layer = layer
        self.image = image


def _consumers(tree) -> dict[str, list]:
    """Nodes reading each node's outputs, by node name, from one pass over the links."""
    found: dict[str, list] = {}
    for link in tree.links:
        found.setdefault(link.from_node.name, []).append(link.to_node)
    return found


def _showing_cache(node) -> bool:
    """Whether *node* is compiled as its baked image rather than from its inputs.

    The compiler's own test also compares the stored hash with the tree's
    (`compiler.core.CompileContext.is_cached`); this is the cheap half,
    which is what the operator needs and errs towards refusing.
    """
    return (getattr(node, 'cache_enabled', False)
            and getattr(node, 'cache_image', None) is not None
            and not getattr(node, 'cache_stale', False))


def _cache_hiding(tree, layer):
    """A node whose live cache would hide an edit of *layer*, or None.

    A cache stands for its node and everything that feeds it, and holds
    pixels, not a hash of them, so an edit under one changes nothing on
    screen until the bake runs again.
    """
    consumers = _consumers(tree)
    seen = {layer.name}
    queue = [layer]
    while queue:
        node = queue.pop()
        if _showing_cache(node):
            return node
        for reader in consumers.get(node.name, ()):
            if reader.name not in seen:
                seen.add(reader.name)
                queue.append(reader)
    return None


def resolve_target(context, action: str) -> ActionTarget:
    """What *action* would edit, or `Refused` with the reason it cannot."""
    from ..context import parse_context

    ps = parse_context(context)
    tree = ps.tree
    if tree is None:
        raise core.Refused("No Paint System tree is active")
    layer = ps.layer
    if layer is None:
        raise core.Refused("No active layer")
    if layer.lock_layer:
        raise core.Refused(f"Layer '{layer.name}' is locked")
    image = getattr(layer, 'paint_image', None)
    if image is None:
        raise core.Refused(f"Layer '{layer.name}' has no image to edit")
    cached = _cache_hiding(tree, layer)
    if cached is not None:
        raise core.Refused(f"Layer '{cached.name}' shows its baked cache; "
                           "turn Use Cache off to edit through it")
    if image.source == 'TILED':
        raise core.Refused("UDIM layers are not supported yet")
    if image.source not in {'GENERATED', 'FILE'}:
        raise core.Refused("Only still images can be edited")
    if image.library is not None or image.is_library_indirect:
        raise core.Refused(f"Image '{image.name}' is linked from another file")
    if image.channels != 4:
        raise core.Refused(f"Image '{image.name}' has no alpha channel to edit")
    if action == CLEAR and layer.lock_alpha:
        raise core.Refused("Clear changes transparency, and this layer has Lock Alpha on")
    if not image.has_data or not all(image.size):
        raise core.Refused(f"Image '{image.name}' has no pixels; is its file missing?")
    return ActionTarget(tree, layer, image)


def selection_mask(target: ActionTarget):
    """The selection's mask texture for *target*, or None for no selection.

    Raises `Refused` when a selection exists but no mask can stand for
    it, and when the mask it builds covers nothing of this layer.
    """
    selection = target.tree.selection
    if not len(selection.ops):
        return None
    size = raster.image_size(target.image)
    problem = raster.availability(selection, size)
    if problem:
        raise core.Refused(problem)
    try:
        mask = raster.get_mask(selection, size)
    except raster.MaskUnavailable as error:
        raise core.Refused(str(error)) from error
    if mask is None:
        return None
    if mask.is_empty():
        raise core.Refused(NOTHING_COVERED)
    return mask.texture


def _passes(context, action: str, target: ActionTarget, channels, sigma, strength):
    """The passes *action* runs over the layer, in order.

    A pass sees what the image stores, so `encode` says whether that is
    scene linear and needs its sRGB encoding taken first. A byte layer
    already holds one.
    """
    if action == CLEAR:
        return [(registry.CLEAR, {})]
    if action == FILL:
        colour = brush_color.stored_fill_color(context, target.image)
        return [(registry.FILL, {"color": (*colour, 1.0),
                                 "lock_alpha": int(target.layer.lock_alpha)})]
    if action == INVERT:
        if channels[3] and target.layer.lock_alpha:
            raise core.Refused("Inverting alpha changes transparency, and this layer has "
                               "Lock Alpha on")
        return [(registry.INVERT, {
            "channels": tuple(1.0 if on else 0.0 for on in channels),
            # A float layer holds scene linear; inverting its sRGB
            # encoding is what makes it match a byte layer.
            "encode": int(target.image.is_float),
        })]
    blur = [(registry.BLUR, params) for params in registry.blur_passes(sigma)]
    if blur and _stores_srgb_bytes(target.image):
        # Blur in linear light, as a float layer and a filter layer do,
        # so the same blur looks the same on every kind of layer.
        blur = [(registry.DECODE_SRGB, {}), *blur, (registry.ENCODE_SRGB, {})]
    if action == BLUR:
        return blur
    if action == SHARPEN:
        # Without a blur there is no detail to tell apart from the
        # picture, so the combine would subtract the layer from itself.
        if not blur:
            return []
        return blur + [(registry.SHARPEN, {"strength": strength,
                                           "encode": int(target.image.is_float)})]
    raise ValueError(f"Unknown action {action!r}")


def _stores_srgb_bytes(image) -> bool:
    """Whether *image* holds sRGB-encoded bytes rather than linear or data values."""
    return not image.is_float and image.colorspace_settings.name == 'sRGB'


_COMPOSE = core.FilterSpec(
    name="compose",
    apply_source="""
/* Become the second texture. On its own that is a copy; run with a mask
   it is how a filter of several passes is limited to a selection, which
   `apply_passes` explains. It lives here rather than in `registry`
   because it is part of the masking and not a filter anyone chooses. */
vec4 apply(ivec2 texel, vec4 c)
{
  return stored_to_straight(texelFetch(second, texel, 0));
}
""",
    reads_second=True,
)


def apply_passes(passes, image, *, mask=None) -> bool:
    """Run *passes* over *image* in order and write the result back, undoably.

    *passes* is a non-empty list of ``(spec, push constants)``. A spec
    whose `reads_second` is set reads the image's own values, which is
    what an unsharp mask needs: by the time the combine runs, the chain
    holds the blur.

    The mask is where this is more than a loop. A single pass takes it
    directly, which keeps a masked Invert exactly ``255 - k`` inside the
    selection and bit-identical outside it. Several passes cannot: a
    masked blur would blend each pass against the half-filtered picture
    it was drawn from rather than against the layer, so the passes run
    unmasked and one more pass composes the result over the original
    through the mask. The blending is the same either way -- it is
    `core._MAIN` doing it in both -- so the edge of a selection behaves the
    same for a blur as for a fill.

    The write goes through `undo.pixels.write_pixels`, so one Ctrl+Z
    takes it back. Returns False when the pixels are written but the
    undo step could not be pushed.
    """
    source = core.PixelSource.from_image(image)
    try:
        original = source.texture
        current = original
        # One pass carries the mask itself; several compose at the end.
        inline_mask = mask if len(passes) == 1 else None
        for spec, params in passes:
            framebuffer, current = core.run_pass(
                spec, current, storage=source.storage, mask=inline_mask,
                second=original if spec.reads_second else None, params=params)
        if mask is not None and inline_mask is None:
            framebuffer, current = core.run_pass(
                _COMPOSE, original, storage=source.storage, mask=mask, second=current)
        values = read_color(framebuffer, source.width, source.height)
        return undo_pixels.write_pixels(image, values)
    finally:
        source.release()


def run_action(context, action: str, *, channels=(True, True, True, False),
               sigma: float = 0.0, strength: float = 1.0) -> bool:
    """Run *action* on the active layer, and report whether Ctrl+Z will undo it.

    Raises `Refused` when it cannot run; nothing is written then.
    """
    target = resolve_target(context, action)
    mask = selection_mask(target)
    passes = _passes(context, action, target, channels, sigma, strength)
    if not passes:
        raise core.Refused(NOTHING_TO_DO)
    return apply_passes(passes, target.image, mask=mask)
