"""Generate updater/icon.ico from updater/icon-64.png.

    python tools/build_icon.py

The master is 64x64 pixel art and every entry is scaled from it with NEAREST, so no
edge is ever smoothed. That is the whole point: the icon this replaced had been scaled
smoothly somewhere upstream, which left a one-to-two pixel gradient on every block edge
and turned the 16 and 32 -- the sizes Explorer and the taskbar actually show -- into a
blur.

Entries are stored as uncompressed BMP, the format an .ico has always used, and not as
PNG. PNG entries are legal since Vista and would cut this file from 22 KB to 13 KB, but
they are not worth it: shipping them shrank the icon resource enough to move the app
across Microsoft Defender's ML boundary, and GreenCraft.exe started coming back as
Trojan:Win32/Bearfoos.A!ml on a machine where the identical Python was clean. Same art,
BMP entries, clean. See PLAN.md.

Pillow's own ICO writer is not used because it resamples with LANCZOS, which would
smooth exactly the edges this is trying to keep hard.

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

    def dib(im):
        """An icon's BMP entry: BITMAPINFOHEADER, then bottom-up BGRA, then an AND mask.

        biHeight is doubled because the header describes colour and mask together. The
        mask is left all zero; 32-bit entries are composited from the alpha channel and
        Windows ignores it, but it still has to be present and 4-byte aligned per row.
        """
        n = im.size[0]
        header = struct.pack("<IiiHHIIiiII", 40, n, n * 2, 1, 32, 0, 0, 0, 0, 0, 0)
        px = im.load()
        rows = []
        for y in range(n - 1, -1, -1):
            row = bytearray()
            for x in range(n):
                r, g, b, a = px[x, y]
                row += bytes((b, g, r, a))
            rows.append(bytes(row))
        mask_stride = ((n + 31) // 32) * 4
        return header + b"".join(rows) + b"\0" * (mask_stride * n)

    blobs = []
    for sz in SIZES:
        im = art if sz == 64 else art.resize((sz, sz), Image.NEAREST)
        blobs.append((sz, dib(im)))

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
