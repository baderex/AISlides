import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, JsonValue, model_validator

from app.db import uid

Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=70, pattern=r"^[a-zA-Z0-9_-]+$")]
ShortText = Annotated[str, Field(min_length=1, max_length=220)]
Aspect = Literal["16:9", "4:3", "1:1"]
Layout = Literal["cover", "section", "summary", "comparison", "timeline", "process", "metrics", "chart", "showcase", "editorial", "research", "quote", "closing"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class DesignRules(Contract):
    audience: ShortText
    purpose: ShortText
    industry: ShortText
    mood: ShortText
    brand_constraints: str = Field(max_length=400)
    visual_references: str = Field(max_length=400)
    imagery: ShortText
    heading_font: str = Field(min_length=1, max_length=60)
    spacing: Literal["compact", "regular", "spacious"]
    card_shape: Literal["rect", "round"]
    cover_style: Literal["rules", "panel", "circles"]
    cover_background: Color
    dense_surface: Color
    dense_ink: Color
    chart_colors: list[Color] = Field(min_length=2, max_length=5)


class DesignConstraints(Contract):
    background_mode: Literal["light", "dark"] | None = None
    restrained: bool = False
    palette: list[Color] = Field(default_factory=list, max_length=10)
    colors: dict[Literal["background", "cover_background", "ink", "accent", "muted", "surface", "dense_surface"], Color] = Field(default_factory=dict)
    heading_font: str | None = Field(default=None, max_length=60)
    body_font: str | None = Field(default=None, max_length=60)


class Theme(Contract):
    background: Color = "#F7F8FF"
    ink: Color = "#181D3B"
    accent: Color = "#4C52D9"
    muted: Color = "#545D79"
    surface: Color = "#E7E9FF"
    font: str = Field(default="Arial", max_length=60)
    profile: Literal["standard", "consulting"] = "standard"
    design: DesignRules | None = None
    constraints: DesignConstraints | None = None


class Source(Contract):
    id: Identifier = Field(default_factory=uid)
    title: ShortText
    url: HttpUrl | None = None
    excerpt: str = Field(default="", max_length=3000)
    asset_id: Identifier | None = None


class ElementBase(Contract):
    id: Identifier = Field(default_factory=uid)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)
    rotation: float = Field(default=0, ge=-360, le=360)
    z: int = Field(default=0, ge=-1000, le=1000)
    opacity: float = Field(default=1, ge=0, le=1)
    border: Color | None = None
    edited: bool = False
    placeholder_idx: int | None = Field(default=None, ge=0, le=4294967295)

    @model_validator(mode="after")
    def inside_canvas(self):
        if self.x + self.w > 1.000001 or self.y + self.h > 1.000001:
            raise ValueError("Element exceeds the slide bounds")
        return self


class TextSpan(Contract):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    color: Color | None = None
    bold: bool | None = None


class Text(ElementBase):
    type: Literal["text"] = "text"
    text: str = Field(max_length=5000)
    size: float = Field(default=24, ge=8, le=100)
    color: Color = "#181D3B"
    font: str = Field(default="Arial", max_length=60)
    bold: bool = False
    align: Literal["left", "center", "right"] = "left"
    spans: list[TextSpan] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def valid_spans(self):
        previous = 0
        for span in self.spans:
            if not previous <= span.start < span.end <= len(self.text):
                raise ValueError("Text spans must be ordered, within the text, and non-overlapping")
            previous = span.end
        return self

    def text_style(self, start: int, end: int):
        cursor = start
        for span in self.spans:
            if span.end <= start:
                continue
            if span.start >= end:
                break
            if span.start > cursor:
                yield self.text[cursor:span.start], self.bold, self.color
            yield (self.text[max(start, span.start):min(end, span.end)],
                   self.bold if span.bold is None else span.bold, span.color or self.color)
            cursor = min(end, span.end)
        if cursor < end:
            yield self.text[cursor:end], self.bold, self.color


class Shape(ElementBase):
    type: Literal["shape"] = "shape"
    shape: Literal["rect", "round", "ellipse", "triangle"] = "rect"
    fill: Color = "#4C52D9"


class Image(ElementBase):
    type: Literal["image"] = "image"
    asset_id: Identifier
    alt: str = Field(default="", max_length=220)


class ConnectorAnchor(Contract):
    shape_id: Identifier
    side: Literal["top", "left", "bottom", "right"]


