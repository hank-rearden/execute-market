"""Generate dependency-free PNG icons for the EXECUTE Market PWA."""
from pathlib import Path
import struct
import zlib

out = Path("_site/icons")
out.mkdir(parents=True, exist_ok=True)

def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)

def generate(size):
    rows = []
    for y in range(size):
        line = bytearray([0])
        for x in range(size):
            # Deep navy background with three ascending emerald market bars.
            color = (11, 22, 38)
            bars = [(0.25, 0.63, 0.37, 0.76), (0.42, 0.49, 0.54, 0.76), (0.59, 0.30, 0.71, 0.76)]
            fx, fy = x / size, y / size
            if any(a <= fx <= b and c <= fy <= d for a, c, b, d in bars):
                color = (61, 224, 165)
            line.extend(color)
        rows.append(line)
    raw = b"".join(rows)
    png = (b"\x89PNG\r\n\x1a\n" +
           chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)) +
           chunk(b"IDAT", zlib.compress(raw, 9)) +
           chunk(b"IEND", b""))
    (out / f"icon-{size}.png").write_bytes(png)

for dimension in (192, 512):
    generate(dimension)
