# SPDX-License-Identifier: GPL-3.0-or-later
"""Draws the stack below a filter layer on the GPU (PS-057).

A filter layer needs the picture the render engines would draw for
everything below it, as one RGBA texture to filter. A Cycles bake gives
that exactly but takes seconds. This module draws it in a few GPU passes
instead, for the stacks it can be sure to draw correctly.

`plan_below` decides which stacks those are, and `composite_below` draws
the plan.

- `plan_below` follows the same links the compiler reads
  (`feeding_link`, `clip_base`, `feeds_clip_run`, a folder's
  ``Content Color``) and mirrors `PaintSystemLayerNode.emit` branch for
  branch. Anything it does not recognise raises `Unsupported`, and the
  caller bakes instead, so the two cannot silently disagree. Planning
  allocates nothing, so a refusal costs no video memory.
- The textures are `RGBA16F`, for the reason `filters.core.texture_format`
  gives: 11 bits of mantissa hold every ``k / 255`` exactly, at half the
  memory of `RGBA32F`.
- Textures come from a `Pool` and go back as soon as the walk is done
  with them. So peak video memory does not grow with the depth of the
  stack. It is the backdrop, the layer's own content, and one held
  backdrop per open clip run.
- Source images are read with `gpu.texture.from_image`. It shares the
  texture the viewport already has and decodes sRGB in hardware, so a
  byte image arrives as straight scene-linear values, as the shader
  graph carries them. A float image is stored premultiplied
  (`filters.core.storage_of`), so the shader divides the alpha out.
- Images are sampled by normalised coordinate, not by texel, so a source
  at another resolution still lands in the right place. That only holds
  while every layer below uses the same UV map. `filters.layer_plan`
  checks that, not this module.
- Linked images are allowed, because both paths only read them.
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

# The layer types the walk can draw. Every other type falls back to a
# bake, including layers that evaluate surface data and any type added
# later.
DRAWN_TYPES = frozenset({'IMAGE', 'SOLID_COLOR', 'FOLDER', 'FILTER'})


class Unsupported(Exception):
    """The composite path cannot draw this subtree, so it needs a bake.

    `str()` is the reason, which the layer's state row shows. This is not
    a failure. `filters.core.Refused` is for what neither path can do.
    """


# ── The plan ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LayerStep:
    """One layer of a chain, and how the walk computes its output.

    *placement* mirrors the branches of `PaintSystemLayerNode.emit`:

    - ``'BLEND'``: composite over the stack below. The normal case.
    - ``'CLIP'``: clipped, with a layer above that still continues the
      clip run.
    - ``'CLIP_TOP'``: the top of a clip run. Composite onto the base's
      content, then blend base and run together over the stack below.
    - ``'PASS'``: a clip base. Its own content is the run's backdrop, and
      the top of the run does its blend instead.

    *strength* is Opacity, or a filter layer's Amount. It is already zero
    for a disabled layer. *content* is a folder's chain. *image* and
    *fill* hold the pixels of every other layer type.
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
    """The layers feeding one input socket, bottom first, and what they read.

    *images* and *uv_maps* include folder contents too, so a caller can
    check the whole subtree without walking the plan again.
    """
    layers: tuple[LayerStep, ...]
    images: tuple[bpy.types.Image, ...]
    uv_maps: frozenset[str]


def plan_below(node) -> ChainPlan:
    """Plan the composite of everything feeding *node*'s ``Color`` input.

    The compiler passes that socket to the layer as Prev Color. For a
    clipped filter layer it carries the base's own content, not the
    stack, and that is what such a layer filters.

    Raises `Unsupported` when the stack holds something this path cannot
    draw. Raises `filters.core.Refused` when a bake could not draw it
    either.
    """
    tree = node.id_data
    with link_index(tree):
        return _plan_chain(node.inputs['Color'], set(), tree.get_input_node())


def _plan_chain(socket, visited: set[str], group_input) -> ChainPlan:
    nodes = list(layers_down_from(socket, visited))
    # The walk stops at anything that is not a layer, such as a group
    # layer, a hand-made link or a cycle. The compiler still composites
    # it, so without this check the plan would silently miss a layer.
    # The Group Input is the only node allowed under a channel. It is
    # the transparent backdrop the walk starts from anyway.
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
        # A filter layer replaces the stack below instead of compositing
        # over it. It is transparent until it is built.
        rule = blend_glsl.FILTER_MIX
        strength = layer.amount
        if derived.is_built(layer.derived_image):
            # Skip `_source_image`. A built result was packed by the
            # build that stamped it, so there is nothing to check. Also,
            # reading the size of a result just committed would decode
            # the whole file (`filters.layer_build.commit`).
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
    """Return *image*, or raise `Refused` when neither path can read it.

    None stays None. The test is the size, not `has_data`. A generated
    image builds its buffer lazily, so it reports no data until something
    asks for it. An image whose file is missing reports no size at all.
    `filters.core.PixelSource.from_image` uses the same test.
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
    /* Colour under zero alpha cannot be un-premultiplied. It is
       invisible anyway. */
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
    """Reusable `RGBA16F` textures of one size.

    The walk hands each texture back as soon as it is done with it.
    `made` counts the textures actually allocated. A test checks that it
    follows the depth of the clip runs, not the depth of the stack.
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

    The result holds straight alpha and scene-linear colour, as the
    shader graph carries it. So a filter pass over it sees the same
    picture as the render engines.

    The walk does what `PaintSystemLayerNode.emit` does, but over
    textures, from the bottom up. *prev* is what the next layer up sees
    on its ``Color`` input, which is the ``Color`` output of the layer
    below. A clip base's *prev* is kept in *held* until the top of its
    run blends the two together.
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
        return composite_below(step.content, pool)
    if step.image is not None:
        return _draw_image(pool, step.image)
    return _draw_fill(pool, step.fill)


def _draw_fill(pool: Pool, fill: tuple):
    return _draw_into(pool.acquire(), fill=fill)


def _draw_image(pool: Pool, image):
    # Keep the texture in a variable, because it has to stay alive until
    # the draw is done.
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
    # The framebuffer is not kept. The draw has already written into the
    # target, and the walk only needs the target.
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
