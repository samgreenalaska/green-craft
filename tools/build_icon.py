"""Generate updater/icon.ico from updater/icon-64.png.

    python tools/build_icon.py

The master is 64x64 pixel art and every entry is scaled from it with NEAREST, so no
edge is ever smoothed. That is the whole point: the icon this replaced had been scaled
smoothly somewhere upstream, which left a one-to-two pixel gradient on every block edge
and turned the 16 and 32 -- the sizes Explorer and the taskbar actually show -- into a
blur.

Entries are stored as PNG rather than as the uncompressed bitmaps an .ico usually
holds. Windows has read PNG-compressed icons since Vista, and it is the difference
between 241 KB and something under 40 KB, because hard-edged art is nearly all flat
runs. Uncompressed BMP entries cost the same whether the art is crisp or blurry.

64 is the largest entry, deliberately. Windows scales up from it for the extra-large
views and high-DPI desktops, and that scaling is smooth -- but the art is 64x64, so
there is nothing above 64 to represent faithfully anyway, and shipping upscaled copies
would only trade file size for a sharpness the master does not have.

Only exact integer factors of 64 are shipped. 24 and 48 were tried and dropped: at
x0.375 and x0.75 nearest scaling makes some art pixels one screen pixel wide and others
two, which at 24 visibly mangles the cat's face -- Windows' own smooth downscale reads
better than a mangled one. Every entry here is either exactly crisp or absent.
"""
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MASTER = REPO / "updater" / "icon-64.png"
OUT = REPO / "updater" / "icon.ico"

SIZES = [16, 32, 64]


def main():
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is not installed. Run:  python -m pip install pillow")
        return 1

    if not MASTER.exists():
        print(f"missing master: {MASTER}")
        return 1

    art = Image.open(MASTER).convert("RGBA")
    if art.size != (64, 64):
        print(f"master must be 64x64, got {art.size[0]}x{art.size[1]}")
        return 1

    import io
    blobs = []
    for sz in SIZES:
        im = art if sz == 64 else art.resize((sz, sz), Image.NEAREST)
        b = io.BytesIO()
        im.save(b, "PNG", optimize=True)
        blobs.append((sz, b.getvalue()))

    # ICONDIR, then one ICONDIRENTRY per image, then the image data.
    offset = 6 + 16 * len(blobs)
    out = bytearray(struct.pack("<HHH", 0, 1, len(blobs)))
    for sz, blob in blobs:
        out += struct.pack("<BBBBHHII", sz, sz, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    for _, blob in blobs:
        out += blob

    before = OUT.stat().st_size if OUT.exists() else 0
    OUT.write_bytes(out)

    print(f"{OUT.name}: {len(blobs)} sizes, {len(out):,} bytes"
          + (f" (was {before:,})" if before else ""))
    for sz, blob in blobs:
        print(f"  {sz:>3}x{sz:<3} {len(blob):>7,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
