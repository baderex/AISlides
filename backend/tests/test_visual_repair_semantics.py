"""Visual alternatives may change composition, but must preserve reviewed meaning."""
import io

from fastapi.testclient import TestClient
from PIL import Image as PILImage
import pytest

from app.domain import Line, Shape, Slide, Text
from app.html_layout.markup import parse_markup
from app.html_layout.review import Quality, markup_content
from app.main import app
from app.providers import Intelligence
from test_workflow import create, work


FREEFORM = ('<page><drawing grow="1"><label x="2" y="40" w="23" h="18">Browser</label>'
            '<label x="75" y="40" w="23" h="18">Server</label>'
            '<arrow x1="25" y1="50" x2="75" y2="50"/></drawing></page>')


def decoration_page(count):
    boxes = ''.join(f'<box x="{i*4}" y="5" w="2" h="2"/>' for i in range(count))
    return ('<page><text role="headline">Coverage</text><text>Current state</text>'
            '<text>Evidence</text><text>Next steps</text>'
            f'<drawing grow="1">{boxes}</drawing></page>')


def anchored_page(vertical=False):
    if vertical:
        boxes = '<box id="browser" x="10" y="5" w="30" h="20"/><box id="server" x="10" y="65" w="30" h="20"/>'
        labels = '<label x="12" y="7" w="26" h="16">Browser</label><label x="12" y="67" w="26" h="16">Server</label>'
        arrow = '<arrow x1="25" y1="25" x2="25" y2="65" from="browser:bottom" to="server:top"/>'
    else:
        boxes = '<box id="browser" x="5" y="40" w="25" h="20"/><box id="server" x="70" y="40" w="25" h="20"/>'
        labels = '<label x="7" y="42" w="21" h="16">Browser</label><label x="72" y="42" w="21" h="16">Server</label>'
        arrow = '<arrow x1="30" y1="50" x2="70" y2="50" from="browser:right" to="server:left"/>'
    return '<page><drawing grow="1">' + boxes + labels + arrow + '</drawing></page>'


def test_visual_signature_allows_decorative_cleanup_and_semantic_repositioning():
    assert markup_content(decoration_page(20)) == markup_content(decoration_page(1))
    assert markup_content(FREEFORM) == markup_content(FREEFORM.replace('y="40"', 'y="60"')
                                                     .replace('y1="50"', 'y1="70"').replace('y2="50"', 'y2="70"'))
    assert markup_content(anchored_page()) == markup_content(anchored_page(vertical=True))


@pytest.mark.parametrize("changed", [
    FREEFORM.replace('x1="25"', 'x1="75"').replace('x2="75"', 'x2="25"'),
    FREEFORM.replace('<arrow ', '<arrow head="false" '),
    FREEFORM.replace('<arrow x1="25" y1="50" x2="75" y2="50"/>', ''),
    FREEFORM.replace('>Browser<', '>TEMP<').replace('>Server<', '>Browser<').replace('>TEMP<', '>Server<'),
])
def test_visual_signature_preserves_freeform_direction_arrowheads_and_labels(changed):
    assert markup_content(FREEFORM) != markup_content(changed)


def test_visual_signature_preserves_named_and_labeled_nodes_and_visible_anchor_direction():
    original = anchored_page()
    no_node = original.replace('<box id="browser" x="5" y="40" w="25" h="20"/>', '')
    wrong_form = original.replace('id="browser"', 'id="browser" form="diamond"')
    reversed_visible_arrow = original.replace('x1="30"', 'x1="70"').replace('x2="70"', 'x2="30"')
    for changed in (no_node, wrong_form, reversed_visible_arrow):
        assert markup_content(original) != markup_content(changed)


