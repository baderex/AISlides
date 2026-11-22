from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.domain import Chart, Document, Image, Shape, Slide, Table, Text, Theme
from app.rendering import fit_image

if TYPE_CHECKING:
    from app.composition import OutlineItem, SlideContent


CONSULTING_DIRECTION = "Strategy consulting"
FIRM = r"(?:bcg|boston consulting group)"
STYLE = r"(?:style|inspired|format|look|aesthetic|visuals?|layout|template|design|like)"


def requested_consulting(prompt: str) -> bool:
    return bool(re.search(rf"\b{FIRM}(?:['’]s)?[\s-]+{STYLE}\b|\b(?:{STYLE})\s+(?:of\s+)?{FIRM}\b",
                          prompt, re.IGNORECASE))


def consulting_theme() -> Theme:
    return Theme(profile="consulting", background="#FFFFFF", ink="#16251E", accent="#006841",
                 muted="#4D6056", surface="#EDF4EF", font="Arial")


PLAN_GUIDANCE = (
    "Use a strategy consulting storyline. After the cover, write each headline as a concise conclusion or recommendation "
    "rather than a subject label. Begin with an executive answer, compare the actual markets or alternatives in an exhibit, "
    "and finish with a decision and next steps. Use comparison or research layouts for matrices. "
    "For a country landscape name the countries and compare them on common dimensions. "
    "Use neutral, unbranded copy and distinguish qualitative hypotheses from evidence."
)
SLIDE_GUIDANCE = (
    "For comparison or research, prefer an editable table with a short header row and up to six concise data rows. "
    "Use comparable dimensions and identify the actual markets or alternatives. For a qualitative exhibit, clearly label "
    "hypotheses and leave unsupported metrics out. Use the subtitle for the context or scope. "
    "For a closing slide, express the actions and how to validate or sequence them."
)


def compose_consulting(item: OutlineItem, content: SlideContent, deck: Document, index: int, image_ids: set[str],
                       *, web_image_ratio: float | None = None) -> Slide:
    theme = deck.theme
    factor = {"1:1": .68, "4:3": .83, "16:9": 1}[deck.aspect_ratio]
    references = {source.id: source.title for source in deck.sources}
    source_ids = list(dict.fromkeys(key for key in content.source_ids if key in references))
    slide = Slide(title=item.title, background=theme.background, layout=item.layout, notes=content.notes,
                  source_ids=source_ids)

    def block(x, y, w, h, fill=theme.accent):
        slide.elements.append(Shape(x=x, y=y, w=w, h=h, fill=fill, z=-1))

    def label(value, x, y, w, h, size, color=theme.ink, bold=False, align="left"):
        if value.strip():
            slide.elements.append(Text(x=x, y=y, w=w, h=h, text=value, size=max(8, size * factor),
                                       color=color, bold=bold, font=theme.font, align=align))

    if item.layout == "cover":
        block(.742, .0, .258, 1)
        block(.742, .73, .258, .27, "#004E36")
        block(.794, .19, .142, .006, "#87C7A7")
        block(.794, .25, .102, .006, "#87C7A7")
        block(.06, .156, .065, .009)
        label(item.title, .06, .23, .64, .29, 47, bold=True)
        label(content.subtitle, .06, .56, .60, .19, 21, theme.muted)
        return slide

    label(item.title, .06, .077, .88, .16, 35, bold=True)
    block(.06, .255, .88, .005)
    label(content.subtitle, .06, .279, .88, .088, 17, theme.muted)
    points = content.points[:4]
    rows = content.table or []
    valid_table = 2 <= len(rows) <= 7 and 2 <= len(rows[0]) <= 5 and all(len(row) == len(rows[0]) for row in rows)
    if not valid_table and item.layout in {"comparison", "research"} and points and not web_image_ratio:
        rows = [["Observation", "Implication"], *[[point.title, point.body] for point in points]]
        valid_table = True

    chart = content.chart
    exhibit = None
    if chart and source_ids:
        try:
            exhibit = Chart(x=.06, y=.39, w=.88, h=.50, chart=chart.chart, categories=chart.categories,
                            series=[{"name": chart.label, "values": chart.values}], color=theme.accent)
        except ValueError:
            slide.warnings.append("The chart data could not be rendered.")

    if exhibit:
        slide.elements.append(exhibit)
    elif valid_table:
        rows = [[cell[:180] for cell in row] for row in rows]
        slide.elements.append(Table(x=.06, y=.39, w=.88, h=.5, rows=rows, fill=theme.surface, color=theme.ink,
                                    size=max(8, min(17 * factor, 100 / len(rows))),
                                    header_fill=theme.accent, header_color="#FFFFFF"))
    elif content.image_asset_id in image_ids and item.layout in {"showcase", "editorial", "research"}:
        image = Image(x=.56, y=.39, w=.38, h=.5, asset_id=content.image_asset_id, alt=item.title)
        if web_image_ratio:
            slide.elements.append(fit_image(image, deck, web_image_ratio))
            for i, point in enumerate(points[:3]):
                y = .39 + .165 * i
                label(point.title, .06, y, .45, .06, 18, bold=True)
                label(point.body, .06, y + .065, .45, .09, 15, theme.muted)
        else:
            slide.elements.append(image)
            for i, point in enumerate(points[:2]):
                y = .39 + .25 * i
                label(point.title, .06, y, .45, .09, 20, bold=True)
                label(point.body, .06, y + .098, .45, .12, 16, theme.muted)
    elif item.layout in {"timeline", "process", "closing"} and points:
        width = .88 / len(points)
        block(.06, .46, .88, .006, theme.surface)
        for i, point in enumerate(points):
            x = .06 + i * width
            block(x, .453, .024, .019)
            label(f"{i + 1:02d}", x, .40, width - .018, .044, 13, theme.accent, True)
            label(point.title, x, .512, width - .018, .137, 21, bold=True)
            label(point.body, x, .668, width - .023, .19, 17, theme.muted)
    else:
        row_height = .49 / max(1, len(points))
        for i, point in enumerate(points):
            y = .385 + i * row_height
            block(.06, y + .008, .005, row_height - .027, theme.accent)
            if i:
                block(.077, y, .863, .002, theme.surface)
            label(point.title, .079, y + .013, .26, row_height - .025, 18, bold=True)
            label(point.body, .366, y + .013, .574, row_height - .025, 17, theme.muted)

    if source_ids:
        source_note = "; ".join(references[key] for key in source_ids)
        label(f"Sources: {source_note}", .06, .917, .80, .048, 9, theme.muted)
    label(f"{index + 1:02d}", .88, .929, .06, .033, 9, theme.muted, align="right")
    return slide
