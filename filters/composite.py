# SPDX-License-Identifier: GPL-3.0-or-later
"""The stack below a filter layer, composited on the GPU (PS-057).

A filter layer needs the picture the render engines would draw for
everything under it, as one RGBA buffer it can run a filter pass over.
Baking that with Cycles is exact and takes seconds; this draws it in a
handful of passes instead, for the stacks it can be certain about.

"Certain" is what `plan_below` decides. It walks the same links the
compiler reads -- `feeding_link`, `clip_base`, `feeds_clip_run`, a
folder's `Content Color` -- and mirrors `PaintSystemLayerNode.emit`
branch for branch, so the two cannot drift quietly: anything the walk
does not recognise raises `Unsupported` and the caller bakes instead.
Planning allocates nothing, so a refusal costs no video memory.

The buffers are `RGBA16F`, for the reason `filters.core.texture_format`
gives: 11 bits of mantissa hold every ``k / 255`` exactly, at half the
memory of `RGBA32F`. They come from a pool that takes them back as the
walk finishes with them, so peak video memory is flat in the depth of
the stack rather than linear -- the backdrop, the layer's own content,
and one held backdrop per open clip run.

Source images are read through `gpu.texture.from_image`, which shares
the texture the viewport already has and decodes sRGB in hardware, so a
byte image arrives as straight scene-linear values: what the shader
graph carries between layers. A float image is stored premultiplied
(`filters.core.storage_of`) and is divided back out here. Every image is
sampled by normalised coordinate rather than by texel, so a source at a
different resolution than the filter's lands in the right place -- which
is only true while every layer below shares one UV map, and checking
that is `filters.layer_plan`'s job, not this module's.

A linked image is not turned away: both paths only ever read it.
"""
from __future__ import annotations

from dataclasses import dataclass

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from ..gpu_passes.core import UNIT_QUAD, offscreen_state, release_unused_sampler, unused_sampler
from ..nodetree.stack_ops import (clip_base, feeding_link, feeds_clip_run,
                                  layers_down_from, link_index)
from . import blend_glsl, derived
from .core import PREMULTIPLIED, Refused, new_texture, storage_of

TARGET_FORMAT = 'RGBA16F'
TRANSPARENT = (0.0, 0.0, 0.0, 0.0)

# The layer types the walk draws. Everything else -- the layers that
# evaluate surface data, and any type added later -- falls back.
DRAWN_TYPES = frozenset({'IMAGE', 'SOLID_COLOR', 'FOLDER', 'FILTER'})


class Unsupported(Exception):
    """The composite path cannot draw this subtree; bake it instead.

    `str()` names the reason, which the layer's state row shows. It is
    not a failure: `filters.core.Refused` is what neither path can do.
    """


# ── The plan ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LayerStep:
    """One layer of a chain, and how the walk reaches its output.

    *placement* mirrors the branches of `PaintSystemLayerNode.emit`:

    - ``'BLEND'`` -- composite over the stack below, the ordinary case;
    - ``'CLIP'`` -- clipped, with a layer above that still blends the run;
    - ``'CLIP_TOP'`` -- the top of a clip run: composite onto the base's
      content, then blend base and run together over the stack below;
    - ``'PASS'`` -- a clip base, whose own content is the run's backdrop
      and whose blend the top of the run runs instead.

    *strength* is Opacity, or a filter layer's Amount, already zeroed
    for a disabled layer. *content* is a folder's chain; *image* and
    *fill* are every other type's own pixels.
    """
    node: bpy.types.Node
    placement: str
    rule: str
    blend_mode: str
    strength: float
    mask: float
    image: bpy.types.Image | None
    fill: tuple
    content: ChainPlan | None
    uv_map: str
    base_index: int
    holds_run: bool


@dataclass(frozen=True)
class ChainPlan:
    """Everything feeding one slot, bottom-up, and what it reads.

    *images* and *uv_maps* gather a folder's content too, so a caller can
    check the whole subtree without walking the plan again.
    """
    layers: tuple[LayerStep, ...]
    images: tuple[bpy.types.Image, ...]
    uv_maps: frozenset[str]


def plan_below(node) -> ChainPlan:
    """Plan the composite of everything feeding *node*'s ``Color`` input.

    That socket is exactly what the compiler hands the layer as Prev
    Color, so for a clipped filter layer it is its base's own content
    rather than the stack -- which is what such a layer filters.

    Raises `Unsupported` when the stack holds something this path does
    not draw, and `filters.core.Refused` when the bake could not help
    either.
    """
    tree = node.id_data
    with link_index(tree):
        return _plan_chain(node.inputs['Color'], set(), tree.get_input_node())


