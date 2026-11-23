"""Schema and declarative layout language used by the slide designer."""
from functools import lru_cache
from typing import Literal
import xml.etree.ElementTree as ET

from pydantic import Field, ValidationInfo, create_model, model_validator

from app.domain import Color, Contract, Identifier, Layout, WorkbookData
from app.evidence import EvidenceObservation
from app.visuals import VISUAL_MODELS, VisualType


class PagePlan(Contract):
    title: str = Field(min_length=1, max_length=160)
    layout: Layout = "summary"
    direction: str = Field(min_length=1, max_length=1800, description="Specific exhibit, composition and information to communicate")
    exhibit: VisualType | Literal["workbook-chart"] | None = None
    image_query: str = Field(default="", max_length=380)
    image_prompt: str = Field(default="", max_length=400)


class HtmlPlan(Contract):
    title: str = Field(min_length=1, max_length=160)
    background: Color
    ink: Color
    accent: Color
    muted: Color
    surface: Color
    display: Literal["Georgia", "Arial", "Aptos", "Consolas"]
    slides: list[PagePlan] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def requested_count(self, info: ValidationInfo):
        if info.context and len(self.slides) != info.context["slide_count"]:
            raise ValueError(f"Return exactly {info.context['slide_count']} slides")
        if (info.context or {}).get("requires_chart_workbook") and not any(s.exhibit == "workbook-chart" for s in self.slides):
            raise ValueError("Assign workbook-chart to a slide presenting the requested editable data series.")
        return self


class PageMarkup(Contract):
    source_ids: list[Identifier] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=3500)
    markup: str = Field(description="One XML page containing composed visual content", max_length=30000)


@lru_cache
def page_schema(exhibit):
    if not exhibit:
        return PageMarkup
    data_type = WorkbookData if exhibit == "workbook-chart" else VISUAL_MODELS[exhibit]
    return create_model("ExhibitPage", __base__=PageMarkup, exhibit_data=(data_type, ...),
                        illustrative=(bool, False), observations=(list[EvidenceObservation], Field(default_factory=list, max_length=96)))


PLAN = """Art-direct an editable slide deck from the supplied brief. Follow its requested count, aspect,
audience, wording, data, and slide order. Vary compositions and derive exhibit conclusions and calculations
from the supplied facts. Use the cover title requested. Select exhibit for structured figures, scorecards,
comparisons, schedules or diagrams so their inputs remain editable together. Use decision-matrix for a
weighted scorecard or weighted choice, heatmap for retention grids, waterfall for bridges, and workbook-chart for ordinary
bar, column, pie and line charts, especially when an editable chart workbook is requested. Leave exhibit
null for free layouts. Use image_query only for requested, enabled web photos and image_prompt only
for enabled generated imagery. Prefer available uploaded images. Choose legible contrasting colors.
Write each direction as an instruction to a slide designer, including what must be drawn."""

