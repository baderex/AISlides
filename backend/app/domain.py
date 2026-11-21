from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from app.db import uid

Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=70, pattern=r"^[a-zA-Z0-9_-]+$")]
ShortText = Annotated[str, Field(min_length=1, max_length=220)]
Aspect = Literal["16:9", "4:3", "1:1"]
Layout = Literal["cover", "section", "summary", "comparison", "timeline", "process", "metrics", "chart", "showcase", "editorial", "research", "quote", "closing"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Theme(Contract):
    background: Color = "#F7F8FF"
    ink: Color = "#181D3B"
    accent: Color = "#4C52D9"
    muted: Color = "#545D79"
    surface: Color = "#E7E9FF"
    font: str = Field(default="Arial", max_length=60)
    profile: Literal["standard", "consulting"] = "standard"


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


class Text(ElementBase):
    type: Literal["text"] = "text"
    text: str = Field(max_length=5000)
    size: float = Field(default=24, ge=8, le=100)
    color: Color = "#181D3B"
    font: str = Field(default="Arial", max_length=60)
    bold: bool = False
    align: Literal["left", "center", "right"] = "left"


class Shape(ElementBase):
    type: Literal["shape"] = "shape"
    shape: Literal["rect", "round", "ellipse", "triangle"] = "rect"
    fill: Color = "#4C52D9"


class Image(ElementBase):
    type: Literal["image"] = "image"
    asset_id: Identifier
    alt: str = Field(default="", max_length=220)


class Line(ElementBase):
    type: Literal["line"] = "line"
    color: Color = "#4C52D9"
    weight: float = Field(default=2, ge=0.5, le=20)


class Table(ElementBase):
    type: Literal["table"] = "table"
    rows: list[list[Annotated[str, Field(max_length=180)]]] = Field(min_length=1, max_length=12)
    color: Color = "#181D3B"
    fill: Color = "#E7E9FF"
    size: float = Field(default=12, ge=8, le=28)
    header_fill: Color | None = None
    header_color: Color | None = None

    def row_style(self, index: int) -> tuple[str, str, bool]:
        if index == 0 and self.header_fill:
            return self.header_fill, self.header_color or self.color, True
        return self.fill, self.color, False

    @model_validator(mode="after")
    def rectangular(self):
        count = len(self.rows[0])
        if not 1 <= count <= 8 or any(len(row) != count for row in self.rows):
            raise ValueError("Table rows must have an equal width of 1 to 8 cells")
        return self


class Series(Contract):
    name: ShortText
    values: list[float] = Field(min_length=1, max_length=16)


class Chart(ElementBase):
    type: Literal["chart"] = "chart"
    chart: Literal["bar", "column", "line", "pie"] = "column"
    categories: list[ShortText] = Field(min_length=1, max_length=16)
    series: list[Series] = Field(min_length=1, max_length=5)
    color: Color = "#4C52D9"

    @model_validator(mode="after")
    def matching_series(self):
        if any(len(s.values) != len(self.categories) for s in self.series):
            raise ValueError("Chart series must match its categories")
        if self.chart == "pie" and (len(self.series) != 1 or any(v < 0 for v in self.series[0].values)):
            raise ValueError("Pie charts need one non-negative series")
        return self


class Group(ElementBase):
    type: Literal["group"] = "group"
    elements: list["Element"] = Field(min_length=1, max_length=50)


Element = Annotated[Text | Shape | Image | Line | Table | Chart | Group, Field(discriminator="type")]
Group.model_rebuild()


class Slide(Contract):
    id: Identifier = Field(default_factory=uid)
    title: ShortText
    layout: Layout = "summary"
    background: Color = "#F7F8FF"
    elements: list[Element] = Field(default_factory=list, max_length=100)
    notes: str = Field(default="", max_length=7000)
    source_ids: list[Identifier] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=30)

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
    sources: list[Source] = Field(default_factory=list, max_length=30)
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
    mode: Literal["professional", "creative", "guided"] = "professional"
    aspect_ratio: Aspect = "16:9"
    provider: Identifier | None = None
    model: str | None = Field(default=None, max_length=150)
    skill_id: Identifier | None = None
    asset_ids: list[Identifier] = Field(default_factory=list, max_length=15)
    research: bool = False
    generate_images: bool = False
    generate: bool = True