def _plan_chain(socket, visited: set[str], group_input) -> ChainPlan:
    nodes = list(layers_down_from(socket, visited))
    # The walk stops at anything that is not a layer -- a group layer, a
    # hand-made link, a cycle -- and the compiler goes on compositing it,
    # so a chain that still has something under it would come out of here
    # missing a layer rather than refusing. The Group Input is the one
    # thing that legitimately sits under a channel: it is the transparent
    # backdrop the walk starts from anyway.
    bottom = feeding_link(nodes[-1].inputs['Color'] if nodes else socket)
    if bottom is not None and bottom.from_node != group_input:
        below = bottom.from_node
        raise Unsupported(f"'{below.name}' below is a {below.bl_label}, "
                          "which needs a render to draw")
    nodes.reverse()
    positions = {layer.name: index for index, layer in enumerate(nodes)}
    bases = {base.name for base in (clip_base(layer) for layer in nodes)
             if base is not None}

    steps = []
    images: list[bpy.types.Image] = []
    uv_maps: set[str] = set()
    for layer in nodes:
        step = _plan_layer(layer, positions, visited, group_input,
                           holds_run=layer.name in bases)
        steps.append(step)
        if step.content is not None:
            images.extend(step.content.images)
            uv_maps |= step.content.uv_maps
        elif step.image is not None:
            images.append(step.image)
            uv_maps.add(step.uv_map)
    return ChainPlan(tuple(steps), tuple(images), frozenset(uv_maps))


def _plan_layer(layer, positions, visited, group_input, *, holds_run: bool) -> LayerStep:
    ps_type = getattr(layer, 'ps_type', '')
    if ps_type not in DRAWN_TYPES:
        raise Unsupported(f"Layer '{layer.name}' is a {layer.bl_label} layer, "
                          "which needs a render to draw")
    if feeding_link(layer.inputs['Mask']) is not None:
        raise Unsupported(f"Layer '{layer.name}' has a linked mask")

    base = clip_base(layer)
    top_of_run = not feeds_clip_run(layer)
    base_index = -1
    if base is not None:
        placement = 'CLIP_TOP' if top_of_run else 'CLIP'
        base_index = positions.get(base.name, -1)
        if base_index < 0:
            raise Unsupported(f"Layer '{layer.name}' clips to a layer outside its own stack")
    else:
        placement = 'BLEND' if top_of_run else 'PASS'

    rule = blend_glsl.BLEND
    strength = layer.opacity if layer.enabled else 0.0
    content = None
    image = None
    fill = TRANSPARENT
    uv_map = ""
    if ps_type == 'FOLDER':
        content = _plan_chain(layer.inputs['Content Color'], visited, group_input)
    elif ps_type == 'SOLID_COLOR':
        red, green, blue, alpha = layer.fill_color
        fill = (red, green, blue, alpha)
    elif ps_type == 'IMAGE':
        image = _source_image(layer.image)
        uv_map = layer.uv_map
    else:
        # A filter layer replaces the stack below rather than
        # compositing over it, and is transparent until it is built.
        rule = blend_glsl.FILTER_MIX
        strength = layer.amount
        if derived.is_built(layer.derived_image):
            # Not `_source_image`: a built result is packed by the build
            # that stamped it, so there is nothing to check, and reading
            # the size of one just committed would decode the whole file
            # to learn it (`filters.layer_build.commit`).
            image = layer.derived_image
            uv_map = derived.stamped_uv_map(image)

    if rule == blend_glsl.BLEND and layer.blend_mode not in blend_glsl.ALLOWED_BLEND_MODES:
        raise Unsupported(f"Layer '{layer.name}' blends with {layer.blend_mode}, "
                          "which has no GPU parity test")

    return LayerStep(node=layer, placement=placement, rule=rule,
                     blend_mode=layer.blend_mode, strength=strength,
                     mask=layer.inputs['Mask'].default_value, image=image,
                     fill=fill, content=content, uv_map=uv_map,
                     base_index=base_index, holds_run=holds_run)


def _source_image(image):
    """*image*, checked for what neither path can read. None stays None.

    The size is the test, not `has_data`: a generated image regenerates
    its buffer lazily and reads as having no data until something asks
    for it, while an image whose file is gone reports no size at all.
    `filters.core.PixelSource.from_image` draws the same line.
    """
    if image is None:
        return None
    if image.source == 'TILED':
        raise Refused(f"Image '{image.name}' below is a UDIM image, which is not supported yet")
    if not image.size[0] or not image.size[1]:
        raise Refused(f"Image '{image.name}' below has no pixels; is its file missing?")
    return image


# ── The draw ─────────────────────────────────────────────────────────

_VERTEX = """
void main()
{
  v_uv = position;
  gl_Position = vec4(position * 2.0 - 1.0, 0.0, 1.0);
}
"""

_FRAGMENT = """
void main()
{
  vec4 c = use_texture != 0 ? texture(source, v_uv) : fill;
  if (premultiplied != 0) {
    /* Colour under zero alpha has no straight form; it shows nothing
       either way. */
    c = c.a > 0.0 ? vec4(c.rgb / c.a, c.a) : vec4(0.0);
  }
  out_color = c;
}
"""

_shader = None
_batch = None


