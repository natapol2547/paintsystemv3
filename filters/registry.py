# SPDX-License-Identifier: GPL-3.0-or-later
"""The GPU filters, as `FilterSpec` values (PS-050, PS-052).

Each one defines ``vec4 apply(ivec2 texel, vec4 c)`` over straight colour
in the colour space the image stores. `core` handles alpha storage, the
mask and the write back. A spec that needs a second texture besides the
one it draws over, such as the unsharp mask, sets ``reads_second``, and
the caller binds that texture.

`ENCODE_SRGB` and `DECODE_SRGB` are here too, though no user picks them.
The filter layer build and the painter need the encode, and the Blur and
Sharpen actions need both. They cannot live in `layer_build`, because
the painter would then import `layer_build`, which already imports the
painter through `layer_specs`.
"""
from math import ceil

from .core import FilterSpec

# Taps on each side of the blur kernel's centre. A gaussian falls below
# half a byte step past about three sigma, so this covers sigma 21
# without a visible cut-off edge.
BLUR_TAPS = 63
BLUR_MAX_SIGMA = BLUR_TAPS / 3.0

# Blurring twice with sigma s gives a blur with sigma s*sqrt(2). So a
# sigma wider than one kernel is reached by running the pair of passes
# again, not by widening the kernel.
#
# The limit is where more width stops being worth the cost. Each
# iteration is two full passes at 63 taps. On the test machine a 2048
# build takes about 0.5 s at one iteration and 1.1 s at four. At sixteen
# it takes 3.4 s, and 13 s at 4096, far too slow for a slider. A wider
# blur should spread the taps over a downsampled copy instead of adding
# passes at full size. PS-051 records that follow-up.
BLUR_MAX_ITERATIONS = 4
BLUR_MAX_EFFECTIVE_SIGMA = BLUR_MAX_SIGMA * BLUR_MAX_ITERATIONS ** 0.5

CLEAR = FilterSpec(
    name="clear",
    apply_source="""
vec4 apply(ivec2 texel, vec4 c)
{
  return vec4(0.0);
}
""",
)

FILL = FilterSpec(
    name="fill",
    apply_source="""
vec4 apply(ivec2 texel, vec4 c)
{
  return vec4(color.rgb, lock_alpha != 0 ? c.a : color.a);
}
""",
    params=(('VEC4', "color"), ('INT', "lock_alpha")),
)

INVERT = FilterSpec(
    name="invert",
    apply_source="""
/* With `encode` set, the colour is scene linear, as in a float layer.
   Inverting that directly turns a mid grey almost white, so the
   inversion runs on the sRGB encoding instead. A float layer and a byte
   layer of the same picture then look the same after Invert. Values
   outside 0 to 1 have no encoding and are clamped. */
vec4 apply(ivec2 texel, vec4 c)
{
  vec3 rgb = encode != 0 ? ps_to_srgb(clamp(c.rgb, 0.0, 1.0)) : c.rgb;
  rgb = mix(rgb, vec3(1.0) - rgb, channels.rgb);
  if (encode != 0) {
    rgb = ps_to_linear(rgb);
  }
  return vec4(rgb, channels.a > 0.5 ? 1.0 - c.a : c.a);
}
""",
    params=(('VEC4', "channels"), ('INT', "encode")),
)

BLUR = FilterSpec(
    name="blur",
    apply_source="""
const int PS_BLUR_TAPS = 63;

/* One axis of a separable gaussian. The caller runs this twice, with
   `direction` (1, 0) and then (0, 1).

   The weights are computed here, not uploaded. A 63-tap kernel would
   need 256 bytes of push constants, well past what a Vulkan driver has
   to offer, and an `exp` costs far less than the texture fetch next to
   it.

   The sum is premultiplied, so a transparent texel adds no colour and
   an edge does not bleed towards black. Sampling is clamped to the
   edge, because a filter layer's derived image is a UV layout, not a
   tiling pattern. Wrapping would fold the far side of the map into the
   near side. */
vec4 apply(ivec2 texel, vec4 c)
{
  if (radius <= 0) {
    return c;
  }
  ivec2 offset = ivec2(direction);
  ivec2 last = ivec2(target_size) - ivec2(1);
  vec4 sum = vec4(c.rgb * c.a, c.a);
  float total = 1.0;
  for (int i = 1; i <= PS_BLUR_TAPS; i++) {
    if (i > radius) {
      break;
    }
    float weight = exp(-0.5 * float(i * i) / (sigma * sigma));
    vec4 lo = stored_to_straight(texelFetch(source, clamp(texel - offset * i, ivec2(0), last), 0));
    vec4 hi = stored_to_straight(texelFetch(source, clamp(texel + offset * i, ivec2(0), last), 0));
    sum += weight * (vec4(lo.rgb * lo.a, lo.a) + vec4(hi.rgb * hi.a, hi.a));
    total += 2.0 * weight;
  }
  sum /= total;
  return sum.a > 0.0 ? vec4(sum.rgb / sum.a, sum.a) : vec4(0.0);
}
""",
    params=(('VEC2', "direction"), ('FLOAT', "sigma"), ('INT', "radius")),
)

