import re
from textwrap import shorten
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, ValidationInfo, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.branding import choose_layout, compose_branded
from app.consulting import CONSULTING_ICON_LAYOUTS, consulting_rules, consulting_theme, requested_consulting
from app.design import blend, readable, style_chart, style_slide_tables
from app.diagrams import Diagram, Swimlane, draw_diagram, draw_swimlane
from app.domain import (BrandTemplate, Chart, Contract, Document, Image, Layout, Recipe, Series, Shape, ShortText,
                        Slide, Table, Text, Theme, display_value)
from app.exhibit_layout import ExhibitCanvas, compose_exhibit
from app.evidence import EvidenceObservation
from app.exhibit_evidence import STATUS_KEY, UNVERIFIED, evidence_label, exhibit_bindings
from app.lucide import ICON_NAMES
from app.questions import Question
from app.rendering import contrast, fit_image, longest_word_width, physical, slide_icon, text_height
from app.visuals import Annotation, Copy, Decorations, VISUAL_LAYOUTS, Visual, VisualChoice


VISUAL_DIRECTION_LIMIT = 250


class Intent(Contract):
    subject: ShortText
    objective: str = Field(default="", max_length=700)
    audience: str = Field(default="", max_length=250)
    tone: str = Field(default="", max_length=250)
    visual_direction: str = Field(default="", max_length=VISUAL_DIRECTION_LIMIT, description=(
        "Give a short style label such as 'Strategy consulting'. The designer receives the full brief separately."
    ))
    requires_quantitative_evidence: bool = False
    requires_chart_workbook: bool = False
    slide_count: int | None = Field(default=None, ge=1, le=30)
    web_images_requested: bool = False
    reuse_uploaded_images: bool = False
    supplied_figures: bool = False
    supplied_evidence_only: bool = False
    supplied_phase_quantities: bool = False
    missing: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("visual_direction", mode="before")
    @classmethod
    def shorten_visual_direction(cls, value: object) -> object:
        if isinstance(value, str) and len(value) > VISUAL_DIRECTION_LIMIT:
            # Keep a usable prefix even for writing systems without spaces.
            return shorten(value, width=VISUAL_DIRECTION_LIMIT, placeholder="") or value.lstrip()[:VISUAL_DIRECTION_LIMIT]
        return value


class PlanningResponse(Contract):
    # Some providers add slide copy to the outline, even in JSON mode.
    model_config = ConfigDict(extra="ignore", json_schema_extra={"additionalProperties": False})


Exhibit = Literal["cover_callout", "stacked_comparison", "allocation", "rollout", "decision", "diagram", "swimlane", "readout", "visual"]
EXHIBIT_LAYOUTS: dict[Exhibit, Layout] = {
    "cover_callout": "cover", "stacked_comparison": "chart", "allocation": "metrics",
    "rollout": "timeline", "decision": "closing", "diagram": "process", "swimlane": "process", "readout": "metrics",
    "visual": "process",
}


class OutlineItem(PlanningResponse):
    title: ShortText
    topic: str = Field(default="", max_length=180, description="A neutral label for the subject.")
    key_message: str = Field(default="", max_length=140, description="The concise supported takeaway or recommendation.")
    slideIntent: str = Field(default="", max_length=100)
    mainMessage: str = Field(default="", max_length=140)
    visual: VisualChoice | None = None
    annotations: list[Copy] = Field(default_factory=list, max_length=3)
    lock_title: bool = Field(default=False, description="True only if the user supplies the exact slide title.")
    recipe: Recipe | None = Field(default=None, description="Optional preferred composition for the actual slide content.")
    visual_priority: str = Field(default="", max_length=180, description="The exhibit or takeaway the design should emphasize.")
    layout: Layout = Field(description="Use one exact layout ID from the enum. Use chart for a sourced chart and research for a data table.")
    purpose: str = Field(default="", max_length=500)
    required_details: list[ShortText] = Field(default_factory=list, max_length=8)
    point_count: int | None = Field(default=None, ge=1, le=3,
                                    description="An explicit brief requirement for one to three visible text points.")
    image_query: str = Field(default="", max_length=380)
    research_query: str = Field(default="", max_length=260)
    use_uploaded_image: bool = Field(default=False, description="This slide calls for an uploaded image.")
    exhibit: Exhibit | None = Field(default=None, description=(
        "Choose cover_callout for a cover with a specific decision amount, stacked_comparison for comparable "
        "non-negative totals with supplied components, allocation for an itemized budget, rollout for deployment "
        "phases, decision for approval with success gates and a stop rule, or diagram for connected entities, "
        "ordered steps, or alternative paths. Use readout for a closing with two or three exact, sourced result figures. "
        "Leave null for other slides."
    ))

    @model_validator(mode="before")
    @classmethod
    def accept_semantic_plan(cls, value):
        if not isinstance(value, dict) or not value.get("mainMessage"):
            return value
        result = dict(value)
        result.setdefault("title", result["mainMessage"])
        if not result.get("key_message") and not result.get("lock_title"):
            result["key_message"] = result["mainMessage"]
        visual = result.get("visual")
        if isinstance(visual, dict) and isinstance(visual.get("type"), str):
            result.setdefault("layout", VISUAL_LAYOUTS.get(visual.get("type"), "summary"))
        return result

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


