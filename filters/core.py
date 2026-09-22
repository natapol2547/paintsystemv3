# SPDX-License-Identifier: GPL-3.0-or-later
"""One GPU pass over a layer image's own stored values (PS-050).

`FilterSpec` describes a filter. `PixelSource.from_image` uploads an
image, `run_pass` draws one pass over the whole target in bands, and
`filters.actions.apply_passes` reads the result back into the image.

- Nothing here converts colour. The pass sees exactly what the image
  stores, so an identity filter writes back the bytes it read, and
  `Invert` gives exactly ``255 - k``.
- `PixelSource.from_image` reads `Image.pixels` into a float32 array and
  uploads it unchanged. A byte image goes into `RGBA16F`, whose 11 bits
  of mantissa round back to the original byte. A float image goes into
  `RGBA32F` unchanged.
- `gpu.texture.from_image` is not used for the source. It returns an
  sRGB byte image as straight linear values, and a float image through a
  CPU conversion. Neither can be written back.
- Byte images store straight alpha in the image's own colour space.
  Float images store premultiplied scene linear. The GLSL prelude
  converts both to straight alpha for a filter.
- The prelude also has `ps_to_srgb` and `ps_to_linear`. A filter needs
  them when the colour space differs, for example to invert a float
  layer, or to write a scene-linear composite into a byte sRGB image.
- A float texel with ``a == 0`` reaches a filter as transparent black,
  because colour under zero alpha cannot be un-premultiplied. Its stored
  colour is lost, but nothing visible changes, and no filter here reads
  colour that a texel does not show.
- The mask is any `R32F` texture the size of the image: a selection mask
  from `selection.raster`, or later the coverage of a flood fill
  (PS-095). It is quantised to 8 bits, exactly as the brush stencil reads
  a selection, so an action changes what a stroke would have painted. A
  texel where the mask is zero is copied through bit-exactly.
"""
import logging
from dataclasses import dataclass, field

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from ..gpu_passes.core import (BAND_VERTEX_SOURCE, UNIT_QUAD, context_active, draw_in_bands,
                               offscreen_state, release_unused_sampler, unused_sampler)

log = logging.getLogger(__name__)

STRAIGHT, PREMULTIPLIED = 0, 1


class Refused(Exception):
    """A filter cannot run on what is active. `str()` is the message for the UI."""


def new_texture(size, image_format: str, *, data=None) -> gpu.types.GPUTexture:
    """A new GPU texture, or `Refused` when the GPU has no room for it.

    Blender raises a bare `RuntimeError` when an allocation fails. The
    operators only catch `Refused`, so this turns a large image on a small
    GPU into a message rather than a traceback. The same error comes when
    no GPU context is bound (see `context_active`), which is a different
    problem and gets its own message.
    """
    # Blender 4.2 to 5.0 reject `data=None`, so pass it only when there is some.
    extra = {} if data is None else {"data": data}
    try:
        return gpu.types.GPUTexture(size, format=image_format, **extra)
    except RuntimeError as error:
        log.warning("Could not allocate a %sx%s %s texture: %s",
                    size[0], size[1], image_format, error)
        if not context_active():
            raise Refused("No GPU context is active right now; try again in a moment") from error
        raise Refused("The GPU does not have enough memory for an image this size") from error

_PRELUDE = """
vec3 ps_to_srgb(vec3 c)
{
  vec3 low = c * 12.92;
  vec3 high = 1.055 * pow(max(c, vec3(0.0)), vec3(1.0 / 2.4)) - 0.055;
  return mix(high, low, step(c, vec3(0.0031308)));
}

vec3 ps_to_linear(vec3 c)
{
  vec3 low = c / 12.92;
  vec3 high = pow((max(c, vec3(0.0)) + 0.055) / 1.055, vec3(2.4));
  return mix(high, low, step(c, vec3(0.04045)));
}

vec4 stored_to_straight(vec4 c)
{
  if (storage == 0) {
    return c;
  }
  return c.a > 0.0 ? vec4(c.rgb / c.a, c.a) : vec4(0.0);
}

vec4 straight_to_stored(vec4 s)
{
  return storage == 0 ? s : vec4(s.rgb * s.a, s.a);
}
"""