SHARPEN = FilterSpec(
    name="sharpen",
    apply_source="""
/* The second half of an unsharp mask. `source` is the blur and `second`
   is the original picture. Their difference is the detail the blur
   removed, and adding `strength` times that detail back sharpens.

   With `encode` set, the difference is taken on the sRGB encoding, like
   `invert`. On scene-linear values a high pass reacts far more to a
   highlight than to a shadow of the same visible contrast, so `strength`
   would mean different things in bright and dark areas. The blur itself
   ran in linear, so this is the encoded original minus the encoding of
   a linear blur. That is still zero wherever the picture is flat, which
   is what matters.

   Alpha is kept from the original. Sharpening alpha would carve a halo
   into the silhouette instead of into the detail. */
vec4 apply(ivec2 texel, vec4 c)
{
  vec4 original = stored_to_straight(texelFetch(second, texel, 0));
  vec3 blurred = encode != 0 ? ps_to_srgb(clamp(c.rgb, 0.0, 1.0)) : c.rgb;
  vec3 sharp = encode != 0 ? ps_to_srgb(clamp(original.rgb, 0.0, 1.0)) : original.rgb;
  sharp = clamp(sharp + strength * (sharp - blurred), 0.0, 1.0);
  return vec4(encode != 0 ? ps_to_linear(sharp) : sharp, original.a);
}
""",
    params=(('FLOAT', "strength"), ('INT', "encode")),
    reads_second=True,
)

ENCODE_SRGB = FilterSpec(
    name="encode_srgb",
    apply_source="""
/* Scene linear in, sRGB out. A filter layer's derived image stores sRGB.
   The painter also works on sRGB so that its look matches v2, which
   painted stored byte values. Values outside 0 to 1 have no sRGB
   encoding and are clamped. Eight bits could not store them anyway. */
vec4 apply(ivec2 texel, vec4 c)
{
  return vec4(ps_to_srgb(clamp(c.rgb, 0.0, 1.0)), clamp(c.a, 0.0, 1.0));
}
""",
)

DECODE_SRGB = FilterSpec(
    name="decode_srgb",
    apply_source="""
/* sRGB in, scene linear out, the reverse of `encode_srgb`. The Blur and
   Sharpen actions run a byte layer's blur between the two, so it blurs
   in linear light like float layers and filter layers do. */
vec4 apply(ivec2 texel, vec4 c)
{
  return vec4(ps_to_linear(c.rgb), c.a);
}
""",
)


def blur_passes(sigma: float) -> list[dict]:
    """Push constants for the passes a gaussian blur of *sigma* texels needs.

    Two passes per iteration, one per axis. A sigma wider than one kernel
    is split across several iterations instead of using more taps,
    because blurring n times with sigma s gives sigma ``s * sqrt(n)``.
    Repeating a truncated kernel adds up its truncation error. At three
    sigma that error starts below half a byte step, and sixteen of them
    stay under one step.

    Empty when *sigma* is zero or less, so a blur set to zero costs
    nothing instead of running an identity kernel.
    """
    sigma = min(float(sigma), BLUR_MAX_EFFECTIVE_SIGMA)
    if sigma <= 0.0:
        return []
    iterations = min(BLUR_MAX_ITERATIONS, max(1, ceil((sigma / BLUR_MAX_SIGMA) ** 2)))
    each = sigma / iterations ** 0.5
    radius = min(BLUR_TAPS, max(1, round(3.0 * each)))
    axes = ((1.0, 0.0), (0.0, 1.0))
    return [{"direction": axis, "sigma": each, "radius": radius}
            for _ in range(iterations) for axis in axes]