def planned_exhibit(item: OutlineItem, has_evidence: bool, *, prefer_diagrams: bool = False,
                    supplied_breakdown: bool = False) -> Exhibit | None:
    description = f"{item.title} {item.purpose} {' '.join(item.required_details)}".lower()
    if item.recipe == "diagram_swimlane" or (
        item.layout in {"summary", "process", "comparison", "showcase", "editorial"}
        and re.search(r"\bswim[ -]?lanes?\b", description)
    ):
        return "swimlane"
    if item.exhibit and item.exhibit not in {"diagram", "visual"}:
        approval = r"\b(approve|approval|authorize|authorization|endorse|endorsement|funding request|request for funding)\b"
        if item.exhibit == "cover_callout" and not re.search(approval, f"{item.title} {item.purpose}", re.I):
            return None
        return item.exhibit if item.exhibit != "readout" or has_evidence else None
    # The outline may describe the right exhibit without selecting its field.
    if item.layout == "chart" and has_evidence and re.search(r"\b(costs?|expenses?)\b", description) and (
        supplied_breakdown or (
            re.search(r"\bcompar\w*", description)
            and re.search(r"\b(components?|breakdown|broken down)\b", description)
        )
    ):
        return "stacked_comparison"
    if item.layout in {"summary", "metrics", "research", "chart", "comparison"} and has_evidence and (
        re.search(r"\b(budget|spend|funding)\b", description)
        and re.search(r"\b(allocat\w*|line items?|breakdown|itemiz\w*|divided)\b", description)
    ):
        return "allocation"
    if item.layout in {"timeline", "process", "summary", "showcase"} and re.search(r"\b(timeline|rollout|deployment)\b", description) and (
        re.search(r"\b(risks?|mitigat\w*)\b", description)
    ):
        return "rollout"
    if item.layout in {"closing", "summary", "comparison", "metrics"} and (
        re.search(r"\b(gates?|go/no-go|success criteria|acceptance criteria)\b", description)
        and re.search(r"\b(approval|approve|decision|pause|stop rule)\b", description)
    ):
        return "decision"
    if (item.exhibit == "diagram" or prefer_diagrams) and item.layout in {"summary", "process", "comparison", "showcase", "editorial"}:
        return "diagram"
    if has_evidence and item.layout in {"closing", "summary", "metrics"} and re.search(r"\b(readout|recovery|key results)\b", description):
        return "readout"
    return None


class Point(Contract):
    title: Annotated[str, Field(min_length=1, max_length=65)]
    body: str = Field(default="", max_length=250)
    value: str = Field(default="", max_length=25)
    icon: str | None = Field(default=None, description=f"Optional Lucide icon. Use one of: {', '.join(ICON_NAMES)}.")

    @field_validator("icon", mode="before")
    @classmethod
    def known_icon(cls, value: object) -> str | None:
        if isinstance(value, str):
            name = value.strip().lower()
            if name in ICON_NAMES:
                return name
        return None


class Readout(Contract):
    metrics: list[Point] = Field(min_length=2, max_length=3)
    highlight: int | None = Field(default=None, ge=0, le=2, description="Index of the single primary metric, if any.")

    @model_validator(mode="after")
    def has_values(self):
        if any(not metric.value.strip() for metric in self.metrics):
            raise ValueError("Every readout metric needs a sourced value")
        if self.highlight is not None and self.highlight >= len(self.metrics):
            raise ValueError("The highlighted metric must exist")
        return self


class ChartContent(Contract):
    categories: list[ShortText] = Field(min_length=1, max_length=10)
    values: list[float] = Field(min_length=1, max_length=10)
    label: ShortText
    chart: Literal["column", "bar", "line", "pie"] = "column"
    highlight: int | None = Field(default=None, ge=0, description="Zero-based index of the one category that supports the slide takeaway.")
    compare_to: int | None = Field(default=None, ge=0, description="Zero-based index of a comparable baseline for the highlighted category.")
    value_prefix: str = Field(default="", max_length=8)
    value_suffix: str = Field(default="", max_length=8)

    def comparison_label(self, highlight: int | None) -> str:
        baseline = self.compare_to
        if highlight is None or baseline is None or baseline == highlight or baseline >= len(self.values):
            return ""
        change = round(self.values[highlight] - self.values[baseline], 2)
        if not change or len(self.categories[baseline]) > 22:
            return ""
        sign = "+" if change > 0 else "−"
        return f"{sign}{display_value(abs(change), self.value_prefix)}{self.value_suffix} vs {self.categories[baseline]}"


class StackedComparison(Contract):
    label: ShortText
    categories: list[ShortText] = Field(min_length=2, max_length=4)
    series: list[Series] = Field(min_length=2, max_length=4)
    value_prefix: str = Field(default="", max_length=8)
    caption: str = Field(default="", max_length=120)
    difference_label: str = Field(default="", max_length=65,
                                  description="What a lower total means, including its unit and period.")

    @model_validator(mode="after")
    def aligned_components(self):
        if any(len(s.values) != len(self.categories) or any(v < 0 for v in s.values) for s in self.series):
            raise ValueError("Provide a non-negative component value for each category in each series")
        return self


class AllocationLine(Contract):
    label: Annotated[str, Field(min_length=1, max_length=58)]
    amount: float = Field(ge=0)


class Allocation(Contract):
    value_prefix: str = Field(max_length=8, description="Currency symbol such as $ for supplied currency, otherwise empty.")
    lines: list[AllocationLine] = Field(min_length=2, max_length=8)


class Phase(Contract):
    period: Annotated[str, Field(min_length=1, max_length=30)]
    milestone: Annotated[str, Field(min_length=1, max_length=125, description="A short, complete milestone phrase.")]


class RiskMitigation(Contract):
    risk: Annotated[str, Field(min_length=1, max_length=50)]
    mitigation: Annotated[str, Field(min_length=1, max_length=100)]


class Rollout(Contract):
    phases: list[Phase] = Field(min_length=2, max_length=4)
    risks: list[RiskMitigation] = Field(default_factory=list, max_length=3)


class DecisionGate(Contract):
    label: Annotated[str, Field(min_length=1, max_length=48)]
    threshold: Annotated[str, Field(min_length=1, max_length=24)]
    detail: str = Field(default="", max_length=100)


class Decision(Contract):
    action: Literal["Approve", "Authorize", "Endorse"]
    request: Annotated[str, Field(min_length=1, max_length=110, description="Noun phrase for the commitment being approved, with any explicit cap.")]
    scope: str = Field(default="", max_length=135, description="Scope and limits of the current request, separate from future gates.")
    if_missed: Annotated[str, Field(min_length=1, max_length=130)]
    gates: list[DecisionGate] = Field(min_length=1, max_length=4)

    @field_validator("request", "scope")
    @classmethod
    def separate_future_gates(cls, value: str, info: ValidationInfo) -> str:
        if info.field_name == "request":
            value = re.sub(r"\s+(?:with|pending)\s+(?:(?:the|future|day[- ]?90)\s+)*"
                           r"(?:gating conditions|meeting the gates|gate clearance)\s*\.?$", "", value, flags=re.I)
        elif match := re.search(r"\s*[;,]\s*(?:success measured by|subject to the day[- ]?90 gates)\b", value, re.I):
            value = value[:match.start()]
        if re.search(r"\b(conditional on|contingent on|subject to|provided that)\b.*\bgates?\b", value, re.IGNORECASE):
            raise PydanticCustomError("decision_scope", "keep the future gate condition in if_missed")
        return value.strip().rstrip(".")


