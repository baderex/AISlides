import io

from PIL import Image
from pptx import Presentation
from pypdf import PdfReader
from pytest import mark, raises

from app.composition import OutlineItem, SlideContent
from app.design import enforce_constraints, explicit_constraints
from app.domain import BrandKit, Chart, DesignRules, Document, Shape, Source, Table, Text, TextSpan, Theme
from app.edits import ChangeText, ChatRequest, EditProposal, Restyle, RestyleDirection, apply, propose
from app.evidence import EvidenceObservation, resolve_headline, verified_observations
from app.powerpoint import to_pptx
from app.providers import Intelligence
from app.rendering import contrast, preview, to_pdf
from app.slide_design import LayoutReview, build_slide, review_layouts


def themed_document(**kwargs):
    return Document(title="Review", theme=Theme(design=DesignRules(
        audience="Readers", purpose="Inform", industry="General", mood="Editorial", brand_constraints="",
        visual_references="", imagery="None", heading_font="Georgia", spacing="regular", card_shape="rect",
        cover_style="rules", cover_background="#F7F6F0", dense_surface="#F5F1EB", dense_ink="#191817",
        chart_colors=["#253B4D", "#8F541D", "#1C6050"],
    )), **kwargs)


def test_cost_headline_reconciles_values_and_derives_the_rate():
    evidence = (
        "Monthly running cost per vehicle: diesel $1,060 ($620 fuel, $440 maintenance), "
        "electric $680 ($380 energy, $300 maintenance). Illustrative figures."
    )
    plan = OutlineItem(title="Cost comparison", layout="chart", key_message="Electric vans are 67% cheaper")
    slide = SlideContent(title=plan.title, stacked_comparison={
        "label": "Monthly running cost", "categories": ["Diesel", "Electric"],
        "series": [{"name": "Fuel or energy", "values": [380, 620]}, {"name": "Maintenance", "values": [300, 440]}],
    })
    resolved = resolve_headline(plan, slide, evidence, [])
    assert resolved.title == "Electric operating costs are 36% lower"
    assert resolved.topic == "Cost comparison"
    assert [item.values for item in slide.stacked_comparison.series] == [[620, 380], [440, 300]]


def test_quarterly_chart_and_signed_recovery_use_supplied_evidence():
    evidence = (
        "Illustrative results in USD millions. Revenue by quarter: Q1 30, Q2 34, Q3 31, Q4 38. "
        "Columns: Metric, Q1, Q2, Q3, Q4 and rows: Operating profit 2, 2, -1, 4; "
        "Cash and equivalents 19, 20, 18, 20."
    )
    plan = OutlineItem(title="Quarterly revenue trends", layout="chart")
    revenue = SlideContent(title=plan.title, chart={
        "label": "Revenue", "chart": "line", "categories": ["Q1", "Q2", "Q3", "Q4"], "values": [30, 34, 31, 83],
    })
    source = Source(id="brief", title="Brief", excerpt=evidence)
    revenue.source_ids = [source.id]
    plan = resolve_headline(plan, revenue, evidence, [source])
    assert plan.title == "Revenue rebounds to $38m in Q4"
    assert revenue.chart.values == [30, 34, 31, 38]
    deck = themed_document(sources=[source])
    slide = build_slide(plan, revenue, deck, 0, set(), data_driven=True)
    chart = next(element for element in slide.elements if isinstance(element, Chart))
    assert chart.highlight == 3
    assert chart.mark_color(3) != chart.mark_color(2)
    deck.slides = [slide]
    ppt = Presentation(io.BytesIO(to_pptx(deck, lambda _: None)))
    native = next(shape.chart for shape in ppt.slides[0].shapes if shape.has_chart)
    assert list(native.series[0].values) == [30, 34, 31, 38]
    assert native.plots[0].data_labels.show_value
    assert native.series[0].points[3].marker.size > native.series[0].points[2].marker.size
    assert any(shape.has_text_frame and shape.text == "+$7m vs Q3" for shape in ppt.slides[0].shapes)
    assert "+$7m vs Q3" in PdfReader(io.BytesIO(to_pdf(deck, lambda _: None))).pages[0].extract_text()
    plan = OutlineItem(title="Financial readout", layout="metrics")
    results = SlideContent(title=plan.title, readout={"metrics": [
        {"title": "Q3 Operating Profit", "value": "$1m"},
        {"title": "Q4 Operating Profit", "value": "$4m"},
        {"title": "Q4 Cash and Equivalents", "value": "$20m", "body": "Cash held steady."},
    ]})
    assert resolve_headline(plan, results, evidence, []).title == "Operating profit rebounds from −$1m to $4m"
    assert results.readout.metrics[0].value == "−$1m"
    assert results.readout.metrics[2].body == "Up from $18m in Q3."