class Line(ElementBase):
    type: Literal["line"] = "line"
    color: Color = "#4C52D9"
    weight: float = Field(default=2, ge=0.5, le=20)
    direction: Literal["diagonal", "right", "left", "down", "up"] = "diagonal"
    arrows: Literal["none", "end", "both"] = "none"
    start_anchor: ConnectorAnchor | None = None
    end_anchor: ConnectorAnchor | None = None

    def endpoints(self, width, height):
        return {
            "diagonal": ((0, 0), (width, height)),
            "right": ((0, height / 2), (width, height / 2)),
            "left": ((width, height / 2), (0, height / 2)),
            "down": ((width / 2, 0), (width / 2, height)),
            "up": ((width / 2, height), (width / 2, 0)),
        }[self.direction]


class Table(ElementBase):
    type: Literal["table"] = "table"
    rows: list[list[Annotated[str, Field(max_length=180)]]] = Field(min_length=1, max_length=12)
    color: Color = "#181D3B"
    fill: Color = "#E7E9FF"
    size: float = Field(default=12, ge=8, le=28)
    font: str = Field(default="Arial", max_length=60)
    header_fill: Color | None = None
    header_color: Color | None = None
    row_rule: Color | None = None
    header_rule: Color | None = None
    numeric_columns: list[Annotated[int, Field(ge=0, le=7)]] = Field(default_factory=list, max_length=8)
    focus_rows: list[Annotated[int, Field(ge=1, le=11)]] = Field(default_factory=list, max_length=11)
    focus_fill: Color | None = None
    negative_ink: Color | None = None

    def row_style(self, index: int) -> tuple[str, str, bool]:
        if index == 0 and self.header_fill:
            return self.header_fill, self.header_color or self.color, True
        if self.focus_fill and index in self.focus_rows:
            return self.focus_fill, self.color, False
        return self.fill, self.color, False

    def negative_cell(self, row: int, column: int) -> bool:
        return bool(self.negative_ink and row > 0 and column in self.numeric_columns and re.fullmatch(
            r"[-−]\s*[$€£¥]?\s*(?:\d[\d,]*(?:\.\d+)?|\.\d+)\s*%?", self.rows[row][column].strip()
        ))

    def cell_style(self, row: int, column: int) -> tuple[str, str]:
        fill, ink, _ = self.row_style(row)
        return fill, self.negative_ink if self.negative_cell(row, column) else ink

    def cell_align(self, column: int) -> Literal["left", "right"]:
        return "right" if self.row_rule and column in self.numeric_columns else "left"

    def cell_bold(self, row: int, column: int) -> bool:
        return (row == 0 and self.header_fill is not None) or self.negative_cell(row, column) or bool(
            self.row_rule and column == 0 and 0 not in self.numeric_columns
        )

    def rule_after(self, row: int) -> tuple[str, float] | None:
        if not self.row_rule:
            return None
        if row == 0 and self.header_rule:
            return self.header_rule, 1.5
        return self.row_rule, .65

    @model_validator(mode="after")
    def rectangular(self):
        count = len(self.rows[0])
        if not 1 <= count <= 8 or any(len(row) != count for row in self.rows):
            raise ValueError("Table rows must have an equal width of 1 to 8 cells")
        return self


class Series(Contract):
    name: ShortText
    values: list[float] = Field(min_length=1, max_length=16)


def display_value(value: float, prefix: str = "") -> str:
    return f"{prefix}{value:,.2f}".rstrip("0").rstrip(".")


class Chart(ElementBase):
    type: Literal["chart"] = "chart"
    chart: Literal["bar", "column", "line", "pie", "stacked_column"] = "column"
    categories: list[ShortText] = Field(min_length=1, max_length=16)
    series: list[Series] = Field(min_length=1, max_length=5)
    color: Color = "#4C52D9"

    palette: list[Color] = Field(default_factory=list, max_length=5)
    label_color: Color = "#292C49"
    font: str = Field(default="Arial", max_length=60)
    value_prefix: str = Field(default="", max_length=8)
    show_values: bool = False

    def totals(self) -> list[float]:
        return [sum(values) for values in zip(*(series.values for series in self.series))]

    def category_labels(self) -> list[str]:
        if self.chart != "stacked_column":
            return self.categories
        return [f"{category}  {display_value(total, self.value_prefix)}"
                for category, total in zip(self.categories, self.totals())]

    def colors(self):
        return self.palette or [self.color, "#8B72DA", "#279990", "#BF693F", "#637B92"]

    @model_validator(mode="after")
    def matching_series(self):
        if any(len(s.values) != len(self.categories) for s in self.series):
            raise ValueError("Chart series must match its categories")
        if self.chart == "pie" and (len(self.series) != 1 or any(v < 0 for v in self.series[0].values)):
            raise ValueError("Pie charts need one non-negative series")
        if self.chart == "stacked_column" and (len(self.series) < 2 or any(v < 0 for s in self.series for v in s.values)):
            raise ValueError("Stacked columns require at least two non-negative series")
        return self