class CoverCallout(Contract):
    headline: Annotated[str, Field(min_length=1, max_length=66)]
    label: Annotated[str, Field(min_length=1, max_length=38)]
    value: Annotated[str, Field(min_length=1, max_length=25)]
    detail: str = Field(default="", max_length=95)


PHASE_PERIOD = re.compile(r"\b(?:day|week|month|quarter)s?\s*[-‑–—]?\s*\d+(?:\s*[-‑–—]\s*\d+)?", re.I)
PHASE_NUMBER = re.compile(r"\d|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
                          r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|"
                          r"fifty|sixty|seventy|eighty|ninety|hundred|thousand|million)\b", re.I)


class SlideContent(Contract):
    title: ShortText
    subtitle: str = Field(default="", max_length=220)
    points: list[Point] = Field(default_factory=list, max_length=5)
    notes: str = Field(default="", max_length=5000)
    evidence_warnings: list[ShortText] = Field(default_factory=list, max_length=6)
    source_ids: list[str] = Field(default_factory=list, max_length=12)
    image_asset_id: str | None = None
    image_prompt: str = Field(default="", max_length=400)
    chart: ChartContent | None = None
    table: list[list[str]] | None = Field(default=None, max_length=8)
    stacked_comparison: StackedComparison | None = None
    allocation: Allocation | None = None
    rollout: Rollout | None = None
    decision: Decision | None = None
    cover_callout: CoverCallout | None = None
    diagram: Diagram | None = None
    swimlane: Swimlane | None = None
    readout: Readout | None = None
    visual: Visual | None = None
    exhibit_observations: list[EvidenceObservation] = Field(default_factory=list, max_length=96)
    illustrative_exhibit: bool = False
    annotations: list[Copy | Annotation] = Field(default_factory=list, max_length=3)
    decorations: Decorations | None = None
    table_focus: list[ShortText] = Field(default_factory=list, max_length=4,
                                       description="Exact first-column labels for the selected or key data rows.")
    table_focus_columns: list[ShortText] = Field(default_factory=list, max_length=3,
                                                description="Exact headers of criteria that justify the selection in table_focus.")
    next_step: str = Field(default="", max_length=145)

    @model_validator(mode="before")
    @classmethod
    def omit_unused_generation_fields(cls, value, info: ValidationInfo):
        layout = (info.context or {}).get("layout")
        if not layout or not isinstance(value, dict):
            return value
        result = dict(value)
        if layout not in {"cover", "closing"} and not result.get("decision"):
            result.pop("next_step", None)
        if not any(result.get(field) is not None for field in ("chart", "visual", "stacked_comparison")):
            result.pop("exhibit_observations", None)
        return result

    @model_validator(mode="after")
    def planned_exhibit(self, info: ValidationInfo):
        context = info.context or {}
        point_count = context.get("point_count")
        if point_count and len(self.points) != point_count:
            raise PydanticCustomError("point_count", "include exactly {expected} visible text points", {"expected": point_count})
        exhibit = context.get("required_exhibit")
        if exhibit and getattr(self, exhibit) is None:
            raise PydanticCustomError("required_exhibit", "populate the {exhibit} object for this slide", {"exhibit": exhibit})
        choice = context.get("visual_choice")
        if self.visual and choice and (self.visual.type != choice["type"] or self.visual.variant != choice.get("variant")):
            raise PydanticCustomError("visual_choice", "use the planned visual type and variant", {})
        if self.visual and self.visual.variant and self.visual.type != "flow-diagram":
            raise ValueError("two-scenario-comparison is a flow-diagram variant")
        if self.rollout and context.get("omit_phase_quantities") and any(
            PHASE_NUMBER.search(PHASE_PERIOD.sub("", phase.milestone)) for phase in self.rollout.phases
        ):
            raise PydanticCustomError("phase_quantities", "describe tasks without allocating unprovided quantities to phases")
        return self

    @property
    def chart_ready(self) -> bool:
        return self.chart is not None and len(self.chart.values) == len(self.chart.categories)

    @property
    def table_ready(self) -> bool:
        rows = self.table
        return bool(rows and 1 <= len(rows[0]) <= 8 and all(len(row) == len(rows[0]) for row in rows))


class ResearchQueries(Contract):
    queries: list[ShortText] = Field(min_length=1, max_length=4)


class EvidenceSummary(PlanningResponse):
    summary: str = Field(max_length=2200)
    uncertainties: list[ShortText] = Field(default_factory=list, max_length=6)
    observations: list[EvidenceObservation] = Field(default_factory=list, max_length=64)


class ResearchBrief(Contract):
    summary: str = Field(max_length=1800)
    uncertainties: list[ShortText] = Field(default_factory=list, max_length=6)
    follow_up: Question | None = None
    observations: list[EvidenceObservation] = Field(default_factory=list, max_length=64)


class Review(Contract):
    issues: list[ShortText] = Field(default_factory=list, max_length=5)
    regenerate_content: bool = False


DISPLAY_LAYOUTS = frozenset({"cover", "section", "quote", "closing"})
IMAGE_LAYOUTS = frozenset({"showcase", "editorial", "research"})
STANDARD_ICON_LAYOUTS = frozenset({"summary", "comparison", "timeline", "process", "metrics", "showcase", "editorial"})


def supports_point_icons(layout: Layout, theme: Theme, brand: BrandTemplate | None) -> bool:
    layouts = CONSULTING_ICON_LAYOUTS if theme.profile == "consulting" else STANDARD_ICON_LAYOUTS
    return brand is None and layout in layouts


def accepts_web_image(item: OutlineItem, content: SlideContent, brand: BrandTemplate | None) -> bool:
    if item.layout not in IMAGE_LAYOUTS or content.chart or content.table or content.stacked_comparison or content.visual:
        return False
    if brand:
        return any(slot.kind == "picture" for slot in choose_layout(brand.layouts, item.layout).slots)
    return True


def is_data_driven(direction: str) -> bool:
    return bool(re.search(r"\bdata[\s_-]+(?:driven|visuali[sz]ations?)\b", direction, re.IGNORECASE))