def source_shader():
    """The shader and batch that put a layer's own content in a target, built once."""
    global _shader, _batch
    if _shader is None:
        interface = gpu.types.GPUStageInterfaceInfo("ps_composite_source_iface")
        interface.smooth('VEC2', "v_uv")
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant('VEC4', "fill")
        info.push_constant('INT', "use_texture")
        info.push_constant('INT', "premultiplied")
        info.sampler(0, 'FLOAT_2D', "source")
        info.vertex_in(0, 'VEC2', "position")
        info.vertex_out(interface)
        info.fragment_out(0, 'VEC4', "out_color")
        info.vertex_source(_VERTEX)
        info.fragment_source(_FRAGMENT)
        _shader = gpu.shader.create_from_info(info)
        _batch = batch_for_shader(_shader, 'TRIS', UNIT_QUAD)
    return _shader, _batch


class Pool:
    """`RGBA16F` targets of one size, handed back as the walk finishes with them.

    `made` counts the ones the GPU actually allocated, which a test
    holds to the depth of the clip runs rather than of the stack.
    """

    def __init__(self, size: tuple[int, int]):
        self.size = size
        self.made = 0
        self._free: list[gpu.types.GPUTexture] = []

    def acquire(self) -> gpu.types.GPUTexture:
        if self._free:
            return self._free.pop()
        texture = new_texture(self.size, TARGET_FORMAT)
        self.made += 1
        return texture

    def release(self, texture) -> None:
        self._free.append(texture)

    def close(self) -> None:
        self._free.clear()


def composite_below(plan: ChainPlan, pool: Pool):
    """Draw *plan* into one of *pool*'s `RGBA16F` textures, and return it.

    The result holds straight alpha and scene-linear colour, the same as
    the shader graph carries, so a filter pass over it and the render
    engines are looking at the same picture.
    """
    return _draw_chain(plan, pool)


def _draw_chain(plan: ChainPlan, pool: Pool):
    """`PaintSystemLayerNode.emit` over textures, bottom-up.

    *prev* is what the next layer up sees on its ``Color`` input, which
    is the previous layer's ``Color`` output. A clip base's is kept in
    *held* until the top of its run blends the two together.
    """
    prev = _draw_fill(pool, TRANSPARENT)
    held: dict[int, gpu.types.GPUTexture] = {}
    for index, step in enumerate(plan.layers):
        if step.holds_run:
            held[index] = prev
        source = _draw_source(step, pool)
        if step.placement == 'PASS':
            out = source
        elif step.placement == 'CLIP':
            out = _draw_blend(pool, prev, source, step, clip=True)
            pool.release(source)
        elif step.placement == 'CLIP_TOP':
            run = _draw_blend(pool, prev, source, step, clip=True)
            pool.release(source)
            backdrop = held.pop(step.base_index)
            out = _draw_blend(pool, backdrop, run, plan.layers[step.base_index], clip=False)
            pool.release(run)
            pool.release(backdrop)
        else:
            out = _draw_blend(pool, prev, source, step, clip=False)
            pool.release(source)
        if not step.holds_run:
            pool.release(prev)
        prev = out
    return prev


def _draw_source(step: LayerStep, pool: Pool):
    if step.content is not None:
        return _draw_chain(step.content, pool)
    if step.image is not None:
        return _draw_image(pool, step.image)
    return _draw_fill(pool, step.fill)


def _draw_fill(pool: Pool, fill: tuple):
    return _draw_into(pool.acquire(), fill=fill)


def _draw_image(pool: Pool, image):
    # The texture has to outlive the draw, so it is held here rather
    # than passed straight through.
    texture = gpu.texture.from_image(image)
    return _draw_into(pool.acquire(), texture=texture,
                      premultiplied=storage_of(image) == PREMULTIPLIED)


def _draw_into(target, *, fill=TRANSPARENT, texture=None, premultiplied=False):
    shader, batch = source_shader()
    framebuffer = gpu.types.GPUFrameBuffer(color_slots=(target,))
    with offscreen_state(), framebuffer.bind():
        shader.uniform_float("fill", fill)
        shader.uniform_int("use_texture", 0 if texture is None else 1)
        shader.uniform_int("premultiplied", 1 if premultiplied else 0)
        shader.uniform_sampler("source", unused_sampler() if texture is None else texture)
        batch.draw(shader)
    return target


def _draw_blend(pool: Pool, backdrop, source, step: LayerStep, *, clip: bool):
    target = pool.acquire()
    # The framebuffer is not kept: the draw has already written into the
    # target, and the target is what the walk carries on with.
    blend_glsl.blend_over(backdrop, source, target, pool.size, rule=step.rule,
                          mode=step.blend_mode, opacity=step.strength * step.mask,
                          clip=clip)
    return target


def release() -> None:
    """Drop the shader and the unused sampler before the GPU context goes."""
    global _shader, _batch
    _shader = None
    _batch = None
    release_unused_sampler()