class Group(ElementBase):
    type: Literal["group"] = "group"
    elements: list["Element"] = Field(min_length=1, max_length=50)


Element = Annotated[Text | Shape | Image | Line | Table | Chart | Group, Field(discriminator="type")]
Group.model_rebuild()


Recipe = Literal[
    "standard", "editorial_cover", "photo_cover", "decision_cover", "closing_display", "closing_centered",
    "feature_panels", "feature_rows", "diagram_flow", "diagram_paths", "diagram_vertical",
    "dense_table", "dense_table_full", "chart", "readout", "allocation", "rollout", "decision",
]


class SlideSpec(Contract):
    plan: dict[str, JsonValue]
    content: dict[str, JsonValue]
    recipe: Recipe = "standard"
    visual_priority: str = Field(default="", max_length=180)
    image_ratio: float | None = Field(default=None, gt=0)
    web_image_ratio: float | None = Field(default=None, gt=0)
    data_driven: bool = False
    id_namespace: Identifier | None = None
    generated_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    deleted_ids: list[Identifier] = Field(default_factory=list, max_length=1000)
    generated_background: Color | None = None
    generated_notes: str = Field(default="", max_length=7000)


class Slide(Contract):
    id: Identifier = Field(default_factory=uid)
    title: ShortText
    layout: Layout = "summary"
    background: Color = "#F7F8FF"
    elements: list[Element] = Field(default_factory=list, max_length=100)
    notes: str = Field(default="", max_length=7000)
    source_ids: list[Identifier] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=30)
    spec: SlideSpec | None = None

    @model_validator(mode="after")
    def unique_elements(self):
        if len({element.id for element in self.elements}) != len(self.elements):
            raise ValueError("Element IDs must be unique per slide")
        return self


class Document(Contract):
    schema_version: Literal[1] = 1
    title: ShortText
    aspect_ratio: Aspect = "16:9"
    theme: Theme = Field(default_factory=Theme)
    sources: list[Source] = Field(default_factory=list, max_length=60)
    slides: list[Slide] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def references_exist(self):
        if len({slide.id for slide in self.slides}) != len(self.slides):
            raise ValueError("Slide IDs must be unique")
        ids = {source.id for source in self.sources}
        if any(not set(slide.source_ids) <= ids for slide in self.slides):
            raise ValueError("Slide references an unknown source")
        return self

    @property
    def dimensions(self) -> tuple[float, float]:
        return {"16:9": (13.333333, 7.5), "4:3": (10, 7.5), "1:1": (7.5, 7.5)}[self.aspect_ratio]


def read_document(raw: dict) -> Document:
    if "schema_version" not in raw:
        raw = {"schema_version": 1, **raw}
    return Document.model_validate(raw)


class Skill(Contract):
    schema_version: Literal[1] = 1
    name: ShortText
    description: str = Field(min_length=1, max_length=500)
    category: str = Field(min_length=1, max_length=60)
    tags: list[ShortText] = Field(default_factory=list, max_length=10)
    formats: list[Aspect] = Field(default_factory=lambda: ["16:9", "4:3", "1:1"], min_length=1, max_length=3)
    audience: str = Field(default="", max_length=250)
    instructions: str = Field(default="", max_length=3000)
    recipes: list[Layout] = Field(default_factory=list, max_length=13)
    theme: Theme = Field(default_factory=Theme)
    preview: Literal["strategy", "pitch", "research", "product", "data", "education"] = "strategy"
    brand: "BrandTemplate | None" = None


class BrandSlot(Contract):
    idx: int = Field(ge=0, le=4294967295)
    kind: Literal["title", "subtitle", "body", "chart", "table", "picture"]
    x: float
    y: float
    w: float
    h: float
    size: float = Field(ge=8, le=100)
    color: Color
    font: str = Field(max_length=60)
    bold: bool = False
    align: Literal["left", "center", "right"] = "left"


class BrandLayout(Contract):
    index: int = Field(ge=0)
    name: ShortText
    cover: bool = False
    section: bool = False
    background: Color
    slots: list[BrandSlot] = Field(max_length=32)


class BrandTemplate(Contract):
    asset_id: Identifier
    layouts: list[BrandLayout] = Field(min_length=1, max_length=100)


Skill.model_rebuild()


class CreatePresentation(Contract):
    prompt: str = Field(min_length=3, max_length=16000)
    mode: Literal["professional", "creative", "guided"] = Field(default="professional", deprecated=True)
    aspect_ratio: Aspect = "16:9"
    skill_id: Identifier | None = None
    asset_ids: list[Identifier] = Field(default_factory=list, max_length=15)
    research: bool | None = None
    generate_images: bool = False
    generate: bool = True