@mark.parametrize("kind", ["bar", "column"])
def test_sparse_comparison_charts_export_native_labels_and_highlight(kind):
    source = Source(id="brief", title="Brief")
    plan = OutlineItem(title="Compare operating results", layout="chart")
    content = SlideContent(title=plan.title, source_ids=[source.id], chart={
        "label": "Operating results", "chart": kind, "categories": ["North", "South"], "values": [4, -2],
        "highlight": 1, "compare_to": 0,
    })
    deck = themed_document(sources=[source])
    deck.slides = [build_slide(plan, content, deck, 0, set(), data_driven=True)]
    chart = next(element for element in deck.slides[0].elements if isinstance(element, Chart))
    ppt = Presentation(io.BytesIO(to_pptx(deck, lambda _: None)))
    native = next(shape.chart for shape in ppt.slides[0].shapes if shape.has_chart)
    assert native.plots[0].data_labels.show_value
    assert list(native.series[0].values) == [4, -2]
    assert str(native.series[0].points[1].format.fill.fore_color.rgb) == chart.color[1:]
    assert str(native.series[0].points[0].format.fill.fore_color.rgb) == chart.context_color[1:]
    assert any(shape.has_text_frame and shape.text == "−6 vs North" for shape in ppt.slides[0].shapes)


def test_site_selection_emphasizes_only_the_selected_rows_and_criteria():
    source = Source(id="brief", title="Pilot brief", excerpt="Plan six vans each at North and Central.")
    rows = [
        ["Site", "Daily route miles", "Eligible vans", "Spare charging ports"],
        ["North", "72", "8", "4"],
        ["Central", "86", "6", "3"],
        ["South", "118", "2", "0"],
    ]
    plan = OutlineItem(title="Compare depots", layout="research")
    content = SlideContent(title=plan.title, source_ids=[source.id], table=rows,
                           table_focus_columns=["Eligible vans", "Spare charging ports"])
    plan = resolve_headline(plan, content, source.excerpt, [source])
    deck = themed_document(sources=[source])
    deck.slides = [build_slide(plan, content, deck, 0, set(), data_driven=True)]
    table = next(element for element in deck.slides[0].elements if isinstance(element, Table))
    assert table.rows == rows
    assert table.focus_rows == [1, 2] and table.focus_columns == [2, 3]
    assert table.focus_cell_fill != table.focus_fill != table.fill
    assert contrast(table.focus_ink, table.focus_cell_fill) >= 4.5

    ppt = Presentation(io.BytesIO(to_pptx(deck, lambda _: None)))
    native = next(shape.table for shape in ppt.slides[0].shapes if shape.has_table)
    assert [[native.cell(i, j).text for j in range(4)] for i in range(4)] == rows
    for i in range(1, 4):
        for j in range(1, 4):
            cell = native.cell(i, j)
            highlighted = i in (1, 2) and j in (2, 3)
            assert cell.text_frame.paragraphs[0].font.bold == highlighted
            assert (str(cell.fill.fore_color.rgb) == table.focus_cell_fill[1:]) == highlighted


def test_unsupported_numerical_headline_falls_back_and_explicit_title_is_preserved():
    plan = OutlineItem(title="Profitability", layout="summary", key_message="Profit was $5m")
    draft = SlideContent(title=plan.title)
    assert resolve_headline(plan, draft, "Profit was -$5m.", []).title == "Profitability"
    plan = OutlineItem(title="Five years in review", layout="summary", lock_title=True, key_message="A different title")
    assert resolve_headline(plan, draft, 'Slide titled "Five years in review".', []).title == plan.title


def test_explicit_theme_constraints_apply_to_both_backgrounds_and_fonts():
    deck = themed_document()
    deck.theme.background = "#4C52D9"
    deck.theme.design.cover_background = "#252341"
    constraints = explicit_constraints(
        "Use a restrained light financial-report style. Use Georgia for headings and Arial for body text. Accent #1A4354."
    )
    updated = enforce_constraints(deck.theme, constraints)
    assert updated.background == updated.design.cover_background == "#F7F5EF"
    assert updated.accent == "#1A4354"
    assert updated.design.heading_font == "Georgia" and updated.font == "Arial"
    assert updated.constraints.background_mode == "light"


