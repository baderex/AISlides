"""Deck-level art direction and deterministic component styles."""
import base64
import io

from PIL import Image as PILImage
from pydantic import Field

from app.domain import Color, Contract, DesignRules, Theme
from app.rendering import contrast
from app.storage import Storage


class ArtDirection(Contract):
    background: Color
    ink: Color
    accent: Color
    muted: Color
    surface: Color
    font: str = Field(min_length=1, max_length=60)
    design: DesignRules


DIRECTOR_PROMPT = (
    "You are an expert presentation art director. Create one cohesive design system. "
    "Infer audience, purpose, industry, mood and visual references from the request and clarification answers. "
    "Honor explicit brand instructions, exact color values, requested fonts and locked template settings before "
    "inferred preferences. Treat uploaded materials as references. Choose body and heading fonts, card geometry, "
    "spacing, cover treatment, chart colors and an imagery direction. Prefer Arial or Georgia for portable typography "
    "unless a brand specifies another font. Make ink and muted text readable on both background and surface. "
    "Use a contrasting, quieter dense_surface for data tables. For dark luxury use a charcoal background, restrained "
    "gold accents, a rules-style cover, editorial typography and a light warm table surface unless the user specifies otherwise. "
    "Return only design JSON matching the schema. Do not produce slide content, layout assignments, HTML or CSS."
)


def reference_sheet(assets):
    images = [asset for asset in assets if asset.media_type.startswith("image/")][:4]
    if not images:
        return None
    sheet = PILImage.new("RGB", (256 * len(images), 256), "white")
    storage = Storage()
    for index, asset in enumerate(images):
        with PILImage.open(io.BytesIO(storage.get(asset.id))) as image:
            image.thumbnail((256, 256))
            sheet.paste(image.convert("RGB"), (256 * index, 0))
    output = io.BytesIO()
    sheet.save(output, "PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def readable(preferred, surface, *alternatives, minimum=4.5):
    colors = (preferred, *alternatives, "#FFFFFF", "#000000")
    return next(color for color in colors if contrast(color, surface) >= minimum)


def style_table(table, theme):
    rules = theme.design
    dense = len(table.rows) >= 5 or len(table.rows[0]) >= 4
    table.fill = rules.dense_surface if dense else theme.surface
    table.color = readable(rules.dense_ink if dense else theme.ink, table.fill, theme.ink, theme.background)
    table.header_fill = theme.accent
    table.header_color = readable(theme.ink, theme.accent, theme.background)
    table.font = theme.font


def style_chart(chart, theme, background):
    colors = [readable(color, background, theme.ink, minimum=3) for color in [theme.accent, *theme.design.chart_colors]]
    chart.palette = list(dict.fromkeys(colors))[:5]
    chart.color = chart.palette[0]
    chart.label_color = readable(theme.muted, background, theme.ink)
    chart.font = theme.font
