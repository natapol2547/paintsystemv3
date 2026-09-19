"""The GPU filter core reads and writes an image's own values (PS-050).

What these check is exactness: a filter that changes nothing must change
no byte, an inversion of a byte image must be exactly 255 - k, and a
texel the mask leaves at zero must come back bit-identical. The soft-mask
mix and the premultiplied storage rule are compared against the same
formulas in numpy.

These need a GPU context. Blender 5.2 added `gpu.init()`, which builds
one in background mode, so they run in the ordinary headless job there.
Background 4.2 to 5.1 have no way to get a context and skip; that
coverage comes from the windowed job.
"""
import os
import sys

import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, finish, guarded, import_from, register_addon, section, skip  # noqa: E402

register_addon()
gpu_core = import_from("gpu_passes.core")
core = import_from("filters.core")
registry = import_from("filters.registry")
raster = import_from("selection.raster")

IDENTITY = core.FilterSpec(name="test_identity", apply_source="""
vec4 apply(ivec2 texel, vec4 c)
{
  return c;
}
""")


def available():
    if gpu_core.gpu_available():
        return True
    skip("no GPU context in this session; gpu.init() arrived in Blender 5.2")
    return False


def new_image(name, width, height, *, float_buffer=False, values=None):
    """A fresh image of *values*, or of repeatable random pixels."""
    existing = bpy.data.images.get(name)
    if existing is not None:
        bpy.data.images.remove(existing)
    image = bpy.data.images.new(name, width, height, alpha=True, float_buffer=float_buffer)
    if values is None:
        rng = np.random.default_rng(7)
        values = rng.integers(0, 256, size=width * height * 4).astype(np.float32) / 255.0
    image.pixels.foreach_set(np.asarray(values, dtype=np.float32).ravel())
    image.update()
    return image


def read(image):
    values = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(values)
    return values


def run(spec, image, **kwargs):
    """Run *spec* over *image* into a plain array, without touching it."""
    source = core.PixelSource.from_image(image)
    try:
        framebuffer, target = core.run_pass(spec, source, **kwargs)
        values = gpu_core.read_color(framebuffer, source.width, source.height).ravel()
        # `target` stays referenced until the read is done: a framebuffer
        # does not keep its colour slot alive.
        del target
        return values
    finally:
        source.release()


def as_bytes(values):
    return np.rint(np.asarray(values, dtype=np.float64) * 255.0).astype(np.int32)


def mask_texture(values):
    """An R32F texture holding *values*, shaped (height, width)."""
    import gpu
    flat = np.ascontiguousarray(values, dtype=np.float32).ravel()
    height, width = values.shape
    return gpu.types.GPUTexture((width, height), format='R32F',
                                data=gpu.types.Buffer('FLOAT', flat.size, flat))


def test_identity_is_exact():
    section("a filter that changes nothing changes no value")
    if not available():
        return
    image = new_image("PS Filter Byte", 64, 48)
    before, after = as_bytes(read(image)), as_bytes(run(IDENTITY, image))
    check(np.array_equal(before, after),
          f"a byte image round trips through RGBA16F to the same bytes "
          f"({int(np.count_nonzero(before != after))} of {before.size} differ)")

    # Premultiplied values, which is what a float image stores: colour is
    # scaled by its own alpha, and alpha stays above zero, where colour
    # has no straight form to carry through the pass.
    rng = np.random.default_rng(11)
    straight = rng.random((64 * 48, 4)).astype(np.float32) * 4.0 - 1.0
    straight[:, 3] = rng.random(64 * 48).astype(np.float32) * 0.9 + 0.1
    values = np.concatenate([straight[:, :3] * straight[:, 3:], straight[:, 3:]], axis=1)
    float_image = new_image("PS Filter Float", 64, 48, float_buffer=True, values=values)
    before = read(float_image)
    after = run(IDENTITY, float_image)
    worst = float(np.abs(before - after).max())
    check(worst <= 1e-6, f"a float image round trips through RGBA32F within {worst:.2e}")


def test_invert_is_exact():
    section("invert of a byte image is exactly 255 - k")
    if not available():
        return
    image = new_image("PS Filter Byte", 64, 48)
    before = as_bytes(read(image))
    after = as_bytes(run(registry.INVERT, image,
                         params={"channels": (1.0, 1.0, 1.0, 0.0), "encode": 0}))
    rgb = np.arange(before.size) % 4 != 3
    check(np.array_equal(after[rgb], 255 - before[rgb]),
          "every colour byte is inverted exactly")
    check(np.array_equal(after[~rgb], before[~rgb]), "alpha is untouched")


def test_bands_have_no_seams():
    section("an image taller than one band")
    if not available():
        return
    # 1100 rows draw as 512 + 512 + 76.
    image = new_image("PS Filter Tall", 8, 1100)
    before, after = as_bytes(read(image)), as_bytes(run(IDENTITY, image))
    check(np.array_equal(before, after),
          f"three bands leave no seam ({int(np.count_nonzero(before != after))} bytes differ)")