def design(direction: str, mode: str, selected: Theme | None) -> Theme:
    direction = direction.lower()
    if requested_consulting(direction) or any(term in direction for term in
        ("strategy consulting", "consulting-style", "consulting style", "consulting deck", "management consulting")):
        return selected.model_copy(update={"profile": "consulting"}) if selected else consulting_theme()
    if selected:
        return selected
    if "editorial" in direction:
        return Theme(background="#F7F2E9", ink="#27213D", accent="#9D3C30", muted="#625968", surface="#EEE2D2", font="Georgia")
    if "academic" in direction:
        return Theme(background="#F0F8F5", ink="#16352F", accent="#08745D", muted="#395E54", surface="#DAEDE6")
    if is_data_driven(direction):
        return Theme(background="#F3F6FA", ink="#122239", accent="#0F6B62", muted="#41566B", surface="#E3EDF0")
    if mode == "creative" or "bold" in direction:
        return Theme(background="#F1ECFF", ink="#281852", accent="#8737AC", muted="#57416B", surface="#E6D4F9")
    return Theme()


def select_layout(item, content, document, images, *, data_driven, allow_variants):
    role = item.layout
    if allow_variants:
        if content.swimlane and role in {"summary", "process", "comparison", "showcase", "editorial"}:
            return "process"
        if role == "section" and content.points:
            return "summary"
        if content.decision and role in {"summary", "closing"}:
            return "closing"
        if content.rollout and role in {"process", "timeline"}:
            return "timeline"
        if content.allocation and role in {"chart", "metrics", "research"}:
            return "metrics"
        if content.stacked_comparison and role == "chart":
            return "chart"
    if role in DISPLAY_LAYOUTS:
        return role
    sourced = bool(set(content.source_ids).intersection(source.id for source in document.sources))
    chart_ready = sourced and content.chart_ready and len(content.chart.values) >= 2
    if (data_driven and sourced) or allow_variants:
        if chart_ready and not (role == "research" and content.table_ready):
            return "chart"
        if content.table_ready and (sourced or not any(re.search(r"\d", cell) for row in content.table for cell in row)):
            return "research"
    if allow_variants and role == "summary":
        if content.image_asset_id in images:
            return "editorial"
        if sourced and len(content.points) >= 2 and all(point.value for point in content.points):
            return "metrics"
        if document.slides and document.slides[-1].layout == role and len(content.points) == 2:
            return "comparison"
    return role