DESIGN = """You are the slide designer. Create ONLY the requested slide as XML in the markup property.
The XML compiles to HTML with CSS flex layout and browser-measured editable native PowerPoint objects.
Compose it directly from the brief, plan and slide number. Prefer a shallow flex layout with the main
exhibit directly under the page or a row with grow="1". Put the planned primary exhibit in an exhibit
frame. Matrix, waterfall, Gantt, Sankey and sequence primitives are also available for supplemental
visuals and free layouts. Draw custom geometry only when needed.
Include the headline, exhibit, direct labels, takeaway, and any required source note in the XML.
On covers preserve the requested title and tagline. Use concise, precise language and avoid filler.
Do not add slide metadata labels for decoration. All vector illustrations are built from native primitives.

Language (no other tags, CSS, HTML entities except XML built-ins, scripts or URLs):
<page bg="background" pad="42" gap="16">children</page>: vertical flex, full canvas 720px high. Default 36px padding.
<row gap="16" grow="1">...</row> horizontal flex. <col ...>...</col> vertical flex.
Containers and drawing panels can use grow=positive relative flex weight on parent main axis, w or h in
pixels or percentages, pad, gap, bg (opaque background exported as shape), justify=start|center|end|between,
align=start|center|end|stretch. Default cross-axis stretch. Allocate grow=1 to the principal exhibit.
Use <space grow="1"/> or <rule color="accent" h="2"/> sparingly.
For a planned exhibit, include precisely one <exhibit grow="1"/> where its data visualization belongs.
Return the matching exhibit_data separately. The renderer derives scores, dimensions and data labels.
Supply exact observations and cited source_ids for quantitative inputs. Set illustrative only for data
explicitly identified as illustrative. Leave room below the exhibit for the evidence footer.
Use <picture asset="uploaded-id" alt="Description" grow="1"/> for a relevant supplied picture.
Only use IDs in available_pictures. The original image retains its aspect ratio and is editable.
<text size="32" font="display" color="ink" bold="true" align="left">Text</text>. Size in CSS px, 12–100;
fonts display|body|mono. Newlines preserved. Usually headings 32–48, body 17–23, source 12–14.
Color values are theme tokens background|ink|accent|muted|surface or #RRGGBB. Choose readable contrast.
<matrix grow="1" widths="40,30,30" size="18"><tr><td bg="accent" color="#FFFFFF">...</td>...</tr>...</matrix>
is an editable fixed-column table, max 12 rows, 8 columns. Use distinct cell fills for heatmaps; widths
are optional percentages. Cells accept bg,color,bold,align. Include all labels and all source values.
<waterfall grow="1" unit="$" suffix="M" decimals="1"><bar label="..." value="10" kind="total"/>
<bar label="..." value="-2"/><bar label="..." value="8" kind="total"/></waterfall>: change bars add
signed values; endpoint totals are absolute. Use color option on individual bars for loss/gain.
<gantt grow="1" periods="12" unit="Week"><task label="..." start="1" end="2" milestone="..."/>...</gantt>:
inclusive periods, bars and milestone labels. Good for precise phased plans. Keep milestone labels short.
<sankey grow="1" unit="units"><flow from="..." to="..." value="..."/>...</sankey>: weighted acyclic
flow network with labeled editable ribbons and nodes. Use identical names for shared nodes.
<sequence grow="1" actors="Client|Server|Resource"><message from="Client" to="Server" label="..."/>...
</sequence>: participants, lifelines, directed message rows. Self messages supported. Keep labels concise.
<drawing grow="1"> is a free editable illustration, diagram or chart surface. Coordinates x,y,w,h and
x1,y1,x2,y2 are percentages (0–100) of THIS drawing. Children in paint order:
<box id="optional" x="5" y="10" w="30" h="15" bg="surface" form="rect"/> form rect|round|ellipse|diamond|triangle.
<label x="7" y="12" w="26" h="10" size="18" color="ink" align="center">...</label>, vertically centered,
or <arrow x1="10" y1="20" x2="45" y2="20" color="accent" head="true"/>; optional from="boxId:right"
to="otherId:left" for attachments (side top,left,bottom,right). For unheaded connectors use head="false".
<poly points="10,10 40,30 10,40" bg="accent"/> accepts 3–48 percentage points.
Use a drawing for architecture, branching processes, product silhouettes, wordmark art or custom exhibits.
Draw the device in detail when a product visual is required. Integrate exhibit and narrative; give the
largest region to the visual. Design within the canvas width in the request and 720px height. Prefer
different compositions across slides. Use only factual statements supported by the brief and supplied references. Cite used reference IDs in source_ids. Never imply that an option being evaluated is already in use. Keep the page under 100 editable objects. XML-escape
angle brackets and ampersands in text. Use slide numbers or section labels only when informative."""

CONTAINER_CHILDREN = "row col drawing text picture exhibit matrix waterfall gantt sankey sequence rule space"
TAGS = dict(page=CONTAINER_CHILDREN, row=CONTAINER_CHILDREN, col=CONTAINER_CHILDREN,
            drawing="box label arrow poly", matrix="tr", tr="td", waterfall="bar",
            gantt="task", sankey="flow", sequence="message")
TEXT = {"text","label","td"}


def parse_markup(value):
    value = value.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    if "<!" in value or "<?" in value:
        raise ValueError("Markup declarations are unsupported")
    root = ET.fromstring(value)
    if root.tag != "page" or len(list(root.iter())) > 300:
        raise ValueError("Expected one page with at most 300 nodes")
    def clean(node, depth):
        if depth > 12 or any(child.tag not in TAGS.get(node.tag, "").split() for child in node):
            raise ValueError(f"Unexpected nesting inside {node.tag}")
        if (node.tag not in TEXT and (node.text or "").strip()) or any((c.tail or "").strip() for c in node):
            raise ValueError("All visible copy must be in a text, label or td")
        if node.tag in TAGS and node.tag not in {"row","col"} and not len(node):
            raise ValueError(f"Empty {node.tag}")
        return {"tag":node.tag, "a":node.attrib, "t":(node.text or "").strip(),
                "children":[clean(c, depth + 1) for c in node]}
    return clean(root, 0)
