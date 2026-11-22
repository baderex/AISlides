"""Deck-level art direction and deterministic component styles."""
import base64
import io
import re

from PIL import Image as PILImage
from pydantic import Field

from app.domain import Color, Contract, DesignRules, Table, Theme
from app.rendering import contrast, flattened
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


def blend(first, second, share):
    channels = [round(int(first[i:i+2], 16) * (1 - share) + int(second[i:i+2], 16) * share)
                for i in (1, 3, 5)]
    return "#" + "".join(f"{channel:02X}" for channel in channels)


NUMERIC_CELL = re.compile(r"[+\-−]?\s*[$€£¥]?\s*(?:\d[\d,]*(?:\.\d+)?|\.\d+)\s*%?")


def style_table(table, theme, background=None, *, preserve_palette=False):
    rules = theme.design
    background = background or theme.background
    if not preserve_palette:
        dense = len(table.rows) >= 5 or len(table.rows[0]) >= 4
        table.fill = rules.dense_surface if rules and dense else theme.surface
        if contrast(table.fill, background) < 3:
            table.fill = blend(background, table.fill, .55)
        table.color = readable(theme.ink, table.fill, rules.dense_ink if rules else theme.muted, background)
        table.header_fill = blend(table.fill, background, .42)
        table.header_color = readable(theme.ink, table.header_fill, table.color, background)
        table.font = theme.font
        if table.w >= .65 and len(table.rows) <= 6:
            table.size = max(table.size, 18)
    table.row_rule = blend(table.fill, table.color, .27)
    table.header_rule = theme.accent
    table.numeric_columns = []
    for index, column in enumerate(zip(*table.rows[1:])):
        values = [value.strip() for value in column if value.strip() not in {"", "—", "–", "-", "N/A", "n/a"}]
        if values and all(NUMERIC_CELL.fullmatch(value) for value in values):
            table.numeric_columns.append(index)


def style_slide_tables(slide, theme, *, preserve_palette=False):
    for element, *_ in flattened(slide.elements):
        if isinstance(element, Table):
            style_table(element, theme, slide.background, preserve_palette=preserve_palette)
    return slide


def style_chart(chart, theme, background):
    colors = [readable(color, background, theme.ink, minimum=3) for color in [theme.accent, *theme.design.chart_colors]]
    palette = list(dict.fromkeys(colors))[:5]
    if chart.chart == "stacked_column":
        palette[1:] = sorted(palette[1:], key=lambda color: contrast(palette[0], color), reverse=True)
    chart.palette = palette
    chart.color = chart.palette[0]
    chart.label_color = readable(theme.muted, background, theme.ink)
    chart.font = theme.font
