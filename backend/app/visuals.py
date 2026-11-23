"""Coordinate-free exhibit contracts shared by planning and slide generation."""

import re
from typing import Annotated, Generic, Literal, TypeVar, get_args

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from app.diagrams import Diagram, Swimlane
from app.domain import Contract, Identifier, Layout

Label = Annotated[str, Field(min_length=1, max_length=64)]
Copy = Annotated[str, Field(min_length=1, max_length=140)]
Number = Annotated[float, Field(ge=-1e15, le=1e15)]
Positive = Annotated[Number, Field(gt=0)]
Index = Annotated[int, Field(ge=0, le=11)]

QuantitativeType = Literal["ranked-bar", "waterfall", "stacked-bar", "mekko", "funnel", "slope", "tornado", "pareto"]
MatrixType = Literal["heatmap", "decision-matrix", "scoring-matrix", "raci", "capability-map"]
ScatterType = Literal["two-by-two", "bubble-chart"]
GraphType = Literal["architecture", "data-flow", "flowchart", "state-machine", "pipeline", "er-diagram",
                    "network-topology", "issue-tree", "driver-tree", "organization-chart", "value-chain",
                    "layered-stack", "sankey"]
TimeType = Literal["roadmap", "gantt", "timing-diagram"]
VisualType = QuantitativeType | MatrixType | ScatterType | GraphType | TimeType | Literal[
    "sequence-diagram", "venn", "annotated-code", "flow-diagram", "swimlane"]

NUMERIC_VISUAL_TYPES = {*get_args(QuantitativeType), *get_args(MatrixType), *get_args(ScatterType), *get_args(TimeType), "sankey"}

VISUAL_LAYOUTS: dict[VisualType, Layout] = {
    **dict.fromkeys(get_args(QuantitativeType) + get_args(ScatterType), "chart"),
    **dict.fromkeys(get_args(MatrixType), "research"),
    **dict.fromkeys(get_args(GraphType) + ("sequence-diagram", "venn", "annotated-code", "flow-diagram", "swimlane"), "process"),
    **dict.fromkeys(get_args(TimeType), "timeline"),
}

SELECTION_GUIDANCE = (
    " Use slideIntent for the relationship or analytical task and mainMessage for the supported action headline. "
    "Select visual.type by intent: compare values=ranked-bar, reconcile totals=waterfall, composition=stacked-bar "
    "or mekko (width encodes another measure), evaluate/recommend=decision-matrix or scoring-matrix, "
    "prioritize=two-by-two or bubble-chart, explain causes=driver-tree or issue-tree, implementation=roadmap or gantt. "
    "Use funnel for attrition, sankey for weighted flows, slope for two periods, tornado for sensitivity, "
    "pareto for cumulative contribution, heatmap for a comparable scale, venn for set overlap, and raci for accountability. "
    "For technical relationships use architecture or network-topology, requests over time=sequence-diagram, "
    "branch logic=flowchart, movement/transformation=data-flow, lifecycle=state-machine, dependencies=pipeline, "
    "responsibility lanes=swimlane, entity cardinality=er-diagram, software layers=layered-stack, latency=timing-diagram, "
    "implementation details=annotated-code. Use value-chain, capability-map and organization-chart for their named purposes. "
    "Use flow-diagram with variant two-scenario-comparison for two alternative paths to compare. "
    "Honor explicitly requested exhibits. Set visual to null if no structured exhibit serves the slide. "
    "Pick quantitative exhibits only when their inputs are available in evidence or explicitly supplied illustrative data. "
    "Keep exact user-provided titles in title with lock_title true. Do not specify slide geometry."
)

CONTENT_GUIDANCE = (
    " Populate visual with its requested type and variant and actual exhibit data. "
    "Use semantic values, relationships and time buckets; never give slide coordinates. "
    "Include units, axes, scale meaning, legend labels, and conditions where applicable. "
    "For ranked bars, funnels, Pareto charts and waterfalls, include the exact source metric name in metric. "
    "Use separate units for bubble axes and size when they differ. In a Mekko, unit describes widths and segment_unit "
    "describes the composition inputs. Use column_units for matrix criteria with different units. "
    "For a qualitative two-by-two, place each label in a named quadrant without assigning scores. "
    "Use decorations only when meaningful: kicker with an optional icon, key_metric, delta, status (0–4), "
    "a highlight_band matching a row or period, callout or footnote. "
    "Annotation targets are exact exhibit labels or code line numbers, not coordinates. "
    "Use graph edges for real dependencies and label them with data, trigger, or cardinality as appropriate. "
    "Keep the stage, node, criteria and point labels short. Cite supplied source IDs for quantitative inputs."
)


