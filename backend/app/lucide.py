import io
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image


# Transparent PNGs rendered from @lucide/angular 1.77.0. See lucide_icons/LICENSE.
ICON_DIRECTORY = Path(__file__).with_name("lucide_icons")
ICON_NAMES = tuple(sorted(path.stem for path in ICON_DIRECTORY.glob("*.png")))
ICON_ASSET = re.compile(r"lucide-([a-z-]+)_([0-9a-f]{6})")


def icon_asset_id(name: str, color: str) -> str:
    return f"lucide-{name}_{color[1:].lower()}"


def bundled_icon(asset_id: str) -> tuple[str, str] | None:
    match = ICON_ASSET.fullmatch(asset_id)
    if match and match[1] in ICON_NAMES:
        return match[1], f"#{match[2]}"
    return None


@lru_cache(maxsize=256)
def icon_png(name: str, color: str) -> bytes:
    with Image.open(ICON_DIRECTORY / f"{name}.png") as source:
        image = Image.new("RGBA", source.size, color)
        image.putalpha(source.getchannel("A"))
    result = io.BytesIO()
    image.save(result, format="PNG")
    return result.getvalue()