_MAIN = """
void main()
{
  ivec2 texel = ivec2(v_texel);
  vec4 stored = texelFetch(source, texel, 0);
  float m = 1.0;
  if (use_mask != 0) {
    /* Quantised like SelectionMask.read_bytes and the brush stencil, so
       an action covers exactly what a stroke would paint through. */
    m = floor(clamp(texelFetch(mask, texel, 0).r, 0.0, 1.0) * 255.0 + 0.5) / 255.0;
  }
  if (m <= 0.0) {
    out_color = stored;
    return;
  }
  vec4 before = stored_to_straight(stored);
  vec4 after = apply(texel, before);
  if (m >= 1.0) {
    out_color = straight_to_stored(after);
    return;
  }
  /* Partial coverage mixes premultiplied values, so a feathered Clear
     lowers alpha and leaves colour alone. Where both sides are fully
     transparent, colour is mixed straight instead. Colour under
     transparency then follows the action instead of fading to black. */
  float a = mix(before.a, after.a, m);
  vec3 c = a > 0.0 ? mix(before.rgb * before.a, after.rgb * after.a, m) / a
                   : mix(before.rgb, after.rgb, m);
  out_color = straight_to_stored(vec4(c, a));
}
"""


@dataclass(frozen=True)
class FilterSpec:
    """One single-pass filter, as the GLSL body of its `apply`.

    `apply_source` defines ``vec4 apply(ivec2 texel, vec4 c)``, where `c`
    is straight colour in the colour space the image stores. The return
    value is in the same form. `params` lists the push constants it reads
    as ``(type, name)`` pairs. `run_pass` takes their values by name.
    """

    name: str
    apply_source: str
    params: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    # True when `apply` reads the `second` sampler as well as `source`.
    # An unsharp mask needs the original picture next to its blur, and by
    # then the chain of passes has replaced the original. The caller
    # binds the texture. The spec only says that it needs one. A pass has
    # one `storage` value, so the second texture must store alpha the
    # same way as the source.
    reads_second: bool = False


def storage_of(image: bpy.types.Image) -> int:
    """`PREMULTIPLIED` or `STRAIGHT`, for how *image* stores alpha.

    Blender's float buffers are premultiplied whatever `alpha_mode` says.
    Byte buffers hold what was painted into them, which is straight.
    """
    return PREMULTIPLIED if image.is_float else STRAIGHT


def texture_format(image: bpy.types.Image) -> str:
    """The float format that holds *image*'s values without losing any.

    `RGBA16F` has 11 bits of mantissa, so every ``k / 255`` a byte image
    can hold survives the round trip exactly, at half the video memory of
    `RGBA32F`.
    """
    return 'RGBA32F' if image.is_float else 'RGBA16F'


_shaders: dict[str, tuple] = {}


def _shader(spec: FilterSpec):
    """The shader and batch for *spec*, built once per session."""
    if spec.name in _shaders:
        return _shaders[spec.name]
    interface = gpu.types.GPUStageInterfaceInfo(f"ps_filter_{spec.name}_iface")
    interface.smooth('VEC2', "v_texel")
    info = gpu.types.GPUShaderCreateInfo()
    info.push_constant('VEC2', "target_size")
    info.push_constant('VEC2', "rows")
    info.push_constant('INT', "storage")
    info.push_constant('INT', "use_mask")
    for kind, name in spec.params:
        info.push_constant(kind, name)
    info.sampler(0, 'FLOAT_2D', "source")
    info.sampler(1, 'FLOAT_2D', "mask")
    info.sampler(2, 'FLOAT_2D', "second")
    info.vertex_in(0, 'VEC2', "position")
    info.vertex_out(interface)
    info.fragment_out(0, 'VEC4', "out_color")
    info.vertex_source(BAND_VERTEX_SOURCE)
    info.fragment_source(_PRELUDE + spec.apply_source + _MAIN)
    shader = gpu.shader.create_from_info(info)
    _shaders[spec.name] = (shader, batch_for_shader(shader, 'TRIS', UNIT_QUAD))
    return _shaders[spec.name]