class VisualChoice(Contract):
    type: VisualType
    variant: Literal["two-scenario-comparison"] | None = None

    @model_validator(mode="after")
    def valid_variant(self):
        if self.variant and self.type != "flow-diagram":
            raise ValueError("two-scenario-comparison is a flow-diagram variant")
        return self


class Annotation(Contract):
    text: Copy
    target: Label | None = None


class Decorations(Contract):
    kicker: str = Field(default="", max_length=64)
    icon: str = Field(default="", max_length=40, description="Optional Lucide icon name paired with the kicker.")
    key_metric: str = Field(default="", max_length=64)
    delta: str = Field(default="", max_length=64)
    status: int | None = Field(default=None, ge=0, le=4)
    status_label: str = Field(default="", max_length=64)
    callout: str = Field(default="", max_length=140)
    footnote: str = Field(default="", max_length=140)
    highlight_band: Label | None = None
    emphasis: list[Label] = Field(default_factory=list, max_length=4)


class VisualBase(Contract):
    variant: Literal["two-scenario-comparison"] | None = None
    unit: str = Field(default="", max_length=22)


class MetricRow(Contract):
    label: Label
    values: list[Number] = Field(min_length=1, max_length=4)
    width: Positive | None = Field(default=None, description="Independent category size for Mekko column width.")


class QuantitativeVisual(VisualBase):
    type: QuantitativeType
    metric: str = Field(default="", max_length=80, description="Source metric name for a single-value series.")
    segment_unit: str = Field(default="", max_length=22, description="Unit of the inputs used to calculate Mekko shares.")
    rows: list[MetricRow] = Field(min_length=2, max_length=8)
    series: list[Label] = Field(default_factory=list, max_length=4)
    highlight: Label | None = None
    start_label: Label = "Start"
    end_label: Label = "End"
    baseline: Number | None = Field(default=None, description="Waterfall opening total or tornado reference value.")

    @model_validator(mode="after")
    def valid_data(self):
        kind = self.type
        width = len(self.rows[0].values)
        expected = 2 if kind in {"slope", "tornado"} else len(self.series) if kind in {"stacked-bar", "mekko"} else 1
        if width != expected or any(len(row.values) != width for row in self.rows):
            raise ValueError("values must match the series or required inputs for this chart")
        if kind in {"slope", "tornado"} and len(self.series) not in {0, 2}:
            raise ValueError("slope and tornado use two series labels")
        if kind in {"stacked-bar", "mekko"} and (width < 2 or any(v < 0 for row in self.rows for v in row.values)):
            raise ValueError("composition requires at least two nonnegative series")
        if kind == "mekko" and any(row.width is None or sum(row.values) <= 0 for row in self.rows):
            raise ValueError("each Mekko category needs a positive width and total")
        if kind in {"waterfall", "tornado"} and self.baseline is None:
            raise ValueError("waterfall needs a starting total and tornado needs a baseline")
        if kind == "tornado" and any(lo > self.baseline or hi < self.baseline for lo, hi in (r.values for r in self.rows)):
            raise ValueError("each tornado interval must bracket the baseline in low-to-high order")
        if kind in {"funnel", "pareto"}:
            values = [row.values[0] for row in self.rows]
            if any(v < 0 for v in values) or max(values) <= 0:
                raise ValueError("funnel and Pareto require nonnegative values with a positive maximum")
            if kind == "funnel" and values != sorted(values, reverse=True):
                raise ValueError("funnel stages must follow the process in descending order")
        if self.highlight and self.highlight not in {row.label for row in self.rows}:
            raise ValueError("highlight must match a category label")
        return self


Cell = TypeVar("Cell")
ScaleLabel = Annotated[str, Field(max_length=64)]


class MatrixRow(Contract, Generic[Cell]):
    label: Label
    cells: list[Cell] = Field(min_length=1, max_length=6, description="One value per column, in column order.")


