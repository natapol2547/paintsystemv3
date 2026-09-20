# SPDX-License-Identifier: GPL-3.0-or-later
"""The filters PS-052 runs, as `FilterSpec` values (PS-050).

Each one defines ``vec4 apply(ivec2 texel, vec4 c)`` over straight colour
in the image's storage space; `core` handles storage, the mask and the
write back. A spec that needs a second texture as well as the one it is
drawing over, such as the unsharp mask, sets ``reads_second`` and the
caller binds it.
"""
from math import ceil

from .core import FilterSpec

# Taps to either side of the centre of the blur kernel. A gaussian is
# below half a byte step past about three sigma, so this covers sigma 21
# without a visible truncation edge.
BLUR_TAPS = 63
BLUR_MAX_SIGMA = BLUR_TAPS / 3.0

# Blurring twice with sigma s is a blur with sigma s*sqrt(2), so a sigma
# past one kernel is reached by running the pair again rather than by
# widening it.
#
# The bound is where the cost stops being worth the width. Each
# iteration is two full passes at the cap's 63 taps, and on the probe
# machine a 2048 build costs about 0.5 s at one iteration and 1.1 s at
# four; at sixteen it is 3.4 s, and 13 s at 4096, which is not a slider
# anybody can drag. A blur wider than this one wants the taps spread
# over a downsampled copy rather than more passes at full size, which is
# the follow-up PS-051 records.
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
/* A float layer holds scene linear, and inverting that directly turns a
   mid grey almost white. The inversion runs on the sRGB encoding of the
   colour instead, so a float layer and a byte layer of the same picture
   come out looking the same. Values outside 0 to 1 have no encoding and
   are clamped into it. */
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

/* One axis of a separable gaussian: the caller runs this twice, with
   `direction` (1, 0) and then (0, 1).

   The weights are evaluated here rather than uploaded. A 63-tap kernel
   is 256 bytes of push constant, well past the block a Vulkan driver
   has to offer, and an `exp` costs far less than the texture fetch
   beside it.

   The sum is premultiplied, so a transparent texel contributes no
   colour and an edge does not bleed towards black, which is what v2's
   `_gaussian_blur_alpha_safe` was for. Sampling is clamped to the
   edge: the derived image of a filter layer is a UV layout, not a
   tiling pattern, and wrapping would fold the far side of the map into
   the near one. */
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
/* The second half of an unsharp mask: `source` is the blur, `second` is
   the picture it was made from, and the difference between them is the
   detail the blur took away. Adding `strength` of it back is the
   sharpen.

   The difference is taken on the sRGB encoding, as `invert` does and
   for the same reason: a high pass on scene-linear values responds to a
   highlight far more than to a shadow of the same visible contrast, so
   `strength` would mean something different in each half of the
   picture. The blur itself ran in linear, which leaves this the
   difference between an encoded original and the encoding of a linear
   blur -- still zero wherever the picture is flat, which is the
   property that matters.

   Alpha is the original's. Sharpening it would carve a halo out of the
   silhouette rather than out of the detail. */
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

FILTERS = {spec.name: spec for spec in (CLEAR, FILL, INVERT, BLUR, SHARPEN)}


def blur_passes(sigma: float) -> list[dict]:
    """Push constants for the passes a gaussian blur of *sigma* texels needs.

    Two per iteration, one per axis. A sigma wider than one kernel is
    split across several iterations instead of more taps, because
    blurring n times with sigma s is a blur with sigma ``s * sqrt(n)``.
    Repeating a truncated kernel compounds its truncation, but at three
    sigma that error starts below half a byte step and sixteen of them
    stay under one.

    Empty for a sigma too small to move a texel, which is how a blur set
    to zero costs nothing rather than running an identity kernel.
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
