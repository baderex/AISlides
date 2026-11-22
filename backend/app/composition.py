import re
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError

from app.branding import choose_layout, compose_branded
from app.consulting import compose_consulting, consulting_theme, requested_consulting
from app.domain import BrandTemplate, Chart, Contract, Document, Image, Layout, Shape, ShortText, Slide, Table, Text, Theme
from app.questions import Question
from app.rendering import fit_image


class Intent(Contract):
    subject: ShortText
    objective: str = Field(default="", max_length=700)
    audience: str = Field(default="", max_length=250)
    tone: str = Field(default="", max_length=250)
    visual_direction: str = Field(default="", max_length=250)
    requires_quantitative_evidence: bool = False
    slide_count: int | None = Field(default=None, ge=1, le=30)
    web_images_requested: bool = False
    missing: list[str] = Field(default_factory=list, max_length=8)


class PlanningResponse(Contract):
    # Some providers add slide copy to the outline, even in JSON mode.
    model_config = ConfigDict(extra="ignore", json_schema_extra={"additionalProperties": False})


class OutlineItem(PlanningResponse):
    title: ShortText
    layout: Layout = Field(description="Use one exact layout ID from the enum. Use chart for a sourced chart and research for a data table.")
    purpose: str = Field(default="", max_length=500)
    image_query: str = Field(default="", max_length=380)
    research_query: str = Field(default="", max_length=260)

    @field_validator("layout", mode="before")
    @classmethod
    def normalize_layout(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        label = " ".join(value.lower().replace("_", " ").replace("-", " ").split())
        return {
            "line chart": "chart",
            "bar chart": "chart",
            "column chart": "chart",
            "pie chart": "chart",
            "table": "research",
            "data table": "research",
        }.get(label, label)


class Outline(PlanningResponse):
    title: ShortText
    narrative: str = Field(default="", max_length=1000)
    slides: list[OutlineItem] = Field(min_length=1, max_length=30)

    @field_validator("slides")
    @classmethod
    def exact_slide_count(cls, slides: list[OutlineItem], info: ValidationInfo) -> list[OutlineItem]:
        count = (info.context or {}).get("slide_count")
        if count is not None and len(slides) != count:
            raise PydanticCustomError("slide_count", "return exactly {expected} slides (received {actual})",
                                      {"expected": count, "actual": len(slides)})
        return slides


class Point(Contract):
    title: Annotated[str, Field(min_length=1, max_length=65)]
    body: str = Field(default="", max_length=250)
    value: str = Field(default="", max_length=25)


class ChartContent(Contract):
    categories: list[ShortText] = Field(min_length=1, max_length=10)
    values: list[float] = Field(min_length=1, max_length=10)
    label: ShortText
    chart: Literal["column", "bar", "line", "pie"] = "column"


class SlideContent(Contract):
    title: ShortText
    subtitle: str = Field(default="", max_length=220)
    points: list[Point] = Field(default_factory=list, max_length=5)
    notes: str = Field(default="", max_length=5000)
    source_ids: list[str] = Field(default_factory=list, max_length=12)
    image_asset_id: str | None = None
    image_prompt: str = Field(default="", max_length=400)
    chart: ChartContent | None = None
    table: list[list[str]] | None = Field(default=None, max_length=8)


class ResearchQueries(Contract):
    queries: list[ShortText] = Field(min_length=1, max_length=4)


class EvidenceSummary(PlanningResponse):
    summary: str = Field(max_length=2200)
    uncertainties: list[ShortText] = Field(default_factory=list, max_length=6)


class ResearchBrief(Contract):
    summary: str = Field(max_length=1800)
    uncertainties: list[ShortText] = Field(default_factory=list, max_length=6)
    follow_up: Question | None = None


class Review(Contract):
    issues: list[ShortText] = Field(default_factory=list, max_length=5)
    regenerate_content: bool = False


IMAGE_LAYOUTS = frozenset({"showcase", "editorial", "research"})


def accepts_web_image(item: OutlineItem, content: SlideContent, brand: BrandTemplate | None) -> bool:
    if item.layout not in IMAGE_LAYOUTS or content.chart or content.table:
        return False
    if brand:
        return any(slot.kind == "picture" for slot in choose_layout(brand.layouts, item.layout).slots)
    return True


def is_data_driven(direction: str) -> bool:
    return bool(re.search(r"\bdata[\s_-]+(?:driven|visuali[sz]ations?)\b", direction, re.IGNORECASE))


def design(direction: str, mode: str, selected: Theme | None) -> Theme:
    if selected:
        return selected
    direction = direction.lower()
    if requested_consulting(direction) or any(term in direction for term in
        ("strategy consulting", "consulting-style", "consulting style", "consulting deck", "management consulting")):
        return consulting_theme()
    if "editorial" in direction:
        return Theme(background="#F7F2E9", ink="#27213D", accent="#9D3C30", muted="#625968", surface="#EEE2D2", font="Georgia")
    if "academic" in direction:
        return Theme(background="#F0F8F5", ink="#16352F", accent="#08745D", muted="#395E54", surface="#DAEDE6")
    if is_data_driven(direction):
        return Theme(background="#F3F6FA", ink="#122239", accent="#0F6B62", muted="#41566B", surface="#E3EDF0")
    if mode == "creative" or "bold" in direction:
        return Theme(background="#F1ECFF", ink="#281852", accent="#8737AC", muted="#57416B", surface="#E6D4F9")
    return Theme()


def compose(item: OutlineItem, content: SlideContent, document: Document, index: int, image_ids: set[str],
            brand: BrandTemplate | None = None, *, web_image_ratio: float | None = None, data_driven: bool = False) -> Slide:
    source_ids = {source.id for source in document.sources}
    valid_chart = content.chart and len(content.chart.values) == len(content.chart.categories)
    table = content.table
    valid_table = table and 1 <= len(table[0]) <= 8 and all(len(row) == len(table[0]) for row in table)
    if data_driven and source_ids.intersection(content.source_ids) and item.layout not in {"cover", "section", "quote", "closing"}:
        if valid_chart and len(content.chart.values) >= 2 and not (item.layout == "research" and valid_table):
            item = item.model_copy(update={"layout": "chart"})
        elif valid_table:
            item = item.model_copy(update={"layout": "research"})
    if brand:
        return compose_branded(item, content, document, image_ids, brand)
    t = document.theme
    if t.profile == "consulting":
        return compose_consulting(item, content, document, index, image_ids, web_image_ratio=web_image_ratio)
    compact = document.aspect_ratio == "1:1"
    factor = {"1:1": .68, "4:3": .83, "16:9": 1}[document.aspect_ratio]
    editorial = t.font == "Georgia"
    dark = item.layout in {"cover", "section", "quote", "closing"} and not editorial
    bg = t.ink if dark else t.background
    ink, muted, accent = ("#FFFFFF", "#DDE1F5", "#E0DBFF") if dark else (t.ink, t.muted, t.accent)
    slide = Slide(title=item.title, background=bg, layout=item.layout, notes=content.notes,
                  source_ids=list(dict.fromkeys(s for s in content.source_ids if s in source_ids)))
    elements = slide.elements

    def rect(x, y, w, h, fill, shape="rect"):
        elements.append(Shape(x=x, y=y, w=w, h=h, fill=fill, shape=shape, z=-1))

    def label(value, x, y, w, h, size, color=ink, bold=False):
        if value.strip():
            elements.append(Text(x=x, y=y, w=w, h=h, text=value, size=max(8, size * factor), color=color, bold=bold, font=t.font))

    label(f"{index + 1:02d}  /  {item.layout.upper()}", .068, .065, .65, .045, 12, accent, True)
    rect(.068, .137, .048, .008, accent)
    if item.layout in {"cover", "section", "quote", "closing"}:
        if editorial:
            rect(.76, .125, .172, .59, t.accent)
            rect(.788, .20, .113, .008, "#E5B4A8")
            rect(.788, .53, .113, .008, "#E5B4A8")
            label(f"{index + 1:02d}", .788, .33, .12, .14, 38, "#FFFFFF", True)
        else:
            rect(.77, .09, .19, .32, t.accent, "ellipse")
            rect(.82, .28, .135, .24, t.muted, "ellipse")
        label(item.title, .068, .24, .66, .28, 47, ink, True)
        if content.subtitle:
            label(content.subtitle, .07, .58, .64 if editorial else .70, .13, 23, muted)
        points = content.points[:3]
        if points and item.layout == "closing":
            for i, point in enumerate(points):
                label(point.title, .07 + .295 * i, .77, .274, .13, 18, ink, True)
        elif points and item.layout == "quote":
            label(points[0].title, .07, .77, .65, .08, 18, muted)
        return slide

    label(item.title, .068, .17, .87, .17, 35, ink, True)
    label(content.subtitle, .07, .352, .86, .08, 17, muted)
    points = content.points
    if item.layout == "research" and valid_table:
        rows = [[cell[:180] for cell in row] for row in table]
        elements.append(Table(x=.07, y=.46, w=.86, h=.44, rows=rows, color=t.ink, fill=t.surface))
    elif content.image_asset_id in image_ids and item.layout in IMAGE_LAYOUTS:
        image = Image(x=.55, y=.45, w=.38, h=.44, asset_id=content.image_asset_id, alt=item.title)
        if web_image_ratio:
            elements.append(fit_image(image, document, web_image_ratio))
            for i, point in enumerate(points[:3]):
                y = .46 + i * .146
                label(point.title, .07, y, .43, .056, 18, ink, True)
                label(point.body, .07, y + .06, .43, .08, 15, muted)
        else:
            elements.append(image)
            for i, point in enumerate(points[:2]):
                y = .46 + i * .21
                label(point.title, .07, y, .43, .06, 20, ink, True)
                label(point.body, .07, y + .07, .43, .125, 16, muted)
    elif item.layout == "chart" and slide.source_ids and valid_chart:
        data = content.chart
        try:
            elements.append(Chart(x=.07, y=.51 if data_driven else .45, w=.54 if data_driven else .86,
                                  h=.39 if data_driven else .46, categories=data.categories,
                                  series=[{"name": data.label, "values": data.values}], chart=data.chart, color=t.accent))
        except ValueError:
            slide.warnings.append("The chart data could not be rendered.")
        else:
            if data_driven:
                label(data.label, .07, .45, .54, .05, 15, ink, True)
                rows = [["Category", "Value"], *[[category, f"{value:,.8g}"]
                        for category, value in zip(data.categories, data.values)]]
                elements.append(Table(x=.635, y=.45, w=.295, h=min(.44, .060 * len(rows)), rows=rows,
                                      size=max(9, min(14 * factor, 132 / len(rows))), color=ink, fill=t.surface,
                                      header_fill=accent, header_color="#FFFFFF"))
    elif item.layout == "timeline" and points:
        points = points[:3]
        width = .86 / len(points)
        rect(.07, .542, .86, .007, t.surface)
        for i, point in enumerate(points):
            x = .07 + width * i
            rect(x, .521, .026, .047, t.accent, "ellipse")
            label(point.value or f"0{i + 1}", x, .465, width - .024, .047, 14, accent, True)
            label(point.title, x, .605, width - .024, .095, 20, ink, True)
            label(point.body, x, .72, width - .024, .18, 16, muted)
    elif item.layout in {"comparison", "metrics", "process", "showcase"} and points:
        points = points[:2 if item.layout == "comparison" else 3]
        width = (.864 - .022 * (len(points) - 1)) / len(points)
        for i, point in enumerate(points):
            x = .068 + i * (width + .022)
            rect(x, .463, width, .426, t.surface, "round")
            value = point.value if slide.source_ids or not any(character.isdigit() for character in point.value) else ""
            label(value or (f"0{i + 1}" if item.layout == "process" else "—"), x + .018, .495,
                  width - .036, .083, 25, accent, True)
            label(point.title, x + .018, .596, width - .036, .09, 18, ink, True)
            label(point.body, x + .018, .70, width - .036, .157, 16 if not compact else 15, muted)
    else:
        for i, point in enumerate(points[:3]):
            y = .46 + .146 * i
            rect(.07, y + .021, .014, .025, t.accent, "ellipse")
            label(point.title, .103, y, .81, .062, 20, ink, True)
            label(point.body, .103, y + .063, .81, .075, 16, muted)
    if len(points) > 3:
        remainder = "\n".join(f"{p.title}: {p.body}" for p in points[3:])
        slide.notes = (slide.notes + "\n" + remainder).strip()[:7000]
    return slide