class MatrixGrid(VisualBase, Generic[Cell]):
    columns: list[Label] = Field(min_length=1, max_length=6)
    column_units: list[Annotated[str, Field(max_length=22)]] = Field(
        default_factory=list, max_length=6, description="Leave empty or supply one unit per column.")
    rows: list[MatrixRow[Cell]] = Field(min_length=1, max_length=8)
    recommended: Label | None = None


class NumericHeatmap(MatrixGrid[Number]):
    """Use for measured values or numeric ratings such as a 1–5 maturity score. Supply JSON numbers in cells."""

    type: Literal["heatmap"]
    variant: None = None
    scale_low: ScaleLabel = ""
    scale_high: ScaleLabel = ""


class CategoricalHeatmap(MatrixGrid[Label]):
    """Use for ordered text categories. Each cell must exactly match one of the supplied levels."""

    type: Literal["heatmap"]
    variant: None = None
    levels: list[Label] = Field(min_length=2, max_length=6, description="Distinct categories from lowest to highest.")


class MatrixVisual(MatrixGrid[Label | Number]):
    type: MatrixType
    weights: list[Positive] = Field(default_factory=list, max_length=6)
    scale_low: ScaleLabel = ""
    scale_high: ScaleLabel = ""
    levels: list[Label] = Field(default_factory=list, max_length=6,
                               description="Heatmap categories from lowest to highest, when the scale is qualitative.")

    @model_validator(mode="after")
    def valid_matrix(self):
        if any(len(row.cells) != len(self.columns) for row in self.rows):
            raise PydanticCustomError("matrix_data", "give every row one cell per column in column order")
        if self.column_units and len(self.column_units) != len(self.columns):
            raise PydanticCustomError("matrix_data", "leave column_units empty or supply one unit per column")
        if self.weights and self.type not in {"decision-matrix", "scoring-matrix"}:
            if self.type == "heatmap":
                raise PydanticCustomError("matrix_data", "omit weights for a heatmap. Use numeric cells without levels "
                                          "for scores, or text cells matching ordered levels for categories")
            raise PydanticCustomError("matrix_data", "omit weights for this visual type")
        if self.weights and (len(self.weights) != len(self.columns) or len(self.columns) == 6):
            raise PydanticCustomError("matrix_data", "supply one weight per criterion and at most five weighted criteria")
        cells = [cell for row in self.rows for cell in row.cells]
        if self.levels:
            if self.type != "heatmap":
                raise PydanticCustomError("matrix_data", "omit levels for this visual type")
            if (len(set(self.levels)) < 2 or len(set(self.levels)) != len(self.levels)
                    or any(cell not in self.levels for cell in cells)):
                raise PydanticCustomError("matrix_data", "for numeric scores use JSON numbers and omit levels. "
                                          "for categorical heatmaps use distinct ordered levels covering every text cell")
        elif (self.type in {"heatmap", "scoring-matrix"} or self.weights) and any(isinstance(cell, str) for cell in cells):
            raise PydanticCustomError("matrix_data", "use JSON numbers in cells for numeric scores")
        if self.recommended and self.recommended not in {row.label for row in self.rows}:
            raise PydanticCustomError("matrix_data", "set recommended to an exact row label or null")
        return self


class PlotPoint(Contract):
    label: Label
    horizontal: Number | None = None
    vertical: Number | None = None
    quadrant: Literal["upper-left", "upper-right", "lower-left", "lower-right"] | None = None
    size: Positive | None = None


