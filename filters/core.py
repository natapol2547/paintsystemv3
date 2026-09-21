# SPDX-License-Identifier: GPL-3.0-or-later
"""One GPU pass over a layer image, in and out of the image's own pixels (PS-050).

A call reads `Image.pixels` into a float32 array, uploads it unchanged,
draws one full-target fragment pass in bands, reads the result back and
hands it to an `Output`. Nothing here converts colour: what the image
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

from ..gpu_passes.core import offscreen_state, read_color
from ..undo import pixels as undo_pixels

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
    try:
        return gpu.types.GPUTexture(size, format=image_format, data=data)
    except RuntimeError as error:
        log.warning("Could not allocate a %sx%s %s texture: %s",
                    size[0], size[1], image_format, error)
        raise Refused("The GPU could not allocate the textures for this filter; "
                      "try a lower resolution") from error

_QUAD = {"position": ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0),
                      (0.0, 0.0), (1.0, 1.0), (0.0, 1.0))}

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
_placeholder = None


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
    _shaders[spec.name] = (shader, batch_for_shader(shader, 'TRIS', _QUAD))
    return _shaders[spec.name]


def _unused_sampler():
    """A 1x1 zero texture for a sampler the pass does not use.

    A sampler an info declares must be bound even where the shader never
    reads it. One texture serves both the mask and the second source: a
    GLSL sampler does not care what format is behind it, and neither
    shader reads this one.
    """
    global _placeholder
    if _placeholder is None:
        _placeholder = gpu.types.GPUTexture(
            (1, 1), format='R32F', data=gpu.types.Buffer('FLOAT', 1, [0.0]))
    return _placeholder


class PixelSource:
    """The stored values of one image, on the GPU and in a numpy array.

    The array stays alive for as long as the texture: `gpu.types.Buffer`
    shares its memory rather than copying it.
    """

    __slots__ = ('values', 'width', 'height', 'channels', 'storage', 'format', 'texture')

    def __init__(self, values, width, height, channels, storage, image_format, texture):
        self.values = values
        self.width = width
        self.height = height
        self.channels = channels
        self.storage = storage
        self.format = image_format
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
        return cls(values, width, height, channels, storage_of(image),
                   texture_format(image), texture)

    @classmethod
    def from_texture(cls, texture: gpu.types.GPUTexture, *,
                     storage: int = STRAIGHT) -> "PixelSource":
        """A source the GPU already holds, such as a composited stack.

        There is no array behind it: `values` exists to keep an uploaded
        buffer alive, and nothing was uploaded. The caller owns the
        texture -- `release` drops this reference to it and frees
        nothing, so a pooled target goes on being pooled.
        """
        return cls(None, texture.width, texture.height, 4, storage,
                   texture.format, texture)

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def new_target(self) -> gpu.types.GPUTexture:
        """A texture a pass can draw into, in the same format as the source."""
        return new_texture((self.width, self.height), self.format)

    def release(self) -> None:
        self.texture = None
        self.values = None


def run_pass(spec: FilterSpec, source: PixelSource, target=None, *, mask=None,
             second=None,
             params: dict | None = None) -> tuple[gpu.types.GPUFrameBuffer, gpu.types.GPUTexture]:
    """Draw *spec* from *source* into *target*, a new texture by default.

    The pass covers *target*. That is the size of the source for a
    filter; a pass that reads the source at coordinates of its own, such
    as a reduction or a gather, can draw into a smaller one, whose texels
    `apply` still sees as its own and `_MAIN` still reads the source at
    -- inside it, so long as the target is no larger.

    *mask* is an `R32F` texture the size of the source, or None to cover
    the whole image. *second* is a texture the size of the source for a
    spec whose `reads_second` is set, such as the unsharp mask reading
    what it started from. *params* holds a value per push constant of
    the spec, by name.

    Both the framebuffer and its texture come back, and both have to be
    held until the result is read: a `GPUFrameBuffer` does not keep its
    colour slot alive, and reading one whose texture Python has already
    freed gives zeroes rather than an error.
    """
    if target is None:
        target = source.new_target()
    shader, batch = _shader(spec)
    kinds = dict((name, kind) for kind, name in spec.params)
    framebuffer = gpu.types.GPUFrameBuffer(color_slots=(target,))
    sync = gpu.types.Buffer('FLOAT', 4)
    with offscreen_state(), framebuffer.bind():
        for first in range(0, target.height, BAND_ROWS):
            last = min(target.height, first + BAND_ROWS)
            shader.uniform_float("target_size", (float(target.width), float(target.height)))
            shader.uniform_float("rows", (float(first), float(last)))
            shader.uniform_int("storage", source.storage)
            shader.uniform_int("use_mask", 0 if mask is None else 1)
            for name, value in (params or {}).items():
                if kinds[name] == 'INT':
                    shader.uniform_int(name, value)
                else:
                    shader.uniform_float(name, value)
            shader.uniform_sampler("source", source.texture)
            shader.uniform_sampler("mask", _unused_sampler() if mask is None else mask)
            shader.uniform_sampler("second", _unused_sampler() if second is None else second)
            batch.draw(shader)
            if last < target.height:
                # Reading one texel waits for the band, so the driver
                # sees a stream of short draws rather than one long one.
                framebuffer.read_color(0, first, 1, 1, 4, 0, 'FLOAT', data=sync)
    return framebuffer, target


class LayerImage:
    """Writes the result back into the layer's own image, undoably.

    The write goes through `undo.pixels.write_pixels`, so one Ctrl+Z
    takes it back. `commit` returns False when the pixels are there but
    the undo step could not be pushed.
    """

    def __init__(self, image: bpy.types.Image):
        self.image = image

    def commit(self, values: np.ndarray) -> bool:
        return undo_pixels.write_pixels(self.image, values)


_COMPOSE = FilterSpec(
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


def apply_passes(passes, image: bpy.types.Image, output, *, mask=None) -> bool:
    """Run *passes* over *image* in order and give the result to *output*.

    *passes* is a list of ``(spec, push constants)``. A spec whose
    `reads_second` is set reads the image's own values, which is what an
    unsharp mask needs: by the time the combine runs, the chain holds the
    blur.

    The mask is where this is more than a loop. A single pass takes it
    directly, which keeps a masked Invert exactly ``255 - k`` inside the
    selection and bit-identical outside it. Several passes cannot: a
    masked blur would blend each pass against the half-filtered picture
    it was drawn from rather than against the layer, so the passes run
    unmasked and one more pass composes the result over the original
    through the mask. The blending is the same either way -- it is
    `_MAIN` doing it in both -- so the edge of a selection behaves the
    same for a blur as for a fill.
    """
    source = PixelSource.from_image(image)
    try:
        original = source.texture
        current = original
        framebuffer = None
        # One pass carries the mask itself; several compose at the end.
        inline_mask = mask if len(passes) == 1 else None
        for spec, params in passes:
            step = PixelSource.from_texture(current, storage=source.storage)
            try:
                framebuffer, current = run_pass(
                    spec, step, mask=inline_mask,
                    second=original if spec.reads_second else None, params=params)
            finally:
                step.release()
        if mask is not None and inline_mask is None:
            step = PixelSource.from_texture(original, storage=source.storage)
            try:
                framebuffer, current = run_pass(_COMPOSE, step, mask=mask, second=current)
            finally:
                step.release()
        if framebuffer is None:
            # No passes at all, so nothing was written and there is
            # nothing for an undo step to take back.
            return True
        values = read_color(framebuffer, source.width, source.height)
        return output.commit(values)
    finally:
        source.release()


def release() -> None:
    """Give the cached shaders and the placeholder back to the GPU context."""
    global _placeholder
    _shaders.clear()
    _placeholder = None
