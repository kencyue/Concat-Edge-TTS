#!/usr/bin/env python3
"""Prepare freely licensed Noto Sans TC static fonts for Concat Android CI.

Source: google/fonts/ofl/notosanstc (SIL Open Font License).
The checkout retains source/license references and generates fonts only at
build time so Git history does not carry large binary fonts.
"""
from pathlib import Path
from urllib.request import urlopen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

ROOT = Path(__file__).resolve().parents[2]
FONTS = ROOT / "src/crates/concat-text/fonts"
SOURCE = "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanstc/"
FONT_URL = SOURCE + "NotoSansTC%5Bwght%5D.ttf"
LICENSE_URL = SOURCE + "OFL.txt"


def download(url):
    with urlopen(url, timeout=120) as response:
        return response.read()


def build():
    FONTS.mkdir(parents=True, exist_ok=True)
    var_file = FONTS / "_NotoSansTC-variable.tmp.ttf"
    var_file.write_bytes(download(FONT_URL))
    (FONTS / "LICENSE-NotoSansTC.txt").write_bytes(download(LICENSE_URL))
    try:
        for weight, face in [(400, "Regular"), (600, "SemiBold")]:
            variable = TTFont(var_file)
            font = instantiateVariableFont(variable, {"wght": weight},
                                           inplace=True, updateFontNames=True)
            aliases = {
                1: "Noto Sans TC",
                2: face,
                4: f"Noto Sans TC {face}",
                6: f"NotoSansTC-{face}",
                16: "Noto Sans TC",
                17: face,
            }
            for entry in font["name"].names:
                if entry.nameID not in aliases:
                    continue
                name = aliases[entry.nameID]
                entry.string = (name.encode("utf-16-be") if entry.platformID == 3
                                else name.encode("utf-8"))
            destination = FONTS / f"NotoSansTC-{face}.ttf"
            font.save(destination)
            font.close()
            variable.close()
            print(f"{destination.name}: {destination.stat().st_size} bytes")
    finally:
        var_file.unlink(missing_ok=True)


if __name__ == "__main__":
    build()