def compose(item: OutlineItem, content: SlideContent, document: Document, index: int, image_ids: set[str],
            brand: BrandTemplate | None = None, *, web_image_ratio: float | None = None,
            image_ratio: float | None = None, data_driven: bool = False, recipe: Recipe = "standard") -> Slide:
    if not brand and document.theme.profile == "consulting" and not document.theme.design:
        theme = document.theme.model_copy(update={"design": consulting_rules(document.theme)})
        document = document.model_copy(update={"theme": theme})
    source_ids = {source.id for source in document.sources}
    rules = document.theme.design
    if content.visual:
        return compose_visual_slide(item, content, document, brand, image_ids, source_ids, recipe)
    consulting = document.theme.profile == "consulting"
    if consulting and not brand and not content.table and not content.chart and not content.diagram and not content.swimlane and not (
        content.stacked_comparison or content.image_asset_id or recipe in {"feature_panels", "feature_rows"}
    ) and item.layout in {"comparison", "research"} and content.points:
        content = content.model_copy(update={"table": [["Observation", "Implication"],
            *[[point.title, point.body] for point in content.points]]})
    layout = select_layout(item, content, document, image_ids, data_driven=data_driven,
                           allow_variants=bool(rules and not brand))
    if consulting and not brand and content.table_ready and layout == "comparison":
        layout = "research"
    item = item.model_copy(update={"layout": layout})
    if brand:
        slide = compose_branded(item, content, document, image_ids, brand)
        return style_slide_tables(slide, document.theme, preserve_palette=True)
    t = document.theme
    compact = document.aspect_ratio == "1:1"
    factor = {"1:1": .68, "4:3": .83, "16:9": 1}[document.aspect_ratio]
    font_scale = max(.87, factor) if rules else factor
    ptw, pth = physical(document)
    editorial = t.font == "Georgia"
    dark = item.layout in DISPLAY_LAYOUTS and not editorial and not consulting
    bg = t.ink if dark else t.background
    ink, muted, accent = ("#FFFFFF", "#DDE1F5", "#E0DBFF") if dark else (t.ink, t.muted, t.accent)
    if rules:
        bg = rules.cover_background if item.layout in DISPLAY_LAYOUTS else t.background
        ink = readable(t.ink, bg, t.background)
        muted = readable(t.muted, bg, ink)
        accent = readable(t.accent, bg, ink)
    slide = Slide(title=item.title, background=bg, layout=item.layout, notes=content.notes,
                  source_ids=list(dict.fromkeys(s for s in content.source_ids if s in source_ids)))
    elements = slide.elements

    def rect(x, y, w, h, fill, shape="rect", *, element_key=None):
        elements.append(Shape(x=x, y=y, w=w, h=h, fill=fill, shape=shape, z=-1, element_key=element_key))

    def label(value, x, y, w, h, size, color=ink, bold=False, heading=False, align="left", element_key=None):
        if value.strip():
            face = rules.heading_font if rules and heading else t.font
            elements.append(Text(x=x, y=y, w=w, h=h, text=value, size=max(8, size * font_scale),
                                 color=color, bold=bold, font=face, align=align, element_key=element_key))

    def fitted_label(value, x, y, w, max_h, preferred, minimum, color, *, bold=False, heading=False,
                     prefer_one_line=False, keep_words=False, align="left", element_key=None):
        if not value.strip():
            return y
        face = rules.heading_font if rules and heading else t.font
        if prefer_one_line:
            preferred = next((size for size in range(preferred, max(minimum, round(.88 * preferred)) - 1, -1)
                              if text_height(value, w * ptw, size, bold, face) <= 1.35 * size), preferred)
        for size in range(preferred, minimum - 1, -1):
            height = (text_height(value, w * ptw, size, bold, face) + 4) / pth
            if height <= max_h and (not keep_words or longest_word_width(value, size, bold, face) <= .98 * w * ptw):
                break
        height = min(height, max_h)
        label(value, x, y, w, height, size / font_scale, color, bold, heading=heading, align=align, element_key=element_key)
        return y + height

    points = content.points
    if rules and item.layout == "cover" and content.image_asset_id in image_ids and (ratio := image_ratio or web_image_ratio):
        if compact:
            image = Image(x=.055, y=.33, w=.89, h=.61, asset_id=content.image_asset_id, alt=item.title)
            end = fitted_label(item.title, .07, .07, .86, .15, 48, 31, ink, heading=True)
            fitted_label(content.subtitle, .07, end + .014, .86, max(.06, .32 - end), 17, 13, muted)
        else:
            image = Image(x=.035, y=.045, w=.65, h=.91, asset_id=content.image_asset_id, alt=item.title)
            end = fitted_label(item.title, .719, .29, .245, .33, 64, 30, ink, heading=True)
            fitted_label(content.subtitle, .722, end + .028, .225, .22, 19, 14, muted)
        elements.append(fit_image(image, document, ratio))
        return slide

    if rules and item.layout == "cover" and content.cover_callout:
        callout = content.cover_callout
        panel_ink = readable("#FFFFFF", t.accent, t.ink)
        rect(.655, .22, .275, .53, t.accent)
        end = fitted_label(item.title, .07, .225, .55, .39, 54 if compact else 59, 32, ink, heading=True)
        fitted_label(content.subtitle, .07, end + .024, .55, .21, 19, 14, muted)
        fitted_label(callout.label, .677, .27, .232, .078, 15, 12, panel_ink, bold=True)
        end = fitted_label(callout.value, .677, .36, .232, .15, 43, 27, panel_ink, heading=True)
        fitted_label(callout.detail, .677, end + .027, .232, .21, 16, 12, panel_ink)
        return slide

    if rules and item.layout == "closing" and content.decision:
        decision = content.decision
        panel_ink = readable("#FFFFFF", t.accent, t.ink)
        end = fitted_label(item.title, .07, .095, .86, .18, 36, 28, ink, heading=True)
        fitted_label(decision.if_missed, .07, end + .015, .86, max(.055, .347 - end), 17, 13, muted)
        step = .86 / len(decision.gates)
        for i, gate in enumerate(decision.gates):
            x = .07 + i * step
            rect(x, .375, step - .022, .004, accent)
            threshold, measure = gate.threshold, ""
            if mixed_threshold := re.match(r"^([<>≤≥=]*\s*\d+(?:\.\d+)?\s*%)\s+(.+)$", threshold):
                threshold, measure = mixed_threshold.groups()
            fitted_label(threshold, x, .391, step - .024, .09, 33, 24, accent, heading=True)
            label(gate.label, x, .485, step - .024, .104, 16, ink, True)
            detail = "\n".join(part for part in (measure[:1].upper() + measure[1:], gate.detail) if part)
            fitted_label(detail, x, .589, step - .024, .10, 14, 11, muted)
        rect(.07, .71, .86, .24, t.accent)
        end = fitted_label(f"{decision.action} {decision.request}.", .09, .727, .82, .105, 26, 18, panel_ink, heading=True)
        scope_y = end + .009
        fitted_label(decision.scope, .09, scope_y, .82, .934 - scope_y, 15, 12, panel_ink)
        return slide

    if rules and item.layout in {"cover", "closing"}:
        centered = recipe == "closing_centered"
        align = "center" if centered else "left"
        if item.layout == "cover":
            if recipe == "editorial_cover" and rules.cover_style == "rules":
                rect(.07, .165, .86, .003, accent)
                rect(.07, .858, .86, .003, accent)
            else:
                rect(.07, .202, .075, .004, accent)
        elif centered:
            rect(.463, .212, .074, .003, accent)
            rect(.20, .80, .60, .002, blend(bg, ink, .34))
        display = recipe in {"closing_display", "closing_centered"}
        end = fitted_label(item.title, .12 if centered else .07, .29 if centered else .24, .76 if centered else .86,
                           .40, (60 if compact else 78) if display else (54 if compact else 62), 32, ink,
                           heading=True, align=align)
        end = fitted_label(content.next_step or content.subtitle, .12 if centered else .07, end + .023,
                           .76 if centered else .75, .15, 20, 14, muted, align=align)
        if item.layout == "closing" and not content.next_step:
            visible = [point for point in points[:3] if point.title.casefold() not in {
                item.title.casefold(), (content.next_step or content.subtitle).casefold()
            }]
            if visible:
                width = .86 / len(visible)
                for i, point in enumerate(visible):
                    label(point.title, .07 + i * width, min(.813, end + .034), width - .015, .102, 17, ink, True)
        return slide

    if not rules:
        label(f"{index + 1:02d}  /  {item.layout.upper()}", .068, .065, .65, .045, 12, accent, True)
        rect(.068, .137, .048, .008, accent)
    if item.layout in DISPLAY_LAYOUTS:
        if rules and rules.cover_style == "rules":
            rect(.07, .196, .86, .003, t.accent)
            rect(.07, .90, .86, .003, t.accent)
        elif (rules and rules.cover_style == "panel") or (not rules and editorial):
            rect(.76, .125, .172, .59, t.accent)
            rect(.788, .20, .113, .008, readable(t.muted, t.accent) if rules else "#E5B4A8")
            rect(.788, .53, .113, .008, readable(t.muted, t.accent) if rules else "#E5B4A8")
            if not rules:
                label(f"{index + 1:02d}", .788, .33, .12, .14, 38, "#FFFFFF", True)
        else:
            rect(.77, .09, .19, .32, t.accent, "ellipse")
            rect(.82, .28, .135, .24, t.muted, "ellipse")
        if rules:
            end = fitted_label(item.title, .068, .25, .86 if rules.cover_style == "rules" else .66,
                               .31, 52, 30, ink, heading=True)
            fitted_label(content.subtitle, .07, end + .032, .68, .19, 21, 15, muted)
        else:
            label(item.title, .068, .24, .66, .28, 47, ink, True, heading=True)
            label(content.subtitle, .07, .58, .64 if editorial else .70, .13, 23, muted)
        if points and item.layout == "closing":
            for i, point in enumerate(points[:3]):
                label(point.title, .07 + .295 * i, .77, .274, .13, 18, ink, True)
        elif points and item.layout == "quote":
            label(points[0].title, .07, .77, .65, .08, 18, muted)
        return slide

    body_top = .445
    if rules:
        end = fitted_label(item.title, .07, .095, .86, .20, 34 if compact else 38, 27, ink,
                           bold=rules.heading_font.casefold() == t.font.casefold(), heading=True, prefer_one_line=True)
        if content.subtitle:
            end = fitted_label(content.subtitle, .07, end + .018, .86, .112, 18, 14, muted)
        body_top = max(.252, min(.455, end + .035))
    else:
        label(item.title, .068, .17, .87, .17, 35, ink, True, heading=True)
        label(content.subtitle, .07, .352, .86, .08, 17, muted)
    if rules and recipe == "diagram_swimlane" and content.swimlane:
        draw_swimlane(elements, content.swimlane, body_top, t, bg, fitted_label)
        return slide
    if rules and content.diagram and item.layout in {"process", "summary", "comparison", "showcase"} and (
            recipe != "standard" or not points):
        draw_diagram(elements, content.diagram, body_top, t, bg, fitted_label, compact=compact,
                     vertical_flow=recipe == "diagram_vertical")
        return slide
    if rules and points and recipe in {"feature_panels", "feature_rows"}:
        panel = t.surface if abs(contrast(t.surface, bg) - 1) >= .16 else blend(bg, t.accent, .14)
        panel_ink = readable(ink, panel, t.ink)
        body_ink = readable(muted, panel, panel_ink)
        panel_accent = readable(accent, panel, panel_ink, minimum=3)
        for index, point in enumerate(points[:3]):
            name = point.title.strip().casefold()
            if sum(other.title.strip().casefold() == name for other in points) > 1:
                name = f"{name}:{point.body.strip().casefold()}"
            identity = f"feature:{name}"
            horizontal = recipe == "feature_panels"
            gap = .018
            step = (.86 + gap) / min(len(points), 3)
            row_step = (.914 - body_top) / min(len(points), 3)
            x, y = (.07 + index * step, body_top + .015) if horizontal else (.07, body_top + index * row_step)
            w, h = (step - gap, .89 - y) if horizontal else (.86, row_step - .012)
            rect(x, y, w, h, panel, element_key=f"{identity}:background")
            rect(x, y, w if horizontal else .005, .006 if horizontal else h, panel_accent, element_key=f"{identity}:accent")
            prefix = rf"^(?:Step\s+0?{index + 1}|0?{index + 1})\s*[:.–-]\s*"
            title = re.sub(prefix, "", point.title, flags=re.I)
            if horizontal:
                icon_y = y + .044
                if point.icon:
                    icon = slide_icon(document, point.icon, x + .02, icon_y, min(.062, h * .14), panel)
                    icon.element_key = f"{identity}:icon"
                    elements.append(icon)
                end = fitted_label(title, x + .02, y + h * .28, w - .04, h * .31,
                                   28, 17, panel_ink, bold=True, heading=True, element_key=f"{identity}:heading")
                fitted_label(point.body, x + .02, end + .027, w - .04, y + h - end - .042, 16, 13, body_ink,
                             element_key=f"{identity}:body")
            else:
                split = .31
                fitted_label(title, x + .024, y + .024, split, max(.05, h - .037),
                             23, 14, panel_ink, bold=True, heading=True, element_key=f"{identity}:heading")
                fitted_label(point.body, x + split + .06, y + .024, w - split - .084, max(.05, h - .037),
                             18, 13, body_ink, element_key=f"{identity}:body")
        return slide
    first_body_element = len(elements)
    use_icons = item.layout in STANDARD_ICON_LAYOUTS and not (content.chart or content.table or content.stacked_comparison)
    if rules and item.layout == "chart" and content.stacked_comparison and slide.source_ids:
        data = content.stacked_comparison
        totals = [sum(component.values[index] for component in data.series) for index in range(len(data.categories))]
        large_enough = len(totals) == 2 and len(data.series) <= 3 and max(totals) > 0 and all(
            value >= .105 * max(totals) for component in data.series for value in component.values
        )
        chart = Chart(x=.07, y=.517, w=.55, h=.373, chart="stacked_column", categories=data.categories,
                      series=data.series, value_prefix=data.value_prefix, show_values=large_enough)
        elements.append(chart)
        label(data.label, .07, .449, .55, .057, 16, ink, True)
        rows = [["Component", *data.categories]]
        for component in data.series:
            rows.append([component.name, *[display_value(value, data.value_prefix) for value in component.values]])
        if len(totals) == 2 and totals[0] != totals[1]:
            low = min(range(2), key=totals.__getitem__)
            rect(.646, .504, .284, .185, t.surface)
            value_ink = readable(t.accent, t.surface, ink)
            fitted_label(display_value(abs(totals[0] - totals[1]), data.value_prefix), .663, .518, .25, .083,
                         35, 21, value_ink, heading=True)
            fitted_label(data.difference_label or f"Lower for {data.categories[low]}", .663, .606, .25, .066,
                         15, 11, readable(t.ink, t.surface))
            if large_enough:
                style_chart(chart, t, bg)
                for index, component in enumerate(data.series):
                    y = .716 + .057 * index
                    palette = chart.colors()
                    rect(.647, y + .006, .010, .018, palette[index % len(palette)])
                    fitted_label(component.name, .668, y, .25, .052, 14, 10, muted)
            else:
                elements.append(Table(x=.646, y=.707, w=.284, h=.181, rows=rows, size=max(9, 12 * factor)))
        else:
            elements.append(Table(x=.646, y=.54, w=.284, h=.089 * len(rows), rows=rows, size=max(9, 13 * factor)))
        label(data.caption, .07, .913, .86, .048, 13, muted)
    elif rules and item.layout == "metrics" and content.allocation:
        allocation = content.allocation
        amount = sum(line.amount for line in allocation.lines)
        total = display_value(amount, allocation.value_prefix)
        palette = list(dict.fromkeys(color.upper() for color in (accent, *rules.chart_colors, ink, muted)))
        if amount:
            x = .07
            for index, line in enumerate(allocation.lines):
                width = .86 * (line.amount / amount)
                if width > 0:
                    elements.append(Shape(x=x, y=.452, w=width, h=.052, fill=palette[index % len(palette)],
                                          border=bg, z=-1))
                    x += width
        label(total, .07, .52, .32, .15, 39, accent, True, heading=True)
        label("Total allocation", .07, .684, .32, .07, 16, muted)
        step = .38 / len(allocation.lines)
        for i, line in enumerate(allocation.lines):
            y = .526 + i * step
            rect(.414, y, .516, .003, t.surface)
            rect(.397, y + .020, .009, .015, palette[i % len(palette)])
            size = min(18, max(11, step * 207))
            label(line.label, .414, y + .009, .326, step - .011, size, ink, True)
            label(display_value(line.amount, allocation.value_prefix), .747, y + .009,
                  .183, step - .011, size, ink, True, align="right")
    elif rules and item.layout == "metrics" and content.readout and slide.source_ids:
        readout = content.readout
        step = .86 / len(readout.metrics)
        for index, metric in enumerate(readout.metrics):
            x = .07 + step * index
            lead = index == readout.highlight
            width = step - .022
            rect(x, .463, width, .004, accent if lead else blend(bg, ink, .30))
            fitted_label(metric.title, x, .492, width, .075, 16, 12, ink, bold=True)
            fitted_label(metric.value, x, .586, width, .131, 42 if lead else 35, 23, accent if lead else ink, heading=True)
            fitted_label(metric.body, x, .75, width, .124, 15, 12, muted)
    elif rules and item.layout == "timeline" and content.rollout:
        rollout = content.rollout
        phase_ink = readable(ink, t.surface, t.ink, bg)
        step = .86 / len(rollout.phases)
        for i, phase in enumerate(rollout.phases):
            x = .07 + i * step
            rect(x, .473, step - .009, .168, t.surface)
            rect(x, .473, step - .009, .005, t.accent)
            label(phase.period, x + .012, .491, step - .033, .053, 15, phase_ink, True)
            label(phase.milestone, x + .012, .548, step - .033, .071, 16, phase_ink)
        if rollout.risks:
            label("Risk", .07, .676, .248, .04, 13, muted, True)
            label("Mitigation", .338, .676, .59, .04, 13, muted, True)
            for i, pair in enumerate(rollout.risks):
                y = .725 + i * .075
                rect(.07, y, .86, .002, t.surface)
                label(pair.risk, .07, y + .008, .25, .065, 15, ink, True)
                label(pair.mitigation, .338, y + .008, .592, .065, 15, ink)
    elif item.layout == "research" and content.table_ready:
        rows = [[cell[:180] for cell in row] for row in content.table]
        focus = {value.strip().casefold() for value in content.table_focus[:2]} if rules else set()
        selected = [i for i, row in enumerate(rows[1:], 1) if row[0].strip().casefold() in focus]
        criteria = {value.strip().casefold() for value in content.table_focus_columns}
        focus_columns = [i for i, header in enumerate(content.table[0][1:], 1)
                         if header.strip().casefold() in criteria] if selected else []
        if len(selected) == len(rows) - 1 and not focus_columns:
            selected = []
        full = recipe == "dense_table_full"
        elements.append(Table(x=.055 if full else .07, y=.446 if full else .46, w=.89 if full else .86,
                              h=.50 if full else .44, rows=rows, color=t.ink, fill=t.surface,
                              focus_rows=selected, focus_columns=focus_columns, element_key="exhibit:table",
                              header_fill=accent if consulting else None,
                              header_color=readable("#FFFFFF", accent) if consulting else None))
    elif content.image_asset_id in image_ids and item.layout in IMAGE_LAYOUTS:
        image = Image(x=.55, y=.45, w=.38, h=.44, asset_id=content.image_asset_id, alt=item.title)
        if web_image_ratio or image_ratio:
            elements.append(image)
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
    elif item.layout == "chart" and slide.source_ids and content.chart_ready:
        data = content.chart
        direct_labels = bool(rules and data.chart != "pie" and len(data.values) <= 6)
        has_external_source = any(source.url and source.id in slide.source_ids for source in document.sources)
        has_table = data_driven and (not direct_labels or has_external_source)
        highlight = data.highlight if direct_labels and data.highlight is not None and data.highlight < len(data.values) else None
        callout = data.comparison_label(highlight)
        chart_width = .54 if has_table else .86
        show_caption = data_driven or bool(callout)
        try:
            elements.append(Chart(x=.07, y=.52 if callout else .51 if show_caption else .45, w=chart_width,
                                  h=.39 if show_caption else .46, categories=data.categories, show_values=direct_labels,
                                  highlight=highlight, series=[{"name": data.label, "values": data.values}],
                                  chart=data.chart, color=t.accent, value_prefix=data.value_prefix, value_suffix=data.value_suffix,
                                  element_key="exhibit:chart"))
        except ValueError:
            slide.warnings.append("The chart data could not be rendered.")
        else:
            if show_caption:
                caption_width = chart_width * .58 if callout else chart_width
                fitted_label(data.label, .07, .45, caption_width, .066, 15, 10, ink)
            if callout:
                fitted_label(callout, .08 + caption_width, .443, chart_width - caption_width - .01,
                             .073, 20, 11, accent, bold=True, align="right")
            if has_table:
                rows = [["Category", "Value"], *[[category, f"{display_value(value, data.value_prefix)}{data.value_suffix}"]
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
            offset = 0
            if use_icons and point.icon:
                icon = slide_icon(document, point.icon, x, .464, .047, bg)
                elements.append(icon)
                offset = icon.w + .008
            label(point.value or f"0{i + 1}", x + offset, .465, width - .024 - offset, .047, 14, accent, True)
            label(point.title, x, .605, width - .024, .095, 20, ink, True)
            label(point.body, x, .72, width - .024, .18, 16, muted)
    elif rules and item.layout == "process" and points and (
        compact or len(points) > 3 or any(len(point.title) > 38 or len(point.body) > 85 for point in points)
    ):
        visible = points[:4]
        row_height = .48 / len(visible)
        for i, point in enumerate(visible):
            y = .45 + i * row_height
            with_icon = bool(use_icons and point.icon)
            label(f"{i + 1:02}", .07, y + .008, .050 if with_icon else .09,
                  row_height - .012, 23 if with_icon else 27, accent, True, heading=True)
            if with_icon:
                elements.append(slide_icon(document, point.icon, .124, y + .011, .040, bg))
            if i:
                rect(.07, y, .86, .002, t.surface)
            title = re.sub(rf"^Step\s+0?{i + 1}\s*[:.–-]\s*", "", point.title, flags=re.IGNORECASE)
            end = fitted_label(title or point.title, .18, y + .008, .75, row_height * .40, 21, 15, ink, bold=True)
            fitted_label(point.body, .18, end + .002, .75, max(.025, y + row_height - end - .008), 16, 13, muted)
    elif item.layout in {"comparison", "metrics", "process", "showcase"} and points:
        points = points[:2 if item.layout == "comparison" and not rules else 3]
        gap = {"compact": .012, "regular": .022, "spacious": .032}[rules.spacing] if rules else .022
        width = (.864 - gap * (len(points) - 1)) / len(points)
        card_ink = readable(t.ink, t.surface) if rules else ink
        card_muted = readable(t.muted, t.surface, card_ink) if rules else muted
        card_accent = readable(t.accent, t.surface, card_ink) if rules else accent
        for i, point in enumerate(points):
            x = .068 + i * (width + gap)
            if rules and item.layout == "comparison":
                rect(x, .463, width, .004, accent)
            else:
                rect(x, .463, width, .426, t.surface, rules.card_shape if rules else "round")
            value = point.value if slide.source_ids or not any(character.isdigit() for character in point.value) else ""
            offset = 0
            if use_icons and point.icon:
                icon = slide_icon(document, point.icon, x + .018, .503, .062,
                                  bg if rules and item.layout == "comparison" else t.surface)
                elements.append(icon)
                offset = icon.w + .012
            if not value:
                value = f"0{i + 1}" if item.layout == "process" else ""
            label(value, x + .018 + offset, .495, width - .036 - offset, .083, 25, card_accent, True)
            if rules and item.layout == "comparison":
                end = fitted_label(point.title, x + .018, .575 if offset or value else .493,
                                   width - .036, .139, 20, 15, ink, bold=True)
                fitted_label(point.body, x + .018, end + .012, width - .036, .94 - end - .012, 16, 13, muted)
            else:
                label(point.title, x + .018, .596, width - .036, .09, 18, card_ink, True)
                label(point.body, x + .018, .70, width - .036, .157, 16 if not compact else 15, card_muted)
    else:
        icon_column = use_icons and any(point.icon for point in points[:3])
        text_x = .125 if icon_column else .103
        text_width = .913 - text_x
        for i, point in enumerate(points[:3]):
            y = .46 + .146 * i
            if icon_column and point.icon:
                elements.append(slide_icon(document, point.icon, .068, y + .008, .047, bg))
            else:
                rect(.07, y + .021, .014, .025, t.accent, "ellipse")
            label(point.title, text_x, y, text_width, .062, 20, ink, True)
            label(point.body, text_x, y + .063, text_width, .075, 16, muted)
    if rules:
        scale = (.955 - body_top) / (.955 - .445)
        for element in elements[first_body_element:]:
            element.y = body_top + (element.y - .445) * scale
            element.h *= scale
            if isinstance(element, Chart):
                style_chart(element, t, bg)
            if isinstance(element, Image):
                ratio = (image_ratio or web_image_ratio) if element.asset_id == content.image_asset_id else 1
                if ratio:
                    fitted = fit_image(element, document, ratio)
                    element.x, element.y, element.w, element.h = fitted.x, fitted.y, fitted.w, fitted.h
    if len(points) > 3:
        remainder = "\n".join(f"{p.title}: {p.body}" for p in points[3:])
        slide.notes = (slide.notes + "\n" + remainder).strip()[:7000]
    return style_slide_tables(slide, t, preserve_palette=consulting)


def compose_visual_slide(item, content, document, brand, image_ids, source_ids, recipe):
    role = VISUAL_LAYOUTS[content.visual.type]
    heading = item.title.strip() or content.title.strip() or "Untitled slide"
    source_ids = list(dict.fromkeys(key for key in content.source_ids if key in source_ids))
    slide = Slide(title=heading, layout=role, background=document.theme.background,
                  notes=content.notes, source_ids=source_ids)
    frame = ExhibitCanvas(document, slide, (.06, .055, .88, .89))
    if brand:
        header = content.model_copy(update={"points": [], "subtitle": "", "chart": None, "table": None,
                                            "image_asset_id": None})
        slide = compose_branded(item.model_copy(update={"layout": role, "title": heading}), header, document,
                                image_ids, brand)
        layout = next(layout for layout in brand.layouts if layout.index == slide.brand_layout_index)
        slot = max((slot for slot in layout.slots if slot.kind in {"body", "chart", "table"}),
                   key=lambda slot: slot.w * slot.h, default=None)
        top = max([.23, *(element.y + element.h + .02 for element in slide.elements)])
        slot_top = max(top, slot.y) if slot else top
        if slot and slot.w >= .45 and slot.y + slot.h - slot_top >= .36:
            x, top, w, bottom = slot.x, slot_top, slot.w, slot.y + slot.h
        else:
            before_heading = min((element.y for element in slide.elements), default=0) - .025
            top, bottom = (.055, before_heading) if before_heading - .055 > .945 - top else (min(top, .90), .945)
            x, w = .06, .88
        frame = ExhibitCanvas(document, slide, (x, top, w, bottom - top))
    else:
        frame.label(heading, 0, 0, 1, .184, 35, bold=True, emphasis=content.decorations.emphasis if content.decorations else ())
        heading_box = slide.elements[-1]
        top = max(.17, heading_box.y + heading_box.h + .042)
        frame = ExhibitCanvas(document, slide, (.06, top, .88, .945 - top))
    if content.subtitle:
        frame.label(content.subtitle, 0, 0, 1, .115, 15, color=frame.muted)
    start = .13 if content.subtitle else 0
    frame.line((0, start), (1, start), color=frame.rule, element_key="exhibit:header-rule")
    bounds = (frame.x, frame.y + frame.h * (start + .025), frame.w, frame.h * (1 - start - .025))
    status = evidence_label(exhibit_bindings(content, document.sources, slide.source_ids,
                                             planned_annotations=item.annotations))
    if status == UNVERIFIED and not slide.source_ids:
        slide.warnings.append("Quantitative exhibit omitted because no source was cited.")
        frame.label("Source data needed", 0, start + .18, 1, .18, 20, color=frame.muted,
                    element_key="exhibit:missing-data")
        return slide
    if status == UNVERIFIED:
        slide.warnings.append("Some exhibit values lack matching source observations.")
    if status:
        frame.label(status, 0, .956, 1, .044, 11, color=frame.ink, bold=True, element_key=STATUS_KEY)
        bounds = (*bounds[:3], bounds[3] - frame.h * .06)
    compose_exhibit(document, slide, content, bounds, annotations=content.annotations,
                    planned_annotations=item.annotations, recipe=recipe)
    return slide