def test_recipe_changes_preserve_text_and_deletion_with_new_geometry():
    deck = themed_document()
    part = OutlineItem(title="Three product features", topic="Features", layout="editorial")
    content = SlideContent(title=part.title, points=[
        {"title": "Warm beam", "body": "Dimmable light."}, {"title": "Charging", "body": "USB-C."},
        {"title": "Handle", "body": "A fold-flat loop."},
    ])
    slide = build_slide(part, content, deck, 0, set())
    assert slide.spec.recipe == "feature_panels"
    deck.slides = [slide]
    changed = next(e for e in slide.elements if isinstance(e, Text) and e.text == "USB-C.")
    removed = next(e for e in slide.elements if isinstance(e, Text) and e.text == "A fold-flat loop.")
    slide.elements.remove(removed)
    card = next(e for e in slide.elements if e.element_key == "feature:charging:background")
    card.fill, card.edited = "#704010", True
    changed.x -= .01
    original_pos = changed.x
    deck = apply(deck, EditProposal(summary="Edit", operations=[
        ChangeText(op="change_text", slide_id=slide.id, element_id=changed.id, text="USB-C charging."),
    ]))
    theme = deck.theme.model_copy(deep=True)
    theme.design.spacing = "compact"
    transformed = apply(deck, EditProposal(summary="Redesign", operations=[Restyle(op="restyle", theme=theme)]))
    result = transformed.slides[0]
    assert result.spec.recipe == "feature_rows"
    assert result.spec.content == slide.spec.content
    assert result.id == slide.id
    assert removed.id not in {e.id for e in result.elements}
    edited = next(e for e in result.elements if e.id == changed.id)
    assert edited.edited and edited.text == "USB-C charging." and edited.x != original_pos
    rebuilt_card = next(e for e in result.elements if e.id == card.id)
    assert rebuilt_card.fill == "#704010" and rebuilt_card.w > card.w
    assert next(e for e in result.elements if e.element_key == "feature:handle:background").fill != card.fill


def test_equal_fit_candidates_use_the_visual_rubric():
    deck = themed_document()
    part = OutlineItem(title="Product capabilities", layout="editorial", visual_priority="Charging")
    content = SlideContent(title=part.title, points=[
        {"title": "Charging", "body": "A USB-C port."}, {"title": "Lighting", "body": "Adjustable brightness."},
    ])
    initial = build_slide(part, content, deck, 0, set())
    calls = []

    def evaluate(plan, recipes, image):
        assert plan.visual_priority == "Charging" and image.startswith(b"\x89PNG")
        calls.append(recipes)
        return LayoutReview(ratings=[{"candidate": i + 1, **dict.fromkeys(
            ("hierarchy", "emphasis", "balance", "imagery", "style_match"), 5 if name == "feature_rows" else 1)}
            for i, name in enumerate(recipes)])

    selected, _ = review_layouts(deck, initial, 0, set(), None, evaluate=evaluate)
    assert calls and selected.spec.recipe == "feature_rows"


def test_structured_source_facts_reconcile_annual_values_and_flag_conflicts():
    plan = OutlineItem(title="Revenue by year", topic="Revenue", layout="chart")
    content = SlideContent(title=plan.title, chart={"label": "Revenue", "categories": ["FY2024", "FY2025"],
                                                   "values": [15, 23], "chart": "line"})
    source = Source(id="ledger", title="Ledger", excerpt="Revenue FY2024: 12.5. Revenue FY2025: 15.25. USD millions.")
    reported = Source(id="report", title="Operating report", excerpt="Revenue FY2025: 17.25. USD millions.")
    facts = [EvidenceObservation(source_id=source.id, metric="Revenue", period=period, amount=amount,
                                 quote=quote, currency="$", unit="m") for period, amount, quote in [
        ("FY2024", "12.5", "Revenue FY2024: 12.5."), ("FY2025", "15.25", "Revenue FY2025: 15.25."),
    ]]
    invalid = facts[0].model_copy(update={"period": "FY2025", "quote": source.excerpt})
    assert list(verified_observations([invalid], [source])) == []
    resolve_headline(plan, content, "Show reported revenue.", [source], observations=facts)
    assert content.chart.values == [12.5, 15.25]
    assert (content.chart.value_prefix, content.chart.value_suffix, content.source_ids) == ("$", "m", ["ledger"])
    conflict = facts[1].model_copy(update={"source_id": reported.id, "amount": facts[1].amount + 2, "quote": reported.excerpt})
    resolve_headline(plan, content, "Show reported revenue.", [source, reported], observations=[*facts, conflict])
    assert content.chart.values == [12.5, 15.25]
    assert "Conflicting source values" in content.subtitle
    assert content.evidence_warnings
    assert content.source_ids == ["ledger", "report"]


def test_brand_kit_protects_selected_roles_during_restyle():
    deck = themed_document()
    deck.theme.brand_kit = BrandKit(protected=["accent", "heading_font"])
    previous = deck.theme.model_copy(deep=True)
    revised = previous.model_copy(deep=True)
    revised.accent, revised.background = "#444444", "#222222"
    revised.design.heading_font, revised.font = "Verdana", "Calibri"
    proposal = EditProposal(summary="Refresh", operations=[Restyle(op="restyle", theme=revised)])
    result = apply(deck, proposal).theme
    assert result.accent == previous.accent and result.design.heading_font == previous.design.heading_font
    assert result.background == "#222222" and result.font == "Calibri"
    with raises(ValueError, match="PowerPoint master"):
        apply(deck, proposal, locked_theme=True)


