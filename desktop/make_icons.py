#!/usr/bin/env python3
"""Draw the app's icon and write it in every form the desktop app needs.

    python3 desktop/make_icons.py

Writes into desktop/src-tauri/icons/: 32x32.png, 128x128.png, 128x128@2x.png, icon.png, icon.icns (macOS)
and icon.ico (Windows). The files are committed; this script is how they were made and how to change them.

It also writes icon.ico once more as studio/web/favicon.ico. A browser asks the app for that file by itself,
and shows it in the tab; without it every start of the app in a browser ended in a "not found". The two are
the same bytes, and tests/test_pages_open.py holds them to that.

The icon is drawn here, by rule: a rounded square in the app's accent colour with five white bars of a sound
wave. No picture comes from anywhere else. Standard library only: the PNG, ICO and ICNS files are written
by hand, which is a few lines each.
"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path
from typing import List, Optional, Tuple

OUT = Path(__file__).resolve().parent / "src-tauri" / "icons"
IN_A_BROWSER = Path(__file__).resolve().parent.parent / "studio" / "web" / "favicon.ico"
ACCENT = (224, 82, 63)                  # --accent in studio/web/app.css
WHITE = (255, 255, 255)
SUB = 4                                 # rows a pixel row is cut into; across a row the edge is worked out exactly
BARS = (0.26, 0.48, 0.68, 0.40, 0.22)   # the heights of the five bars, as parts of the square's side

Pixel = Tuple[int, int, int, int]


def span(y: float, left: float, top: float, right: float, bottom: float, radius: float) -> Optional[Tuple[float, float]]:
    """Where a rounded rectangle begins and ends at height ``y``; None above and below it."""
    if y < top or y > bottom:
        return None
    inset = 0.0
    if y < top + radius:
        inset = radius - math.sqrt(max(0.0, radius * radius - (top + radius - y) ** 2))
    elif y > bottom - radius:
        inset = radius - math.sqrt(max(0.0, radius * radius - (y - (bottom - radius)) ** 2))
    return left + inset, right - inset


def cover(size: int, shapes: List[Tuple[float, float, float, float, float]]) -> List[List[float]]:
    """For every pixel, how much of it the shapes cover (0 to 1). The shapes must not overlap each other."""
    rows = [[0.0] * size for _ in range(size)]
    part = 1.0 / SUB
    for y in range(size):
        row = rows[y]
        for step in range(SUB):
            at = y + (step + 0.5) * part
            for shape in shapes:
                found = span(at, *shape)
                if found is None:
                    continue
                a, b = max(0.0, found[0]), min(float(size), found[1])
                if b <= a:
                    continue
                first, last = int(a), min(int(b), size - 1)
                if first == last:
                    row[first] += (b - a) * part
                    continue
                row[first] += (first + 1 - a) * part
                for x in range(first + 1, last):
                    row[x] += part
                row[last] += (b - last) * part
    return rows


def draw(size: int) -> List[List[Pixel]]:
    """The icon at ``size`` pixels, as rows of (red, green, blue, alpha)."""
    margin = size * 0.098                                    # the room macOS icons leave around their shape
    side = size - 2 * margin
    body = cover(size, [(margin, margin, size - margin, size - margin, side * 0.225)])
    width, gap = side * 0.085, side * 0.075
    start = size / 2 - (len(BARS) * width + (len(BARS) - 1) * gap) / 2
    bars = cover(size, [(start + n * (width + gap), size / 2 - side * tall / 2, start + n * (width + gap) + width,
                         size / 2 + side * tall / 2, width / 2) for n, tall in enumerate(BARS)])
    image = []
    for y in range(size):
        line = []
        for x in range(size):
            alpha, white = min(1.0, body[y][x]), min(1.0, bars[y][x])
            colour = tuple(int(round(ACCENT[c] * (1 - white) + WHITE[c] * white)) for c in range(3))
            line.append((colour[0], colour[1], colour[2], int(round(alpha * 255))))
        image.append(line)
    return image


def shrink(image: List[List[Pixel]], factor: int) -> List[List[Pixel]]:
    """The image at 1/``factor`` of its size: each new pixel is the average of a block, weighted by how solid each part is."""
    size = len(image) // factor
    out = []
    for y in range(size):
        line = []
        for x in range(size):
            red = green = blue = solid = 0
            for row in image[y * factor:(y + 1) * factor]:
                for r, g, b, a in row[x * factor:(x + 1) * factor]:
                    red += r * a
                    green += g * a
                    blue += b * a
                    solid += a
            if solid:
                line.append((int(round(red / solid)), int(round(green / solid)), int(round(blue / solid)), int(round(solid / (factor * factor)))))
            else:
                line.append((0, 0, 0, 0))
        out.append(line)
    return out


def png(image: List[List[Pixel]]) -> bytes:
    size = len(image)
    raw = b"".join(b"\x00" + bytes(value for pixel in row for value in pixel) for row in image)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def bitmap(image: List[List[Pixel]]) -> bytes:
    """An image as an .ico file holds its small sizes: a bitmap, bottom row first, then a mask of the same shape."""
    size = len(image)
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, size * size * 4, 0, 0, 0, 0)
    pixels = b"".join(bytes((b, g, r, a)) for row in reversed(image) for r, g, b, a in row)
    mask = bytes(((size + 31) // 32) * 4) * size             # all zero: the alpha in the pixels says what shows
    return header + pixels + mask


def ico(images: List[List[List[Pixel]]]) -> bytes:
    """A Windows icon: the small sizes as bitmaps and 256 pixels as a PNG, which is what Windows expects."""
    parts = [png(image) if len(image) >= 256 else bitmap(image) for image in images]
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for image, data in zip(images, parts):
        size = len(image)
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return out + b"".join(parts)


def icns(by_size: dict) -> bytes:
    """A macOS icon: PNGs under the names macOS gives each size."""
    kinds = ((b"icp4", 16), (b"icp5", 32), (b"icp6", 48), (b"ic07", 128), (b"ic08", 256), (b"ic09", 512), (b"ic10", 1024),
             (b"ic11", 32), (b"ic12", 64), (b"ic13", 256), (b"ic14", 512))
    body = b"".join(kind + struct.pack(">I", 8 + len(png(by_size[size]))) + png(by_size[size]) for kind, size in kinds)
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


def main() -> int:
    large = draw(1024)
    other = draw(768)                                        # 48 and 24 pixels do not divide 1024
    by_size = {1024: large}
    for size in (512, 256, 128, 64, 32, 16):
        by_size[size] = shrink(large, 1024 // size)
    for size in (48, 24):
        by_size[size] = shrink(other, 768 // size)
    OUT.mkdir(parents=True, exist_ok=True)
    files = {"32x32.png": png(by_size[32]), "128x128.png": png(by_size[128]), "128x128@2x.png": png(by_size[256]),
             "icon.png": png(by_size[512]), "icon.icns": icns(by_size),
             "icon.ico": ico([by_size[size] for size in (16, 24, 32, 48, 64, 256)])}
    for name, data in files.items():
        (OUT / name).write_bytes(data)
        print("%-16s %7d bytes" % (name, len(data)))
    IN_A_BROWSER.write_bytes(files["icon.ico"])              # the same icon, for the tab of a browser
    print("%-16s %7d bytes (studio/web/)" % (IN_A_BROWSER.name, len(files["icon.ico"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