class PixelSource:
    """The stored values of one image, on the GPU and in a numpy array.

    The array is kept alive as long as the texture, because
    `gpu.types.Buffer` shares its memory instead of copying it.
    """

    __slots__ = ('values', 'width', 'height', 'storage', 'texture')

    def __init__(self, values, width, height, storage, texture):
        self.values = values
        self.width = width
        self.height = height
        self.storage = storage
        self.texture = texture

    @classmethod
    def from_image(cls, image: bpy.types.Image) -> "PixelSource":
        width, height = image.size
        channels = image.channels
        if not width or not height:
            raise ValueError(f"Image {image.name!r} has no pixel buffer to read")
        if channels != 4:
            raise ValueError(f"Image {image.name!r} has {channels} channels, not RGBA")
        values = np.empty(width * height * channels, dtype=np.float32)
        image.pixels.foreach_get(values)
        texture = new_texture(
            (width, height), texture_format(image),
            data=gpu.types.Buffer('FLOAT', values.size, values))
        return cls(values, width, height, storage_of(image), texture)

    def release(self) -> None:
        self.texture = None
        self.values = None


def run_pass(spec: FilterSpec, source: gpu.types.GPUTexture, target=None, *,
             storage: int = STRAIGHT, mask=None, second=None,
             params: dict | None = None) -> tuple[gpu.types.GPUFrameBuffer, gpu.types.GPUTexture]:
    """Draw *spec* from the *source* texture into *target*.

    *target* defaults to a new texture with the size and format of
    *source*. The pass covers all of *target*. For a filter that is the
    size of the source. A pass that picks its own source coordinates,
    such as a reduction or a gather, can draw into a smaller target.
    `apply` then gets the target's texel, and `_MAIN` reads the source at
    that same texel, which stays inside the source while the target is
    no larger.

    *storage* is how *source* stores alpha, `STRAIGHT` or `PREMULTIPLIED`,
    as `storage_of` reports for an image. *mask* is an `R32F` texture the
    size of the source, or None to cover the whole image. *second* is a
    texture the size of the source, for a spec with `reads_second` set,
    such as the unsharp mask reading its original. *params* maps each
    push constant name of the spec to a value.

    Returns the framebuffer and the target. Hold both until the result
    has been read. A `GPUFrameBuffer` does not keep its colour texture
    alive, and reading one whose texture Python has freed gives zeroes,
    not an error.
    """
    if target is None:
        target = new_texture((source.width, source.height), source.format)
    shader, batch = _shader(spec)
    kinds = dict((name, kind) for kind, name in spec.params)
    framebuffer = gpu.types.GPUFrameBuffer(color_slots=(target,))

    def draw_band(first, last):
        shader.uniform_float("target_size", (float(target.width), float(target.height)))
        shader.uniform_float("rows", (float(first), float(last)))
        shader.uniform_int("storage", storage)
        shader.uniform_int("use_mask", 0 if mask is None else 1)
        for name, value in (params or {}).items():
            if kinds[name] == 'INT':
                shader.uniform_int(name, value)
            else:
                shader.uniform_float(name, value)
        shader.uniform_sampler("source", source)
        shader.uniform_sampler("mask", unused_sampler() if mask is None else mask)
        shader.uniform_sampler("second", unused_sampler() if second is None else second)
        batch.draw(shader)

    with offscreen_state():
        draw_in_bands(framebuffer, target.height, draw_band)
    return framebuffer, target


def release() -> None:
    """Give the cached shaders and the unused sampler back to the GPU context."""
    _shaders.clear()
    release_unused_sampler()
