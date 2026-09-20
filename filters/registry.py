# SPDX-License-Identifier: GPL-3.0-or-later
"""The filters PS-052 runs, as `FilterSpec` values (PS-050).

Each one defines ``vec4 apply(ivec2 texel, vec4 c)`` over straight colour
in the image's storage space; `core` handles storage, the mask and the
write back. A later blur or sharpen (PS-051) adds its spec here.
"""
from .core import FilterSpec

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

FILTERS = {spec.name: spec for spec in (CLEAR, FILL, INVERT)}