def test_candidate_review_prefers_connected_diagram_to_plain_text_grid():
    deck = themed_document(aspect_ratio="1:1")
    part = OutlineItem(title="How a request moves", recipe="standard", layout="comparison")
    content = SlideContent(title=part.title, points=[
        {"title": "First step", "body": "The browser looks for the existing response."},
        {"title": "Second step", "body": "The server responds when contacted."},
    ], diagram={"lanes": [{"nodes": [
        {"label": "Browser", "role": "actor"}, {"label": "Server", "role": "service", "two_way": True},
    ]}]})
    draft = build_slide(part, content, deck, 0, set())
    assert draft.spec.recipe == "standard"
    result, png = review_layouts(deck, draft, 0, set(), None)
    assert result.spec.recipe == "diagram_flow"
    assert png.startswith(b"\x89PNG")
    connector = next(el for el in result.elements if el.type == "line")
    assert {connector.start_anchor.shape_id, connector.end_anchor.shape_id} <= {
        el.id for el in result.elements if isinstance(el, Shape)
    }
    deck.slides = [result]
    native = Presentation(io.BytesIO(to_pptx(deck, None)))
    assert b"<a:stCxn" in native.slides[0]._element.xml.encode()
    assert b"<a:endCxn" in native.slides[0]._element.xml.encode()


def test_styled_text_preserves_one_native_object_and_visible_copy():
    deck = themed_document()
    part = OutlineItem(title="Savings are 36% higher", key_message="Savings are 36% higher", layout="comparison")
    slide = build_slide(part, SlideContent(title=part.title), deck, 0, set())
    deck.slides = [slide]
    text = next(e for e in slide.elements if isinstance(e, Text))
    assert text.text[text.spans[0].start:text.spans[0].end] == "36%"
    png = preview(deck, slide, None)
    assert Image.open(io.BytesIO(png)).width > 0
    pdf = PdfReader(io.BytesIO(to_pdf(deck, None)))
    assert text.text in pdf.pages[0].extract_text()
    native = Presentation(io.BytesIO(to_pptx(deck, None)))
    heading, = [sh for sh in native.slides[0].shapes if sh.has_text_frame and sh.text]
    assert heading.text == text.text
    assert any(run.text == "36%" and run.font.bold for run in heading.text_frame.paragraphs[0].runs)
    with raises(ValueError, match="non-overlapping"):
        Text(text="ab", x=0, y=0, w=.1, h=.1, spans=[TextSpan(start=0, end=1), TextSpan(start=0, end=2)])


def test_named_network_actors_and_optional_response_survive_composition():
    brief = ("Browser requests a resource, browser receives the response and cache instructions, "
             "browser stores the response when permitted. The server may return 304 Not Modified during revalidation.")
    part = OutlineItem(title="Request and response", layout="process")
    content = SlideContent(title=part.title, diagram={"lanes": [{"nodes": [
        {"label": "Browser Requests Resource"},
        {"label": "Receives Response", "detail": "Includes Cache-Control"},
        {"label": "Stores Response if Allowed"},
    ]}], "recap": "Only store the response when permitted. Revalidation requests may get "})
    resolve_headline(part, content, brief, [])
    nodes = content.diagram.lanes[0].nodes
    assert [node.label for node in nodes[:2]] == ["Browser", "Server"]
    assert nodes[1].two_way and nodes[1].incoming == "Request / response"
    assert content.diagram.recap == "Only store the response when permitted."
    nodes[1].label = "Server Responds 304 Not Modified"
    resolve_headline(part, content, brief, [])
    assert nodes[1].label == "May return 304 Not Modified"


def test_deck_design_request_is_routed_to_a_theme_only_proposal(monkeypatch):
    deck = themed_document()
    part = OutlineItem(title="Product demo", layout="closing")
    deck.slides = [build_slide(part, SlideContent(title=part.title), deck, 0, set())]
    theme = deck.theme.model_dump(exclude={"profile", "constraints", "brand_kit"})
    theme["design"]["mood"] = "Luxury editorial"

    def redesign(self, schema, task, data, stage):
        assert schema is RestyleDirection and stage == "design"
        assert "slides" not in data
        return schema.model_validate({**theme, "summary": "A more expressive editorial style"})

    monkeypatch.setattr(Intelligence, "__init__", lambda self: setattr(self, "provider", "compatible"))
    monkeypatch.setattr(Intelligence, "structured", redesign)
    proposal = propose(None, deck, ChatRequest(instruction="Make the deck more luxurious and preserve its facts."))
    operation, = proposal.operations
    assert isinstance(operation, Restyle)
    assert apply(deck, proposal).slides[0].spec.recipe == "closing_centered"
