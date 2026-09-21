# SPDX-License-Identifier: GPL-3.0-or-later
"""An 8-bit RGBA PNG written a band of rows at a time.

What `filters.layer_build` packs into a filter layer's image. Blender's
own `Image.pack` encodes the whole image in one call -- about 0.9 s at
4096 x 4096 -- and nothing can run while it does, which made the commit
the longest freeze of a build by far. Encoding here lets each band go
into the stream as it comes off the GPU, in the same unit as its
readback, so the commit only hands over bytes that are already done.

The encoding is chosen for speed over size: every row is Up-filtered,
which is one numpy subtraction per band, and zlib runs at level 1 with
its run-length strategy. On a painted result that comes out smaller
than Blender's own file; on a blur, all long smooth gradients, somewhat
larger. Either way it is a plain PNG that any reader, Blender's included,
decodes.
"""
from __future__ import annotations

import struct
import zlib

import numpy as np

SIGNATURE = b"\x89PNG\r\n\x1a\n"
# The PNG filter type that stores each byte minus the one above it.
UP = 2


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


class RGBAStream:
    """A PNG of *width* x *height* RGBA bytes, fed a band at a time from the top."""

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self._compressor = zlib.compressobj(1, zlib.DEFLATED, 15, 8, zlib.Z_RLE)
        self._pieces: list[bytes] = []
        # The last row of the previous band, which the first row of the
        # next one is filtered against.
        self._above: np.ndarray | None = None
        self._rows = 0

    def add(self, band: np.ndarray) -> None:
        """Append *band*, uint8 ``(rows, width, 4)`` with its top row first."""
        rows = np.ascontiguousarray(band, dtype=np.uint8).reshape(band.shape[0], -1)
        if rows.shape[1] != self.width * 4:
            raise ValueError(f"A band of {rows.shape[1] // 4} texels does not fit "
                             f"a PNG {self.width} wide")
        filtered = np.empty((rows.shape[0], rows.shape[1] + 1), dtype=np.uint8)
        filtered[:, 0] = UP
        # uint8 arithmetic wraps, which is the modulo the filter is defined
        # with. The first row of the image has zeros above it.
        if self._above is None:
            filtered[0, 1:] = rows[0]
        else:
            np.subtract(rows[0], self._above, out=filtered[0, 1:])
        np.subtract(rows[1:], rows[:-1], out=filtered[1:, 1:])
        self._above = rows[-1].copy()
        self._rows += rows.shape[0]
        self._pieces.append(self._compressor.compress(filtered))

    def finish(self) -> bytes:
        """The whole file. Every row has to have been added."""
        if self._rows != self.height:
            raise ValueError(f"A PNG {self.height} rows high was given {self._rows}")
        self._pieces.append(self._compressor.flush())
        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 6, 0, 0, 0)
        return (SIGNATURE + _chunk(b"IHDR", header)
                + _chunk(b"IDAT", b"".join(self._pieces)) + _chunk(b"IEND", b""))
