"""Bounded PNG decode for non-interlaced 8-bit gray/RGB render output."""

import struct
import zlib


def validate_png(
    image: bytes, *, max_bytes: int, max_width: int, max_height: int, max_pixels: int
) -> None:
    if len(image) > max_bytes or not image.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Unsupported image")
    offset, channels, width, height = 8, 0, 0, 0
    compressed = bytearray()
    ended = seen_data = seen_density = False
    while offset < len(image):
        if len(image) - offset < 12:
            raise ValueError("Truncated PNG chunk")
        length = struct.unpack_from(">I", image, offset)[0]
        end = offset + length + 12
        if end > len(image):
            raise ValueError("Truncated PNG payload")
        kind = image[offset + 4 : offset + 8]
        data = image[offset + 8 : end - 4]
        checksum = struct.unpack_from(">I", image, end - 4)[0]
        if zlib.crc32(kind + data) != checksum:
            raise ValueError("PNG checksum mismatch")
        if offset == 8:
            if kind != b"IHDR" or length != 13:
                raise ValueError("Invalid PNG header")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", data
            )
            channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color, 0)
            if (
                not 0 < width <= max_width
                or not 0 < height <= max_height
                or width * height > max_pixels
                or depth != 8
                or not channels
                or compression
                or filtering
                or interlace
            ):
                raise ValueError("Unsupported PNG dimensions or encoding")
        elif kind == b"IDAT":
            seen_data = True
            compressed.extend(data)
        elif kind == b"IEND":
            if length or end != len(image) or not compressed:
                raise ValueError("Invalid PNG end")
            ended = True
        elif kind == b"pHYs":
            if seen_density or seen_data or length != 9 or data[-1] not in {0, 1}:
                raise ValueError("Invalid PNG density chunk")
            seen_density = True
        else:
            # The trusted renderer needs no text, EXIF, palettes or other metadata.
            raise ValueError("Unsupported PNG chunk")
        offset = end
    if not ended:
        raise ValueError("Missing PNG end")
    # Limit inflated output before allocating it; never use unbounded zlib.decompress.
    stride = width * channels + 1
    expected = height * stride
    decoder = zlib.decompressobj()
    decoded = decoder.decompress(compressed, expected + 1)
    if (
        len(decoded) != expected
        or not decoder.eof
        or decoder.unused_data
        or decoder.unconsumed_tail
    ):
        raise ValueError("Invalid PNG decoded length")
    # In these color modes every sample byte is valid; row filters must be 0..4.
    # Validate the full decompressed raster rather than accepting an IHDR signature.
    if any(decoded[row * stride] > 4 for row in range(height)):
        raise ValueError("Invalid PNG row filter")
