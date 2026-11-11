#!/usr/bin/env python3
"""Generate Android launcher PNG densities from the user's cat silhouette.

The source silhouette is preserved as a black shape, on white for reliable
contrast across light and dark launchers. The Android launcher applies its
own shape mask. No external icon downloads required.
"""
from pathlib import Path
import cairosvg

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).with_name("kencat-icon.svg")
RES = ROOT / "src/crates/concat-android/res"
SIZES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}

def main():
    src = SOURCE.read_bytes()
    for density, size in SIZES.items():
        out = RES / f"mipmap-{density}" / "ic_launcher.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        cairosvg.svg2png(bytestring=src, write_to=str(out), output_width=size, output_height=size)
        assert out.stat().st_size > 100, f"Empty generated icon: {out}"
        print(f"{out}: {size}x{size}, {out.stat().st_size} bytes")

if __name__ == "__main__":
    main()
