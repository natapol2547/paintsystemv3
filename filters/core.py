# SPDX-License-Identifier: GPL-3.0-or-later
"""One GPU pass over a layer image, in and out of the image's own pixels (PS-050).

`PixelSource.from_image` reads `Image.pixels` into a float32 array and
uploads it unchanged. `run_pass` draws one full-target fragment pass in
bands, and `filters.actions.apply_passes` reads the result back into the
image. Nothing here converts colour: what the image
stores is what the pass sees, so an identity filter writes the bytes it
read and `Invert` gives exactly ``255 - k``. A byte image is carried in
`RGBA16F`, whose 11 bits of mantissa round back to the byte they came
from; a float image is carried in `RGBA32F` unchanged.

`gpu.texture.from_image` is not used for the source. It returns an sRGB
byte image as straight linear values and a float image through a CPU
conversion, neither of which can be written back.

Storage conventions, which the prelude converts for a filter:

- byte images hold straight alpha in the image's own colour space;
- float images hold premultiplied scene linear.

The prelude also carries `ps_to_srgb` and `ps_to_linear`, which a filter
needs whenever the two disagree about colour space rather than about
alpha: inverting a float layer, or writing a scene-linear composite into
a byte sRGB image.

Colour under zero alpha has no straight form, so a float texel with
``a == 0`` reaches a filter as transparent black and its stored colour
does not survive the pass. Nothing visible changes, and no filter here
reads colour a texel does not show.

The mask is any `R32F` texture the size of the image: a selection mask
from `selection.raster`, or later the coverage of a flood fill (PS-095).
It is sampled quantised to 8 bits, exactly as the brush stencil reads a
selection, so an action changes what a stroke would have painted. A texel
the mask leaves at zero is copied through bit-exactly.
"""
import logging
from dataclasses import dataclass, field

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from ..gpu_passes.core import UNIT_QUAD, offscreen_state, release_unused_sampler, unused_sampler

log = logging.getLogger(__name__)

# Rows per draw, as in selection/raster.py: a long pass on a busy GPU can
# trip a driver's watchdog, and a band that ends with a one-texel read
# keeps the queue short.
BAND_ROWS = 512

STRAIGHT, PREMULTIPLIED = 0, 1


class Refused(Exception):
    """A filter cannot run on what is active. `str()` is the message for the UI."""


def new_texture(size, image_format: str, *, data=None) -> gpu.types.GPUTexture:
    """A new GPU texture, or `Refused` when the GPU has no room for it.

    Blender raises a bare `RuntimeError` when an allocation fails. The
    operators only catch `Refused`, so this turns a large image on a small
    GPU into a message rather than a traceback.
    """
    # Blender 4.2 to 5.0 reject `data=None`, so pass it only when there is some.
    extra = {} if data is None else {"data": data}
    try:
        return gpu.types.GPUTexture(size, format=image_format, **extra)
    except RuntimeError as error:
        log.warning("Could not allocate a %sx%s %s texture: %s",
                    size[0], size[1], image_format, error)
        raise Refused("The GPU does not have enough memory for an image this size") from error

_VERTEX = """
void main()
{
  v_texel = vec2(position.x * target_size.x, mix(rows.x, rows.y, position.y));
  gl_Position = vec4(v_texel / target_size * 2.0 - 1.0, 0.0, 1.0);
}
"""

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
  /* Partial coverage mixes premultiplied, so a feathered Clear lowers
     alpha and leaves colour alone. Where both sides are invisible the
     colour is mixed straight instead, so colour under transparency
     follows the action rather than fading to black. */
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
    is straight colour in the image's storage space and the result is
    read the same way. `params` are the push constants it reads, as
    ``(type, name)`` pairs; `run_pass` takes their values by name.
    """

    name: str
    apply_source: str
    params: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    # Whether `apply` reads the `second` sampler as well as `source`. An
    # unsharp mask needs the picture it started from alongside the blur
    # of it, and by then the chain has overwritten the first. The caller
    # says which texture that is; the spec only says that it wants one.
    # There is one `storage` for the pass, so the second texture has to
    # hold alpha the same way the source does.
    reads_second: bool = False


def storage_of(image: bpy.types.Image) -> int:
    """Whether *image* stores premultiplied or straight alpha.

    Blender's float buffers are premultiplied whatever `alpha_mode` says;
    byte buffers hold what was painted into them, which is straight.
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
    info.vertex_source(_VERTEX)
    info.fragment_source(_PRELUDE + spec.apply_source + _MAIN)
    shader = gpu.shader.create_from_info(info)
    _shaders[spec.name] = (shader, batch_for_shader(shader, 'TRIS', UNIT_QUAD))
    return _shaders[spec.name]


class PixelSource:
    """The stored values of one image, on the GPU and in a numpy array.

    The array stays alive for as long as the texture: `gpu.types.Buffer`
    shares its memory rather than copying it.
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

    *target* defaults to a new texture the size and format of *source*.
    The pass covers *target*. That is the size of the source for a
    filter; a pass that reads the source at coordinates of its own, such
    as a reduction or a gather, can draw into a smaller one, whose texels
    `apply` still sees as its own and `_MAIN` still reads the source at
    -- inside it, so long as the target is no larger.

    *storage* is how *source* holds alpha, `STRAIGHT` or `PREMULTIPLIED`,
    as `storage_of` reports for an image. *mask* is an `R32F` texture the
    size of the source, or None to cover the whole image. *second* is a
    texture the size of the source for a spec whose `reads_second` is
    set, such as the unsharp mask reading what it started from. *params*
    holds a value per push constant of the spec, by name.

    Both the framebuffer and its texture come back, and both have to be
    held until the result is read: a `GPUFrameBuffer` does not keep its
    colour slot alive, and reading one whose texture Python has already
    freed gives zeroes rather than an error.
    """
    if target is None:
        target = new_texture((source.width, source.height), source.format)
    shader, batch = _shader(spec)
    kinds = dict((name, kind) for kind, name in spec.params)
    framebuffer = gpu.types.GPUFrameBuffer(color_slots=(target,))
    sync = gpu.types.Buffer('FLOAT', 4)
    with offscreen_state(), framebuffer.bind():
        for first in range(0, target.height, BAND_ROWS):
            last = min(target.height, first + BAND_ROWS)
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
            if last < target.height:
                # Reading one texel waits for the band, so the driver
                # sees a stream of short draws rather than one long one.
                framebuffer.read_color(0, first, 1, 1, 4, 0, 'FLOAT', data=sync)
    return framebuffer, target


def release() -> None:
    """Give the cached shaders and the unused sampler back to the GPU context."""
    _shaders.clear()
    release_unused_sampler()
