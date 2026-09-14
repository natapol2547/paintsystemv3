# PS-051 Gaussian blur and sharpen on the GPU

Epic F. Size M. Milestone M3.

## v2 behaviour

`gaussian_blur` / `sharpen_image` (`operators/image_operators.py:178,
208`): alpha-premultiplied blur (`basic_filters.py:33`), sharpen as an
unsharp mask against a sigma 1.0 blur with the original alpha restored.
Parameters: radius/sigma, strength.

## v3 design

- Blur: separable two-pass fragment shader (horizontal, vertical),
  kernel weights computed on the CPU for the given sigma and passed as a
  uniform array (cap at 64 taps, downsample for larger radii). Optional
  "wrap" edge mode for tiling textures.
- Sharpen: blur pass at sigma 1.0 into the second target, then a combine
  pass `orig + strength * (orig - blurred)` on colour only, alpha copied.
- Both registered in PS-050 with the v2 property names so the operator
  dialogs look the same.

## Acceptance

- Blur of a single white pixel at sigma 2 matches a numpy reference within
  1/255.
- 8K image blurs in under 2 seconds on the user's machine.