def test_visual_signature_binds_edge_labels_to_their_connections():
    page = ('<page><drawing><label x="0" y="10" w="15" h="10">Browser</label>'
            '<label x="85" y="10" w="15" h="10">Server</label>'
            '<label x="0" y="70" w="15" h="10">Cache</label>'
            '<label x="85" y="70" w="15" h="10">Database</label>'
            '<arrow x1="15" y1="15" x2="85" y2="15"/>'
            '<arrow x1="15" y1="75" x2="85" y2="75"/>'
            '<label x="40" y="5" w="20" h="8">HTTPS</label>'
            '<label x="40" y="65" w="20" h="8">SQL</label></drawing></page>')
    switched = page.replace('>HTTPS<', '>TEMP<').replace('>SQL<', '>HTTPS<').replace('>TEMP<', '>SQL<')
    assert markup_content(page) != markup_content(switched)


def test_unresolved_connectors_keep_the_coordinate_guard():
    page = '<page><drawing><arrow x1="10" y1="20" x2="80" y2="20"/></drawing></page>'
    reverse = page.replace('x1="10"', 'x1="80"').replace('x2="80"', 'x2="10"')
    assert markup_content(page) != markup_content(reverse)
    ambiguous = FREEFORM.replace('</drawing>', '<label x="2" y="40" w="23" h="18">Other</label></drawing>')
    assert markup_content(ambiguous) != markup_content(ambiguous.replace('x1="25"', 'x1="26"'))


def test_visual_signature_retains_nonrectangular_graphics_and_assigned_media():
    page = '<page><drawing><poly points="10,10 30,10 20,30"/></drawing><picture asset="diagram"/></page>'
    moved = page.replace('10,10 30,10 20,30', '20,20 40,20 30,40')
    assert markup_content(page) == markup_content(moved)
    assert markup_content(page) != markup_content('<page><picture asset="diagram"/></page>')
    assert markup_content(page) != markup_content(page.replace('asset="diagram"', 'asset="photo"'))


def preview_bytes():
    output = io.BytesIO()
    PILImage.new("RGB", (128, 72), "white").save(output, format="PNG")
    return output.getvalue()


