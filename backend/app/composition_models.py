import re
from textwrap import shorten
from typing import Annotated, Literal
from pydantic import ConfigDict, Field, ValidationInfo, field_validator, model_validator
from pydantic_core import PydanticCustomError
from app.diagrams import Diagram, Swimlane
from app.domain import Contract, Layout, Recipe, Series, ShortText, display_value
from app.evidence import EvidenceObservation
from app.lucide import ICON_NAMES
from app.questions import Question
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