class ScatterVisual(VisualBase):
    type: ScatterType
    horizontal_label: Label
    vertical_label: Label
    horizontal_unit: str | None = Field(default=None, max_length=22)
    vertical_unit: str | None = Field(default=None, max_length=22)
    size_unit: str | None = Field(default=None, max_length=22)
    points: list[PlotPoint] = Field(min_length=1, max_length=8)
    horizontal_split: Number | None = None
    vertical_split: Number | None = None
    size_label: str = Field(default="", max_length=64)
    quadrants: list[Label] = Field(default_factory=list, max_length=4,
                                  description="Quadrant labels in reading order, from top left.")
    highlight: Label | None = None

    @model_validator(mode="after")
    def valid_plot(self):
        if self.quadrants and len(self.quadrants) != 4:
            raise ValueError("supply four quadrant labels in reading order")
        if self.highlight and self.highlight not in {point.label for point in self.points}:
            raise ValueError("highlight must match a point label")
        if any(point.quadrant for point in self.points):
            if self.type != "two-by-two" or any(not point.quadrant or point.horizontal is not None or
                                                point.vertical is not None or point.size is not None for point in self.points):
                raise ValueError("qualitative two-by-two points use named quadrants for every item")
            return self
        if any(point.horizontal is None or point.vertical is None for point in self.points):
            raise ValueError("quantitative plots need a value on both axes")
        if self.type == "two-by-two" and (self.horizontal_split is None or self.vertical_split is None):
            raise ValueError("two-by-two requires both decision thresholds")
        if self.type == "bubble-chart" and (not self.size_label or any(p.size is None for p in self.points)):
            raise ValueError("bubble charts require a named size metric and positive sizes")
        return self


class GraphNode(Contract):
    id: Identifier
    label: Label
    detail: str = Field(default="", max_length=96)
    group: str = Field(default="", max_length=48)
    role: Literal["step", "decision", "start", "end", "store", "service"] = "step"
    fields: list[Label] = Field(default_factory=list, max_length=5)
    icon: str = Field(default="", max_length=40)


class GraphEdge(Contract):
    source: Identifier
    target: Identifier
    label: str = Field(default="", max_length=38)
    weight: Positive | None = None


class GraphVisual(VisualBase):
    type: GraphType
    nodes: list[GraphNode] = Field(min_length=2, max_length=10)
    edges: list[GraphEdge] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def valid_graph(self):
        ids = {node.id for node in self.nodes}
        if len(ids) != len(self.nodes) or any(e.source not in ids or e.target not in ids for e in self.edges):
            raise ValueError("edges must reference unique node IDs")
        if len({node.group for node in self.nodes if node.group}) > 4 or sum(e.source == e.target for e in self.edges) > 2:
            raise ValueError("use up to four named groups and two self-transitions per exhibit")
        if self.type != "layered-stack" and not self.edges:
            raise ValueError("supply the relationships between nodes")
        if self.type == "sankey" and any(not edge.weight or edge.source == edge.target for edge in self.edges):
            raise ValueError("Sankey flows need positive weights and distinct endpoints")
        if self.type == "er-diagram" and (any(not node.fields for node in self.nodes) or any(not edge.label for edge in self.edges)):
            raise ValueError("ER diagrams need entity fields and relationship cardinality labels")
        if self.type in {"pipeline", "sankey", "issue-tree", "driver-tree", "organization-chart"}:
            successors = {key: {edge.target for edge in self.edges if edge.source == key} for key in ids}
            while successors:
                leaves = {key for key, targets in successors.items() if not targets}
                if not leaves:
                    raise ValueError("these exhibits require acyclic relationships")
                successors = {key: targets - leaves for key, targets in successors.items() if key not in leaves}
        return self


class ScheduleItem(Contract):
    id: Identifier
    label: Label
    start: Index
    end: Index
    kind: Literal["task", "phase", "milestone", "gate"] = "task"
    predecessors: list[Identifier] = Field(default_factory=list, max_length=3)


class ScheduleVisual(VisualBase):
    type: TimeType
    periods: list[Label] = Field(min_length=2, max_length=12)
    boundaries: list[Number] = Field(default_factory=list, max_length=13,
                                     description="Optional time values at each period boundary, including the final endpoint.")
    items: list[ScheduleItem] = Field(min_length=1, max_length=9)

    @model_validator(mode="after")
    def valid_schedule(self):
        if self.boundaries and (len(self.boundaries) != len(self.periods) + 1 or
                                any(a >= b for a, b in zip(self.boundaries, self.boundaries[1:]))):
            raise ValueError("supply increasing boundaries with one more value than periods")
        items = {item.id: item for item in self.items}
        if len(items) != len(self.items):
            raise ValueError("schedule items need unique IDs")
        for item in self.items:
            if not item.start <= item.end < len(self.periods) or (
                item.kind in {"milestone", "gate"} and item.start != item.end
            ):
                raise ValueError("item dates must reference ordered periods; gates and milestones use a single period")
            if any(key not in items or key == item.id or items[key].end > item.start for key in item.predecessors):
                raise ValueError("dependencies must reference other items ending at or before the start")
        for item in self.items:
            pending, visited = list(item.predecessors), set()
            while pending:
                key = pending.pop()
                if key == item.id:
                    raise ValueError("schedule dependencies cannot contain cycles")
                if key not in visited:
                    visited.add(key)
                    pending.extend(items[key].predecessors)
        return self


