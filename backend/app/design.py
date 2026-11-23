"""Deck-level art direction and deterministic component styles."""
import base64
import io
import re

from PIL import Image as PILImage
from pydantic import Field

from app.domain import Color, Contract, DesignConstraints, DesignRules, Table, Theme
from app.palette import blend, categorical_palette, readable, semantic_palette
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
    "Start from deck_design, its font pairing, grid, density and visual language. Prefer available_fonts. "
    "Infer audience, purpose, industry, mood and visual references from the request and clarification answers. "
    "Honor explicit brand instructions, exact color values, requested fonts and locked template settings before "
    "inferred preferences. Treat uploaded materials as references. Choose body and heading fonts, card geometry, "
    "spacing, cover treatment, chart colors and an imagery direction. Give font family names without style suffixes "
    "such as Bold or Regular. Choose fonts by role. For editorial slides prefer Source Serif 4 headlines "
    "with Manrope body text. Use IBM Plex Mono for code when the brand permits it. "
    "Use background for content slides and cover_background for the cover and closing. When a brief names a dark "
    "brand color without requiring dark slides throughout, consider a quiet light background for analytical slides "
    "and use the dark color on decision slides. Make ink and muted text readable on both background and surface. "
    "For an explicitly light or restrained financial-report brief, use light backgrounds on the cover and content slides. "
    "Reserve saturated accents for small marks. Use a distinct heading face for editorial covers when appropriate. "
    "Use a contrasting, quieter dense_surface for data tables. For dark luxury use a charcoal background, restrained "
    "gold accents, a rules-style cover, editorial typography and a light warm table surface unless the user specifies otherwise. "
    "Return only design JSON matching the schema. Do not produce slide content, layout assignments, HTML or CSS."
)


def font_family(value):
    return re.sub(r"\s+(?:bold|regular)$", "", value.strip(), flags=re.IGNORECASE)


BACKGROUND_DIRECTION = re.compile(
    r"\b(light|dark)[- ](?:backgrounds?|palettes?|themes?|financial|reports?|analytical|editorial|corporate|styles?)\b", re.I
)
HEX_COLOR = r"#[0-9a-fA-F]{6}(?![\da-fA-F])"
COLOR_ROLES = {
    "cover background": "cover_background", "slide background": "background", "background": "background",
    "accent": "accent", "body ink": "ink", "text color": "ink", "muted": "muted",
    "table surface": "dense_surface", "surface": "surface",
}


def explicit_constraints(prompt: str, style_answer: str = "") -> DesignConstraints | None:
    rules = DesignConstraints()
    for text in (prompt, style_answer):
        if match := BACKGROUND_DIRECTION.search(text):
            rules.background_mode = match[1].lower()
        elif re.search(r"\brestrained\s+(?:light\s+)?financial[- ]report\b", text, re.I):
            rules.background_mode = "light"
        if re.search(r"\brestrained\b", text, re.I):
            rules.restrained = True
        palette = re.search(r"\b(?:use only|allowed colors|locked palette)\b([^.;\n]{0,220})", text, re.I)
        if palette and (colors := re.findall(HEX_COLOR, palette[1])):
            rules.palette = list(dict.fromkeys(c.upper() for c in colors))[:10]
        for role, field in COLOR_ROLES.items():
            if role == "background":
                role = r"(?<!cover )(?<!slide )background"
            if match := re.search(rf"(?<![\w-]){role}\s*(?:[:=]|(?:is|of)\s+)?\s*({HEX_COLOR})", text, re.I):
                rules.colors[field] = match[1].upper()
        for role, field in (("headings?", "heading_font"), ("body(?: text)?", "body_font")):
            if match := re.search(rf'\b{role}\s+(?:font\s*)?(?:[:=]|is)\s*["“]?([A-Za-z][A-Za-z0-9 -]{{0,48}}?)["”]?(?=[,.;\n]|$)', text, re.I):
                setattr(rules, field, font_family(match[1]))
            elif match := re.search(rf'\b(?:use|set|and)\s+["“]?([A-Za-z](?:(?!\s+(?:and|for)\b)[A-Za-z0-9 -]){{0,48}}?)["”]?\s+for\s+{role}\b', text, re.I):
                setattr(rules, field, font_family(match[1]))
    return rules if rules != DesignConstraints() else None