def test_mask_limits_the_pass():
    section("the mask limits what changes")
    if not available():
        return
    width, height = 32, 24
    image = new_image("PS Filter Masked", width, height)
    before = read(image).reshape(height, width, 4)
    coverage = np.zeros((height, width), dtype=np.float32)
    coverage[6:18, 8:24] = 1.0
    coverage[5, 8:24] = 0.4              # a feathered row
    coverage[4, 8:24] = 0.4 / 255.0      # below half a byte step, so no coverage
    after = run(registry.CLEAR, image, mask=mask_texture(coverage)).reshape(height, width, 4)

    outside = coverage <= 0.0
    check(np.array_equal(as_bytes(after[outside]), as_bytes(before[outside])),
          "texels the mask leaves out come back unchanged")
    check(np.array_equal(as_bytes(after[4]), as_bytes(before[4])),
          "a texel under half a byte step of coverage counts as uncovered")
    full = coverage >= 1.0
    check(not after[full].any(), "Clear under full coverage leaves zeroes")

    # The feathered row, by the same formula in float64: premultiplied mix
    # towards transparent black lowers alpha and keeps colour.
    row_before = before[5].astype(np.float64)
    m = np.floor(0.4 * 255.0 + 0.5) / 255.0
    alpha = row_before[:, 3] * (1.0 - m)
    colour = np.where(alpha[:, None] > 0.0,
                      row_before[:, :3] * row_before[:, 3:] * (1.0 - m) / np.maximum(alpha, 1e-12)[:, None],
                      row_before[:, :3] * (1.0 - m))
    expected = np.concatenate([colour, alpha[:, None]], axis=1)
    covered = slice(8, 24)
    worst = float(np.abs(as_bytes(after[5][covered]) - as_bytes(expected[covered])).max())
    check(worst <= 1.0, f"a feathered Clear matches the model within {worst:.0f} byte")


def test_mask_matches_the_stencil():
    section("the mask is read as the brush stencil reads it")
    if not available():
        return
    width, height = 16, 16
    steps = (np.arange(width * height, dtype=np.float32) % 255) / 255.0
    coverage = steps.reshape(height, width)
    image = new_image("PS Filter Steps", width, height,
                      values=np.ones(width * height * 4, dtype=np.float32))
    after = run(registry.CLEAR, image, mask=mask_texture(coverage)).reshape(height, width, 4)
    quantised = np.floor(coverage.astype(np.float32) * 255.0 + 0.5) / 255.0
    worst = float(np.abs(as_bytes(after[..., 3]) - as_bytes(1.0 - quantised)).max())
    check(worst <= 1.0,
          f"alpha follows the 8-bit quantised mask within {worst:.0f} byte, as read_bytes rounds it")


def test_float_storage_is_premultiplied():
    section("a float image is read as premultiplied")
    if not available():
        return
    width, height = 8, 8
    # Stored premultiplied: half-transparent pure red is (0.5, 0, 0, 0.5).
    values = np.tile(np.array([0.5, 0.0, 0.0, 0.5], dtype=np.float32), width * height)
    image = new_image("PS Filter Premul", width, height, float_buffer=True, values=values)
    coverage = np.full((height, width), 0.5, dtype=np.float32)
    after = run(registry.CLEAR, image, mask=mask_texture(coverage)).reshape(-1, 4)
    m = np.floor(0.5 * 255.0 + 0.5) / 255.0
    alpha = 0.5 * (1.0 - m)
    check(np.allclose(after[:, 3], alpha, atol=1e-6)
          and np.allclose(after[:, 0], alpha, atol=1e-6),
          f"colour and alpha fall together, staying premultiplied ({after[0]})")


def test_result_image_leaves_the_source():
    section("a result image takes the output instead of the layer")
    if not available():
        return
    source = new_image("PS Filter Source", 16, 16)
    result = new_image("PS Filter Result", 16, 16,
                       values=np.zeros(16 * 16 * 4, dtype=np.float32))
    before = read(source)
    core.apply_filter(registry.INVERT, source, core.ResultImage(result),
                      params={"channels": (1.0, 1.0, 1.0, 0.0), "encode": 0})
    check(np.array_equal(read(source), before), "the source image is untouched")
    expected = as_bytes(before).reshape(-1, 4)
    got = as_bytes(read(result)).reshape(-1, 4)
    check(np.array_equal(got[:, :3], 255 - expected[:, :3]), "the result image holds the inversion")


def test_release():
    section("the shaders are given back")
    if not available():
        return
    run(IDENTITY, new_image("PS Filter Byte", 8, 8))
    check(len(core._shaders) > 0, f"the shader cache holds {len(core._shaders)}")
    core.release()
    check(not core._shaders and core._placeholder is None, "release empties the cache")


for test in (test_identity_is_exact,
             test_invert_is_exact,
             test_bands_have_no_seams,
             test_mask_limits_the_pass,
             test_mask_matches_the_stencil,
             test_float_storage_is_premultiplied,
             test_result_image_leaves_the_source,
             test_release):
    guarded(test)

# Give the GPU objects back while the context is still up; Python frees
# them at shutdown otherwise, which segfaults a background Blender.
core.release()
raster.release()

finish("FILTERS GPU TEST")