class Message(Contract):
    source: Index
    target: Index
    label: Label
    kind: Literal["call", "return"] = "call"


class SequenceVisual(VisualBase):
    type: Literal["sequence-diagram"]
    participants: list[Label] = Field(min_length=2, max_length=5)
    messages: list[Message] = Field(min_length=1, max_length=9)

    @model_validator(mode="after")
    def valid_messages(self):
        if any(max(m.source, m.target) >= len(self.participants) or m.source == m.target for m in self.messages):
            raise ValueError("messages must connect two different named participants")
        return self


class VennVisual(VisualBase):
    type: Literal["venn"]
    left: Label
    right: Label
    left_only: str = Field(default="", max_length=64)
    right_only: str = Field(default="", max_length=64)
    intersection: Label


class CodeLine(Contract):
    code: str = Field(max_length=90)
    highlight: bool = False


class CodeVisual(VisualBase):
    type: Literal["annotated-code"]
    language: Label
    lines: list[CodeLine] = Field(min_length=1, max_length=12)


class FlowVisual(VisualBase):
    type: Literal["flow-diagram"]
    diagram: Diagram

    @model_validator(mode="after")
    def valid_comparison(self):
        if self.variant == "two-scenario-comparison" and (
            len(self.diagram.lanes) != 2 or any(not lane.title for lane in self.diagram.lanes)
        ):
            raise ValueError("two-scenario comparisons need two named lanes")
        return self


class SwimlaneVisual(VisualBase):
    type: Literal["swimlane"]
    swimlane: Swimlane


VisualUnion = (QuantitativeVisual | MatrixVisual | ScatterVisual | GraphVisual | ScheduleVisual |
               SequenceVisual | VennVisual | CodeVisual | FlowVisual | SwimlaneVisual)
Visual = Annotated[VisualUnion, Field(discriminator="type")]
VISUAL_MODELS = {key: model for model in get_args(VisualUnion)
                 for key in (get_args(model.model_fields["type"].annotation) or ())}

EXPLICIT_NAMES = {
    "ranked-bar": r"ranked bars?(?: charts?)?",
    "waterfall": r"waterfall(?: charts?)?|(?:profit|cost|revenue|variance) bridge|bridge chart",
    "stacked-bar": r"stacked bars?(?: charts?)?",
    "mekko": r"(?:mari)?mekko(?: charts?)?",
    "two-by-two": r"(?:two[- ]by[- ]two|2\s*[×x]\s*2) matri(?:x|ces)",
    "decision-matrix": r"decision matri(?:x|ces)",
    "scoring-matrix": r"scoring matri(?:x|ces)",
    "pipeline": r"dag diagram|(?:ci/cd|etl|inference) pipeline diagram",
    "state-machine": r"state (?:machine|diagram)",
    "raci": r"raci matri(?:x|ces)",
    "slope": r"slope (?:graphs?|charts?)",
    "tornado": r"(?:sensitivity|tornado) (?:charts?|diagrams?)",
    "pareto": r"pareto (?:charts?|diagrams?)",
    "flow-diagram": r"flow diagram|two[- ]scenario comparison",
    "organization-chart": r"(?:org|organi[sz]ation(?:al)?) charts?",
    "issue-tree": r"issue trees?",
    "driver-tree": r"driver trees?",
}


def named_visual(text: str) -> VisualChoice | None:
    for kind in VISUAL_LAYOUTS:
        pattern = EXPLICIT_NAMES.get(kind, re.escape(kind).replace(r"\-", "[- ]") + "s?")
        if re.search(rf"\b(?:{pattern})\b", text, re.I):
            variant = "two-scenario-comparison" if kind == "flow-diagram" and re.search(
                r"two[- ]scenario|two paths", text, re.I
            ) else None
            return VisualChoice(type=kind, variant=variant)
    return None