@pytest.mark.parametrize("meaningful_graphic", [False, True])
def test_workflow_reviews_cleanup_against_prior_geometry_and_keeps_notes_and_sources(monkeypatch, meaningful_graphic):
    original, cleaned = decoration_page(20), decoration_page(1)
    structured = Intelligence.structured
    reviews, composed, citations = [], [], []

    def model(self, schema, task, data, stage="planning", image=None):
        if schema.__name__ == "Intent":
            return schema(subject="Coverage", audience="Staff", visual_direction="Editorial", slide_count=1,
                          supplied_figures=True)
        if schema.__name__ == "HtmlPlan":
            return schema(title="Coverage", slides=[dict(title="Coverage", direction="Explain the supplied illustration")])
        if schema.__name__ == "PageMarkup":
            citations.extend(source["id"] for source in data["sources"])
            return schema(markup=original, notes="Credit: supplied illustration", source_ids=citations)
        if schema.__name__ == "LayoutMarkup":
            assert data["previous"] == original
            return schema(markup=cleaned)
        if stage == "content-review":
            assert data["speaker_notes"] == "Credit: supplied illustration"
            assert data["cited_source_ids"] == citations
            reviews.append(data)
            if len(reviews) == 2:
                prior = data["previous_reviewed"]
                assert len([el for el in prior["diagram"] if "node" in el]) == 20
                assert len([el for el in data["diagram"] if "node" in el]) == 1
                assert all(len(el["bounds"]) == 4 for el in prior["diagram"])
                assert prior["speaker_notes"] == data["speaker_notes"]
                if meaningful_graphic:
                    return schema(regenerate_content=True, issues=["Restore the nineteen meaningful illustration panels."])
            return schema()
        return structured(self, schema, task, data, stage, image)

    def measured(markup, title, theme, aspect, pictures, document, plan, candidate, *_):
        # Only Chromium measurement is doubled. Signature comparison, quality
        # scoring, content review, selection and checkpoint persistence are real.
        tree = parse_markup(markup)
        drawing = next(node for node in tree["children"] if node["tag"] == "drawing")
        count = len(drawing["children"])
        composed.append(count)
        elements = [Text(x=.07, y=.06+i*.18, w=.86, h=.10, text=node["t"], size=36 if i == 0 else 18)
                    for i, node in enumerate(tree["children"][:-1])]
        elements.extend(Shape(x=.04+(i % 10)*.09, y=.79+(i//10)*.06, w=.04, h=.025, fill="#888888")
                        for i in range(count))
        return Slide(title=title, elements=elements, layout=plan.layout, notes=candidate.notes,
                     source_ids=candidate.source_ids), preview_bytes()

    monkeypatch.setattr(Intelligence, "structured", model)
    monkeypatch.setattr("app.workflow.html_layout.compose", measured)
    monkeypatch.setattr("app.workflow.renderer", lambda: None)
    client = TestClient(app)
    path = create(client, "Create one editorial slide for staff about the supplied illustration.", research=False)
    work()
    result = client.get(path).json()
    assert result["status"] == "COMPLETED", result
    slide, = result["document"]["slides"]
    assert len(reviews) == 2
    assert len([el for el in slide["elements"] if el["type"] == "shape"]) == (20 if meaningful_graphic else 1)
    assert composed == ([20, 1, 20] if meaningful_graphic else [20, 1])
    assert slide["notes"] == "Credit: supplied illustration" and slide["source_ids"] == citations and citations
    assert bool(slide["warnings"]) == meaningful_graphic


@pytest.mark.parametrize("vision", [False, True])
def test_workflow_still_blocks_reversed_freeform_repair_before_composition(monkeypatch, vision):
    from app.config import settings

    reversed_page = FREEFORM.replace('x1="25"', 'x1="75"').replace('x2="75"', 'x2="25"')
    structured = Intelligence.structured
    composed, reviews = [], []

    def model(self, schema, task, data, stage="planning", image=None):
        if schema.__name__ == "HtmlPlan":
            return schema(title="Request", slides=[dict(title="Request", direction="Show Browser to Server")])
        if schema.__name__ == "PageMarkup":
            return schema(markup=FREEFORM, notes="Diagram credit")
        if schema.__name__ == "LayoutMarkup":
            return schema(markup=reversed_page)
        if stage == "content-review":
            edge, = [el for el in data["diagram"] if "edge" in el]
            assert edge["labels_near_start"] == ["Browser"] and edge["labels_near_end"] == ["Server"]
            assert edge["arrowheads"] == "end" and bool(image) == vision
            reviews.append(edge)
            return schema()
        return structured(self, schema, task, data, stage, image)

    def measured(markup, title, theme, aspect, pictures, document, plan, candidate, *_):
        composed.append(markup)
        assert markup == FREEFORM
        return Slide(title=title, layout=plan.layout, notes=candidate.notes, elements=[
            Text(x=.02, y=.40, w=.23, h=.18, text="Browser", size=20),
            Text(x=.75, y=.40, w=.23, h=.18, text="Server", size=20),
            Line(x=.25, y=.495, w=.50, h=.01, direction="right", arrows="end"),
        ]), preview_bytes()

    monkeypatch.setattr(settings(), "llm_vision_enabled", vision)
    monkeypatch.setattr(Intelligence, "structured", model)
    monkeypatch.setattr("app.workflow.html_layout.compose", measured)
    monkeypatch.setattr("app.workflow.renderer", lambda: None)
    monkeypatch.setattr("app.workflow.evaluate_quality", lambda *_: Quality([], ["Improve spacing"], 1))
    client = TestClient(app)
    path = create(client, "Create one editorial slide for staff about a browser request.", research=False)
    work()
    result = client.get(path).json()
    assert result["status"] == "COMPLETED", result
    assert composed == [FREEFORM, FREEFORM] and len(reviews) == 1
    assert next(el for el in result["document"]["slides"][0]["elements"] if el["type"] == "line")["direction"] == "right"
