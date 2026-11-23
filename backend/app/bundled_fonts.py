"""Licensed slide fonts shared by the browser, raster/PDF and PowerPoint adapters."""
import base64
from functools import lru_cache
from pathlib import Path
from struct import pack, unpack_from

from reportlab.pdfbase.ttfonts import TTFontFile


FONT_DIR = Path(__file__).with_name("fonts")
FAMILIES = ("Source Serif 4", "Manrope", "IBM Plex Mono")


def faces(families):
    wanted = {family.casefold() for family in families}
    for family in FAMILIES:
        if family.casefold() in wanted:
            for style in ("Regular", "Bold"):
                yield family, style, FONT_DIR / f"{family.replace(' ', '')}-{style}.ttf"


@lru_cache(maxsize=6)
def browser_face(family, style, path):
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    weight = 700 if style == "Bold" else 400
    return f"@font-face{{font-family:'{family}';font-weight:{weight};src:url(data:font/ttf;base64,{data})}}"


@lru_cache(maxsize=6)
def powerpoint_face(path):
    """Wrap an unmodified static TTF in the uncompressed EOT format used in PresentationML."""
    raw = path.read_bytes()
    face = TTFontFile(str(path))
    os2, head, names = (face.get_table(tag) for tag in ("OS/2", "head", "name"))
    name_count, offset = unpack_from(">HH", names, 2)
    english = {}
    for i in range(name_count):
        platform, encoding, lang, key, length, start = unpack_from(">6H", names, 6 + 12*i)
        if platform == 3 and lang == 0x409 and encoding in (1, 10):
            english[key] = names[offset+start:offset+start+length].decode("utf-16-be").encode("utf-16-le")
    strings = b"".join(pack("<HH", 0, len(english[key])) + english[key] for key in (1, 2, 5, 4))
    weight, = unpack_from(">H", os2, 4)
    rights, = unpack_from(">H", os2, 8)
    ranges = (*unpack_from(">4I", os2, 42), *unpack_from(">2I", os2, 78))
    checksum, = unpack_from(">I", head, 8)
    metadata = pack("<10sBBIHH7I16x", os2[32:42], 1, 0, weight, rights, 0x504C, *ranges, checksum)
    header = metadata + strings + pack("<HH", 0, 0)  # Empty root string, EOT 0x00020001.
    return pack("<4I", 16 + len(header) + len(raw), len(raw), 0x20001, 0) + header + raw