def enforce_constraints(theme: Theme, constraints: DesignConstraints | None) -> Theme:
    if constraints is None:
        return theme
    theme = theme.model_copy(deep=True, update={"constraints": constraints})
    rules = theme.design
    roles = {"background": theme, "ink": theme, "accent": theme, "muted": theme, "surface": theme}
    if rules:
        roles.update(dict.fromkeys(("cover_background", "dense_surface"), rules))
    for field, model in roles.items():
        current = getattr(model, field).upper()
        if field in constraints.colors:
            current = constraints.colors[field]
        elif constraints.palette and current not in constraints.palette:
            def distance(candidate):
                return sum((int(candidate[i:i+2], 16) - int(current[i:i+2], 16)) ** 2 for i in (1, 3, 5))
            current = min(constraints.palette, key=distance)
        if field in {"background", "cover_background", "surface", "dense_surface"} and field not in constraints.colors:
            if constraints.background_mode == "light":
                acceptable = [color for color in constraints.palette if contrast(color, "#FFFFFF") <= 1.55]
                if contrast(current, "#FFFFFF") > 1.55:
                    current = acceptable[0] if acceptable else min(
                        constraints.palette, key=lambda c: contrast(c, "#FFFFFF"), default="#F7F5EF")
            elif constraints.background_mode == "dark" and field in {"background", "cover_background"}:
                acceptable = [color for color in constraints.palette if contrast(color, "#FFFFFF") >= 7]
                if contrast(current, "#FFFFFF") < 7:
                    current = acceptable[0] if acceptable else max(
                        constraints.palette, key=lambda c: contrast(c, "#FFFFFF"), default="#191B20")
        if constraints.restrained and field in {"background", "cover_background", "surface", "dense_surface"} and (
                not constraints.palette and field not in constraints.colors):
            channels = [int(current[i:i+2], 16) for i in (1, 3, 5)]
            if max(channels) - min(channels) > 33:
                gray = round(sum(channels) / 3)
                current = blend(current, "#" + f"{gray:02X}" * 3, .78)
        setattr(model, field, current)
    if constraints.body_font:
        theme.font = constraints.body_font
    if rules:
        if constraints.heading_font:
            rules.heading_font = constraints.heading_font
        if constraints.palette:
            rules.chart_colors = list(dict.fromkeys([theme.accent, *constraints.palette, theme.ink]))[:5]
            if len(rules.chart_colors) < 2:
                rules.chart_colors.append(readable(theme.ink, theme.background))
    return theme


def apply_brand_kit(theme: Theme, baseline: Theme) -> Theme:
    kit = baseline.brand_kit
    if kit is None:
        return theme.model_copy(update={"brand_kit": None})
    theme = theme.model_copy(deep=True, update={"brand_kit": kit})
    for role in kit.protected:
        if role in {"heading_font", "cover_background", "dense_surface", "chart_colors"}:
            if baseline.design and theme.design:
                setattr(theme.design, role, getattr(baseline.design, role))
        else:
            setattr(theme, role, getattr(baseline, role))
    return theme
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


NUMERIC_CELL = re.compile(r"[+\-−]?\s*[$€£¥]?\s*(?:\d[\d,]*(?:\.\d+)?|\.\d+)\s*%?")


def style_table(table, theme, background=None, *, preserve_palette=False):
    rules = theme.design
    background = background or theme.background
    table.font = theme.font
    dense = len(table.rows) >= 5 or len(table.rows[0]) >= 4
    if table.w >= .65:
        table.size = max(table.size, 15 if dense else 18)
    if not preserve_palette:
        cell_focus = bool(table.focus_rows and table.focus_columns)
        table.fill = rules.dense_surface if rules and dense else theme.surface
        if contrast(table.fill, background) < 3:
            table.fill = blend(background, table.fill, .55)
        table.color = readable(theme.ink, table.fill, rules.dense_ink if rules else theme.muted, background)
        table.header_fill = blend(table.fill, background, .42)
        table.header_color = readable(theme.ink, table.header_fill, table.color, background)
        if rules:
            table.focus_fill = blend(table.fill, theme.accent, .10 if cell_focus else .16) if table.focus_rows else None
            if table.focus_fill and contrast(table.color, table.focus_fill) < 4.5:
                table.focus_fill = blend(table.fill, theme.accent, .07)
            table.focus_cell_fill = None
            table.focus_ink = None
            if cell_focus:
                cell_fill = blend(table.fill, theme.accent, .28)
                table.focus_cell_fill = cell_fill if contrast(table.color, cell_fill) >= 4.5 else table.focus_fill
                candidates = (theme.accent, blend(table.color, theme.accent, .35), table.color)
                table.focus_ink = next((ink for ink in candidates if all(
                    contrast(ink, fill) >= 4.5 for fill in (table.focus_fill, table.focus_cell_fill)
                )), table.color)
            fills = [fill for fill in (table.fill, table.focus_fill, table.focus_cell_fill) if fill]
            table.negative_ink = semantic_palette(theme, table.fill)["negative"]
            if any(contrast(table.negative_ink, fill) < 4.5 for fill in fills):
                table.negative_ink = table.color
    table.row_rule = blend(table.fill, table.color, .27)
    table.header_rule = theme.accent
    table.numeric_columns = []
    for index, column in enumerate(zip(*table.rows[1:])):
        values = [value.strip() for value in column if value.strip() not in {"", "—", "–", "-", "N/A", "n/a"}]
        if values and all(NUMERIC_CELL.fullmatch(value) for value in values):
            table.numeric_columns.append(index)


def style_slide_tables(slide, theme, *, preserve_palette=False, table_floor=None):
    for element, *_ in flattened(slide.elements):
        if isinstance(element, Table):
            style_table(element, theme, slide.background, preserve_palette=preserve_palette)
            element.minimum_pt = table_floor
            element.size = max(element.size, table_floor or 8)
    return slide


def style_chart(chart, theme, background):
    palette = semantic_palette(theme, background)
    identities = len(chart.categories) if chart.chart == "pie" else len(chart.series)
    chart.palette = categorical_palette(theme, background, identities, distinct=chart.chart != "pie" and identities > 1)
    chart.color = chart.palette[0]
    chart.label_color = palette["muted"]
    if chart.highlight is not None:
        chart.context_color = palette["neutral"]
    chart.font = theme.font
