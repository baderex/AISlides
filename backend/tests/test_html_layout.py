import io
from types import SimpleNamespace
from zipfile import ZipFile
from xml.etree import ElementTree

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from pptx import Presentation
from pptx.enum.text import PP_ALIGN
from sqlalchemy import select

from app.db import Job, PresentationRecord, Session
from app.deck_design import baseline_rules, DeckDesignSpec
from app.design import style_chart
from app.domain import Chart, Document, Shape, Theme
from app.events import fetch_events
from app.export_review import compare_rasters
from app.powerpoint import to_pptx
from app.html_layout import compose
from app.html_layout.markup import MarkupError, PagePlan, parse_markup
from app.main import app
from app.providers import Intelligence, ProviderError
from app.queue import claim
from app.workflow import advance, html_slide_inputs
from model_fixture import model_response
from test_workflow import create, work


PALETTE = dict(background="#FAF9F5", ink="#17283C", accent="#16856B", muted="#58677A", surface="#FFFFFF",
               display="Georgia")
MATRIX = """<page><text font="display" size="70">FORM</text><row grow="1" align="center">
  <col w="100%" align="center"><row w="100%"><matrix grow="1" widths="70,30">
  <tr><td>Capability</td><td>Level</td></tr><tr><td>Inspection</td><td align="right" bold="true">4</td></tr>
  </matrix></row></col></row></page>"""


def test_divider_in_end_aligned_column_uses_available_width():
    markup = ('<page><col w="400" align="end"><rule h="1"/><rule w="36" h="2"/></col></page>')
    slide, _ = compose(markup, "Dividers", PALETTE, "16:9")
    full, short = slide.elements
    assert full.w == pytest.approx(400 / 1280)
    assert short.w == pytest.approx(36 / 1280)
    assert full.x + full.w == pytest.approx(short.x + short.w)


def test_col_geometry_exports_as_native_shapes_and_a_directed_connector():
    slide, _ = compose('<page><col grow="1"><text>Browser SPA</text>'
                       '<box id="spa" x="5" y="30" w="20" h="20"/>'
                       '<box id="api" x="75" y="30" w="20" h="20"/>'
                       '<arrow x1="25" y1="40" x2="75" y2="40" from="spa:right" to="api:left"/>'
                       '</col></page>', "Architecture", PALETTE, "1:1")
    title, spa, api, arrow = slide.elements
    assert title.text == "Browser SPA" and arrow.arrows == "end"
    assert (arrow.start_anchor.shape_id, arrow.end_anchor.shape_id) == (spa.id, api.id)
    with pytest.raises(MarkupError, match="Unexpected <script> inside <col>"):
        parse_markup("<page><col><script/></col></page>")


@pytest.mark.parametrize("vision", [False, True])
def test_layout_repair_cannot_reverse_a_reviewed_freeform_arrow(monkeypatch, vision):
    from app.config import settings
    from app.html_layout.review import Quality, markup_content

    page = ('<page><drawing grow="1"><label x="2" y="40" w="23" h="18">Browser</label>'
            '<label x="75" y="40" w="23" h="18">Server</label>'
            '<arrow x1="25" y1="50" x2="75" y2="50"/></drawing></page>')
    reversed_page = page.replace('x1="25"', 'x1="75"').replace('x2="75"', 'x2="25"')
    assert markup_content(page) != markup_content(reversed_page)
    assert markup_content(page) != markup_content(page.replace('<arrow ', '<arrow head="false" '))
    structured = Intelligence.structured
    reviews, repairs = [], []

    def model(self, schema, task, data, stage="planning", image=None):
        if schema.__name__ == "HtmlPlan":
            return schema(title="Request", slides=[dict(title="Request", direction="Show browser to server")])
        if schema.__name__ == "PageMarkup":
            return schema(markup=page, notes="Diagram credit")
        if schema.__name__ == "LayoutMarkup":
            repairs.append(data["previous"])
            return schema(markup=reversed_page)
        if stage == "content-review":
            edge = next(obj for obj in data["diagram"] if "edge" in obj)
            assert edge["arrowheads"] == "end" and edge["edge"][0][0] < edge["edge"][-1][0]
            assert edge["labels_near_start"] == ["Browser"] and edge["labels_near_end"] == ["Server"]
            assert data["speaker_notes"] == "Diagram credit" and bool(image) == vision
            reviews.append(edge)
            return schema()
        return structured(self, schema, task, data, stage, image)

    monkeypatch.setattr(settings(), "llm_vision_enabled", vision)
    monkeypatch.setattr(Intelligence, "structured", model)
    monkeypatch.setattr("app.workflow.renderer", lambda: None)
    monkeypatch.setattr("app.workflow.evaluate_quality", lambda *_: Quality([], ["Improve spacing"], 1))
    client = TestClient(app)
    path = create(client, "Create one editorial slide for staff about a browser request.", research=False)
    work()
    result = client.get(path).json()
    assert result["status"] == "COMPLETED" and repairs == [page] and len(reviews) == 1
    assert next(el for el in result["document"]["slides"][0]["elements"] if el["type"] == "line")["direction"] == "right"


def test_html_slide_uses_planned_evidence_and_preserves_older_outlines():
    item = SimpleNamespace(request={"prompt": "Compare error correction methods"})
    sources = [{"id": key, "excerpt": key * 200} for key in ("brief", "history", "hardware")]
    context = {"sources": sources, "brief_source_id": "brief", "research": {
        "summary": "Deck-wide findings", "uncertainties": ["Dates are estimates"],
        "observations": [{"source_id": key, "quote": key} for key in ("history", "hardware")]}}
    plan = PagePlan(title="Hardware", direction="Explain the current demonstration", source_ids=["hardware"])
    request = html_slide_inputs(item, context, plan)
    assert request["sources"] == [sources[0], sources[2]]
    assert request["research"] == {"uncertainties": ["Dates are estimates"], "observations": [{"source_id": "hardware"}]}
    for ids in (None, ["missing"]):
        assert html_slide_inputs(item, context, plan.model_copy(update={"source_ids": ids}))["sources"] == sources
    assert html_slide_inputs(item, context, plan.model_copy(update={"source_ids": []}))["sources"] == sources[:1]


@pytest.mark.parametrize("native", [False, True])
def test_finished_deck_repairs_missing_notes_and_retains_late_brief_evidence(monkeypatch, native):
    from app.evidence import EvidenceObservation, verified_observations
    from app.domain import Source
    from app.composition_models import Outline
    from app.html_layout.markup import HtmlPlan
    from pydantic import ValidationError

    required = "Credit the city archive in slide one's speaker notes"
    quote = "Q3 revenue was $7m."
    prompt = "Make two editorial slides for staff. " + required + ". " + "Background material. " * 175 + quote
    structured = Intelligence.structured
    audits = []
    def model(self, schema, task, data, stage="planning", image=None):
        if schema.__name__ == "Intent":
            return schema(subject="Revenue", audience="Staff", visual_direction="Editorial", slide_count=2,
                          supplied_figures=True, requirements=[required])
        if schema in (Outline, HtmlPlan):
            slides = [{"title": s, **({"layout": "summary"} if native else {"direction": s})}
                      for s in ("Report", "Conclusion")]
            checks = {"brief_requirements": [required], "slide_count": 2}
            assert data["brief_requirements"] == [required]
            with pytest.raises(ValidationError, match="Missing IDs"):
                schema.model_validate({"title": "Report", "slides": slides}, context=checks)
            slides[0]["requirement_ids"] = [1]
            return schema.model_validate({"title": "Report", "slides": slides}, context=checks)
        if stage in ("html-slide", "slide"):
            title = (data["item"] if native else data["slide"])["title"]
            assigned = data["assigned_requirements"]
            assert assigned == ([required] if title == "Report" else [])
            note = "City archive" if data.get("required_correction") else ""
            return schema(title=title, notes=note) if native else schema(markup=f"<page><text>{title}</text></page>", notes=note)
        if schema.__name__ == "CoverageReview":
            page, = data["slides"]
            audits.append(page["speaker_notes"])
            return schema(issues=[] if page["speaker_notes"] == "City archive" else
                          [dict(slide_number=1, correction="Credit the city archive in speaker notes.")])
        return structured(self, schema, task, data, stage, image)

    monkeypatch.setattr(Intelligence, "structured", model)
    monkeypatch.setattr("app.workflow.renderer", lambda: None)
    client = TestClient(app)
    path = create(client, prompt, native=native, research=False)
    work()
    result = client.get(path).json()
    assert result["status"] == "COMPLETED" and audits == ["", "City archive"]
    first, second = result["document"]["slides"]
    assert first["notes"] == "City archive" and second["title"] == "Conclusion"
    source, = [Source.model_validate(s) for s in result["document"]["sources"] if s["title"] == "User-provided brief"]
    assert source.excerpt == prompt
    row = EvidenceObservation(source_id=source.id, metric="revenue", period="Q3", amount=7, unit="m", currency="$", quote=quote)
    assert list(verified_observations([row], [source])) == [row]


def test_retry_legacy_architecture_checkpoint_keeps_diagram_and_finished_cover(monkeypatch):
    client = TestClient(app)
    path = create(client, "A square OAuth deck", aspect_ratio="1:1", research=False)
    visual = {"type":"architecture", "nodes":[{"id":key, "label":label} for key, label in
              [("spa","Browser SPA"), ("auth","Authorization endpoint"), ("token","Token endpoint"), ("api","API")]],
              "edges":[{"source":a, "target":b, "label":label} for a,b,label in [
                  ("spa","auth","Redirect"), ("auth","spa","Authorization code"), ("spa","token","Code and verifier"),
                  ("token","spa","Access token"), ("spa","api","Bearer token")]]}
    with Session.begin() as db:
        job = db.scalar(select(Job))
        item = db.get(PresentationRecord, job.presentation_id)
        item.status = "FAILED"
        source = {"id":"spec", "title":"PKCE", "excerpt":"The client submits the verifier."}
        item.document = {**item.document, "slides":[{"id":"cover", "title":"OAuth", "elements":[]}], "sources":[source]}
        job.state, job.checkpoint = "failed", "html_slide"
        job.context = {"layout_engine":"html", "sources":[source], "intent":{}, "progress_index":1,
                       "usage":{"input":68016,"output":13059,"estimated_cost":.24},
                       "html_plan":{"title":"OAuth", "slides":[{"title":"OAuth","direction":"Cover"},
                           {"title":"Architecture", "direction":"Show the endpoints", "exhibit":"architecture"}]},
                       "layout_retry":{"attempt":2, "error":"Use exactly one exhibit frame for a planned exhibit, and none otherwise.",
                                       "markup":"<page><col><text>Public client</text></col></page>", "exhibit_data":visual}}
    calls = []
    def recover(self, schema, task, data, stage, image=None):
        calls.append(schema.__name__)
        if stage == "content-review":
            assert data["cited_source_ids"] == ["spec"] and "Architecture" in data["visible"]
            return schema()
        assert data["exhibit_data"] == visual and "outline" not in data and self.usage["attempt_start_tokens"] == 81075
        return schema(caption="Public client", sources=[1], notes="Protocol reference")
    monkeypatch.setattr(Intelligence, "structured", recover)
    monkeypatch.setattr("app.workflow.renderer", lambda: None)
    assert client.post(path + "/generate").status_code == 202
    work()
    item = client.get(path).json()
    assert calls == ["RecoveredExhibitFrame", "Review"]
    assert item["status"] == "COMPLETED"
    cover, diagram = item["document"]["slides"]
    assert cover["id"] == "cover" and diagram["source_ids"] == ["spec"] and diagram["notes"] == "Protocol reference"
    arrows = [el for el in diagram["elements"] if el.get("arrows") == "end"]
    assert [el["direction"] for el in arrows] == ["right", "left", "right", "left", "right"]
    assert len({el["y"] for el in arrows}) == len(arrows)


def test_live_route_checkpoints_browser_retry_and_exports_edited_objects(monkeypatch):
    calls = []

    def model(self, schema, task, data, stage="planning", image=None):
        calls.append(schema.__name__)
        if schema.__name__ == "ArtDirection":
            return schema.model_validate(model_response("ArtDirection", data))
        if schema.__name__ == "Intent":
            return schema(subject="Inspection", audience="Board", visual_direction="Editorial", slide_count=1)
        if schema.__name__ == "Clarification":
            return schema(questions=[])
        if schema.__name__ == "HtmlPlan":
            return schema(title="Inspection", slides=[dict(title="Inspection", direction="Show the table")])
        if schema.__name__ == "PageMarkup":
            return schema(markup="<page><script/></page>", notes="Keep these presenter notes.")
        if schema.__name__ == "LayoutMarkup":
            assert data["previous"] == "<page><script/></page>" and "correction" in data
            assert {"research", "sources", "outline", "previous_exhibit_data"}.isdisjoint(data)
            return schema(markup=MATRIX)
        if stage == "content-review":
            assert {"table": [["Capability", "Level"], ["Inspection", "4"]]} in data["visible"]
            return schema()
        raise AssertionError(f"Unexpected legacy generation request: {schema.__name__}")

    monkeypatch.setattr(Intelligence, "structured", model)
    interruptions = []

    def interrupt_browser(markup, *args):
        if markup == MATRIX and not interruptions:
            interruptions.append(True)
            raise ProviderError("Browser temporarily unavailable")
        return compose(markup, *args)

    monkeypatch.setattr("app.html_layout.compose", interrupt_browser)
    client = TestClient(app)
    item = client.post("/api/v1/presentations", json={"prompt":"One square board slide", "aspect_ratio":"1:1",
                                                    "research":False}).json()
    path = "/api/v1/presentations/" + item["id"]
    reviewed = []
    for _ in range(15):
        with Session() as db:
            ticket = claim(db)
        if not ticket:
            break
        advance(*ticket)
        with Session() as db:
            job = db.scalar(select(Job).where(Job.presentation_id == item["id"]))
            if job.checkpoint == "html_measure" and job.state == "queued":
                reviewed.append(job.context["html_candidate"]["markup"])
            failed = job.state == "failed"
        if failed:
            assert client.post(path + "/generate").status_code == 202
    item = client.get(path).json()
    assert item["status"] == "COMPLETED" and item["slide_count"] == 1
    assert reviewed == ["<page><script/></page>", MATRIX]
    assert interruptions == [True]
    assert calls.count("HtmlPlan") == calls.count("PageMarkup") == calls.count("LayoutMarkup") == 1
    saved = item["document"]
    slide = saved["slides"][0]
    assert slide["notes"] == "Keep these presenter notes."
    title, table = slide["elements"]
    assert title["single_line"] and table["css"]["columns"][0] > .69
    table["rows"][1][0] = "Updated inspection"
    response = client.patch(path, json={"revision":item["revision"],"document":saved,"label":"Manual edit"})
    assert response.is_success, response.text
    native = Presentation(io.BytesIO(client.post(path + "/export", params={"format":"pptx"}).content))
    assert native.slide_width == native.slide_height
    text, exhibit = native.slides[0].shapes
    assert text.text_frame.word_wrap is False and text.text == "FORM"
    assert exhibit.table.columns[0].width / exhibit.width > .69
    assert exhibit.table.cell(1,0).text == "Updated inspection"
    assert exhibit.table.cell(1,1).text_frame.paragraphs[0].alignment == PP_ALIGN.RIGHT
    assert client.get(path + "/slides/" + slide["id"] + "/preview").content.startswith(b"\x89PNG")
    assert client.post(path + "/export", params={"format":"pdf"}).content.startswith(b"%PDF")


def test_picture_keeps_the_uploaded_asset_and_its_aspect(monkeypatch):
    raw = io.BytesIO()
    Image.new("RGB", (160,80)).save(raw, "PNG")
    monkeypatch.setattr("app.html_layout.Storage.get", lambda self,key: raw.getvalue())
    slide, _ = compose('<page><picture grow="1" asset="upload" alt="Sample"/></page>', "Product",
                       PALETTE, "4:3", {"upload"})
    image = slide.elements[0]
    assert image.asset_id == "upload" and image.alt == "Sample"
    assert abs((image.w / image.h) * 4 / 3 - 2) < .001
    assert Document.model_validate_json(Document(title="Product", slides=[slide]).model_dump_json()).slides == [slide]


def test_palette_and_type_roles_reach_editable_objects():
    from app.render_geometry import contrast

    theme = Theme(accent="#FDFDFD", surface="#FDFCFB", font="Verdana")
    theme.design = baseline_rules(theme, "editorial")
    theme.design.chart_colors = ["#FAFAFA", "#FBFBFB"]
    deck = Document(title="Review", theme=theme)
    spec = DeckDesignSpec.from_theme(theme, "16:9")
    markup = ('<page><text role="headline">Review</text><text role="body" size="12">Body copy</text>'
              '<rule color="#FF00FF"/><text role="footnote">Source note</text></page>')
    slide, _ = compose(markup, "Review", theme.model_dump(), "16:9", document=deck)
    heading, body, shape, footnote = slide.elements
    assert heading.font == theme.design.heading_font and heading.size == spec.typography["headline"].target_pt
    assert body.font == "Verdana" and body.size >= 18
    assert footnote.size == pytest.approx(11, abs=.01)
    assert shape.fill.upper() in {color.upper() for color in spec.palette.values()}
    for design in (theme.design, None):
        chart = Chart(x=0, y=0, w=1, h=1, categories=["A"], series=[dict(name=str(i),values=[i]) for i in range(5)])
        style_chart(chart, theme.model_copy(update={"design":design}), "#FFFFFF")
        assert len(set(chart.palette)) == 5
        assert all(contrast(c, "#FFFFFF") >= 3 for c in chart.palette)


def test_wrong_amount_and_scope_trigger_content_repair(monkeypatch):
    structured = Intelligence.structured
    required = ["Approve a pilot capped at $180,000", "No fleetwide rollout", "Three day-90 gates"]
    corrected = "Approve a pilot capped at $180,000. No fleetwide rollout. Day-90 gates: safety, savings, uptime."
    reviews = []

    def model(self, schema, task, data, stage="planning", image=None):
        if schema.__name__ == "HtmlPlan":
            return schema(title="Pilot", slides=[dict(title="Pilot decision", direction="Seek approval",
                                                      required_details=required)])
        if schema.__name__ == "PageMarkup":
            copy = "The pilot needs $999,000. Roll out immediately."
            if reviews:
                assert "funding cap" in data["correction"]
                copy = corrected
            return schema(markup=f'<page><text role="body">{copy}</text></page>')
        if stage == "content-review":
            assert data["assignment"]["required_details"] == required and "$180,000" in data["brief"]
            reviews.append(data["visible"])
            if corrected not in data["visible"]:
                return schema(regenerate_content=True, issues=["Correct the funding cap, scope and day-90 gates."])
            return schema(regenerate_content=False)
        return structured(self, schema, task, data, stage, image)

    monkeypatch.setattr(Intelligence, "structured", model)
    monkeypatch.setattr("app.workflow.renderer", lambda: None)
    client = TestClient(app)
    path = create(client, "Create one editorial slide for executives. " + ". ".join(required), research=False)
    work()
    result = client.get(path).json()
    assert result["status"] == "COMPLETED" and len(reviews) == 2
    assert result["document"]["slides"][0]["elements"][0]["text"] == corrected


def test_html_crop_radius_and_code_survive_measurement(monkeypatch):
    raw = io.BytesIO()
    Image.new("RGB", (200, 100), "navy").save(raw, "PNG")
    monkeypatch.setattr("app.html_layout.Storage.get", lambda self, key: raw.getvalue())
    slide, _ = compose('<page><text role="code">    emit(  value)</text>'
                       '<drawing h="110"><box x="0" y="0" w="100" h="100" form="round"/></drawing>'
                       '<picture grow="1" asset="photo" fit="cover" focal_x="1" focal_y=".25"/></page>',
                       "Crop", PALETTE, "1:1", {"photo"})
    code, card, photo = slide.elements
    assert code.role == "code" and code.text == "    emit(  value)"
    assert card.radius_pt == 6
    assert (photo.fit, photo.focal_x, photo.focal_y) == ("cover", 1, .25)


def test_upload_conclusion_and_table_reach_the_finished_deck(monkeypatch):
    structured = Intelligence.structured
    conclusion = "Maintenance fell by 18%."
    report = "General background. " * 550 + "\n" + conclusion + "\nQuarter | Savings\nQ3 | $82,500"

    def model(self, schema, task, data, stage="planning", image=None):
        if schema.__name__ == "HtmlPlan":
            source, = data["sources"]
            assert source["sections"] and conclusion in source["excerpt"]
            return schema(title="Maintenance", slides=[dict(title="Maintenance", direction="Show quarterly savings",
                                                            source_ids=[source["id"]])])
        if schema.__name__ == "PageMarkup":
            source, = data["sources"]
            lines = source["excerpt"].splitlines()
            assert conclusion in lines and "Q3 | $82,500" in lines
            quarter, savings = lines[-1].split(" | ")
            return schema(markup=f"<page><text>{lines[-3]}</text><matrix><tr><td>Quarter</td><td>Savings</td></tr>"
                                 f"<tr><td>{quarter}</td><td>{savings}</td></tr></matrix></page>",
                          source_ids=[source["id"]])
        return structured(self, schema, task, data, stage, image)

    monkeypatch.setattr(Intelligence, "structured", model)
    monkeypatch.setattr("app.workflow.renderer", lambda: None)
    client = TestClient(app)
    upload = client.post("/api/v1/assets", files={"file": ("report.txt", report.encode(), "text/plain")})
    path = create(client, "Create one editorial slide for staff about maintenance savings from the report.",
                  asset_ids=[upload.json()["id"]], research=False)
    work()
    result = client.get(path).json()
    assert result["status"] == "COMPLETED"
    source, = result["document"]["sources"]
    slide, = result["document"]["slides"]
    assert slide["source_ids"] == [source["id"]]
    deck = Presentation(io.BytesIO(client.post(path + "/export", params={"format": "pptx"}).content))
    shapes = deck.slides[0].shapes
    assert any(shape.has_text_frame and shape.text == conclusion for shape in shapes)
    assert any(shape.has_table and shape.table.cell(1, 1).text == "$82,500" for shape in shapes)


@pytest.mark.parametrize(("repair_valid", "export_only"), [(True, False), (False, False), (True, True)])
def test_hard_visual_defects_require_a_valid_repair(monkeypatch, repair_valid, export_only):
    structured = Intelligence.structured
    attempts = []
    def model(self, schema, task, data, stage="planning", image=None):
        if schema.__name__ in {"PageMarkup", "LayoutMarkup"}:
            if attempts:
                assert ("Export fit" if export_only else "Low text contrast") in data["correction"]
            attempts.append(schema.__name__)
            color = "ink" if export_only or len(attempts) > 1 and repair_valid else "background"
            return schema(markup=f'<page><text color="{color}">Coverage</text></page>')
        return structured(self, schema, task, data, stage, image)
    monkeypatch.setattr(Intelligence, "structured", model)
    monkeypatch.setattr("app.workflow.renderer", lambda: "test-renderer" if export_only else None)
    if export_only:
        monkeypatch.setattr("app.workflow.compare_slide_export", lambda *args:
                            (["Export fit: the headline moved outside its box."] if len(attempts) == 1 else [], 0.))
    client = TestClient(app)
    path = create(client, "Create one slide for staff about coverage in an editorial style.", research=False)
    work()
    result = client.get(path).json()
    assert result["status"] == ("COMPLETED" if repair_valid else "FAILED")
    assert result["slide_count"] == int(repair_valid)
    assert len(attempts) == (2 if repair_valid else 3)
    drafts = [event["payload"] for event in fetch_events(result["id"], 0) if event["type"] == "slide.draft"]
    assert drafts[0]["lines"] == ["Coverage"]
    layouts = [draft["slide"] for draft in drafts if "slide" in draft]
    assert len(layouts) == len(attempts)
    if repair_valid and not export_only:
        assert layouts[0]["elements"][0]["color"] != layouts[-1]["elements"][0]["color"]
    if not repair_valid:
        assert len(result["planned_slide_titles"]) == 1
        assert result["review_draft"]["slide"] == layouts[-1]
        assert "contrast" in result["review_feedback"]


@pytest.mark.parametrize("improves", [True, False])
def test_soft_selection_preserves_content_and_passes_recent_geometry(monkeypatch, improves):
    structured = Intelligence.structured
    first = '<page><text role="headline">Coverage</text><text>Four districts</text></page>'
    repairs = []
    def model(self, schema, task, data, stage="planning", image=None):
        if schema.__name__ == "HtmlPlan":
            return schema(title="Coverage", slides=[dict(title=t, direction="Show service coverage")
                          for t in ("Coverage", "Teams")])
        if schema.__name__ == "PageMarkup":
            if data["slide_number"] == 1:
                assert data["recent_compositions"]["recent"] == []
                return schema(markup=first)
            prior, = data["recent_compositions"]["recent"]
            assert prior["pattern"] == "text stack" and prior["headline"]["pt"] >= 30 and prior["left_margin"] > 0
            return schema(markup='<page><text role="headline">Teams</text></page>')
        if schema.__name__ == "LayoutMarkup":
            assert "canvas" in data["correction"]
            repairs.append(data["previous"])
            if improves:
                return schema(markup=first.replace("<page>", '<page justify="between">'))
            return schema(markup='<page><text role="headline">Coverage</text></page>')
        return structured(self, schema, task, data, stage, image)
    monkeypatch.setattr(Intelligence, "structured", model)
    monkeypatch.setattr("app.workflow.renderer", lambda: None)
    client = TestClient(app)
    path = create(client, "Create two slides for staff about coverage in an editorial style.", research=False, skill_id="product")
    work()
    result = client.get(path).json()
    assert result["status"] == "COMPLETED" and repairs == [first]
    assert result["document"]["theme"]["accent"].upper() == "#6941C6"
    body = next(t for t in result["document"]["slides"][0]["elements"] if t.get("text") == "Four districts")
    assert (body["y"] > .7) == improves


def test_raster_comparison_localizes_export_shift():
    def png(image):
        raw = io.BytesIO()
        image.save(raw, "PNG")
        return raw.getvalue()
    before = Image.new("RGB", (480, 270), "white")
    after = before.copy()
    after.paste("black", (320, 180, 480, 270))
    assert compare_rasters(png(before), png(before), [])[0] == 0
    distance, region, missing = compare_rasters(png(before), png(after), [])
    assert distance > .9 and region == "bottom right"


def test_google_fonts_survive_offline_measurement_and_editable_export():
    deck = Document(title="Coverage", theme=Theme(font="Manrope"))
    deck.theme.design = baseline_rules(deck.theme, "editorial")
    markup = ('<page><text role="headline">Coverage</text><text role="body">Four districts</text>'
              '<text role="code">count = 4</text></page>')
    slide, _ = compose(markup, deck.title, deck.theme.model_dump(), "16:9", document=deck)
    families = ["Source Serif 4", "Manrope", "IBM Plex Mono"]
    assert [el.font for el in slide.elements] == families
    deck.slides.append(slide)
    raw = to_pptx(deck, lambda key: None)
    with ZipFile(io.BytesIO(raw)) as package:
        xml = ElementTree.fromstring(package.read("ppt/presentation.xml"))
        assert {e.get("typeface") for e in xml.findall(".//{*}embeddedFont/{*}font")} == set(families)
        parts = [package.read(n) for n in package.namelist() if n.endswith(".fntdata")]
        assert len(parts) == 6
        for font in parts:
            assert int.from_bytes(font[:4], "little") == len(font) and font[34:36] == b"LP"
            size = int.from_bytes(font[4:8], "little")
            assert font[-size:][:4] == b"\0\1\0\0"
    client = TestClient(app)
    assert "SIL OPEN FONT LICENSE" in client.get("/api/v1/fonts/Manrope-OFL.txt").text
    assert client.get("/api/v1/fonts/Manrope-Regular.ttf").content.startswith(b"\0\1\0\0")


def test_brand_and_readability_apply_to_measured_objects(monkeypatch):
    raw = io.BytesIO()
    Image.new("RGB", (100,50)).save(raw, "PNG")
    monkeypatch.setattr("app.html_layout.Storage.get", lambda self,key: raw.getvalue())
    brand = dict(protected=["accent","background"], logo_asset_id="kit-logo")
    theme = {**PALETTE,"accent":"#006841"}
    document = Document(title="Review", theme={**{k:v for k,v in theme.items() if k != "display"}, "brand_kit":brand})
    markup = """<page bg="#000000"><matrix h="130"><tr><td>Input</td></tr><tr><td>Middle</td></tr>
    <tr><td bg="#FFFFFF" color="#EEEEEE">Unreadable cell</td></tr></matrix>
    <drawing grow="1"><box x="0" y="0" w="8" h="8" bg="#00FF66"/>
    <label x="20" y="35" w="60" h="30" align="center">Decision point</label>
    <arrow x1="50" y1="10" x2="50" y2="90"/></drawing></page>"""
    slide, _ = compose(markup, document.title, theme, "16:9", document=document)
    assert slide.background.lower() == PALETTE["background"].lower()
    assert any(isinstance(e,Shape) and e.fill.lower() == "#006841" for e in slide.elements)
    assert len([e for e in slide.elements if getattr(e,"asset_id",None) == "kit-logo"]) == 1
    assert "Low table cell contrast." in slide.warnings
    assert "Connector crosses label: Decision point" in slide.warnings


def test_northstar_html_edit_recalculates_flags_and_exports_workbook(monkeypatch):
    columns = ["Retention impact", "Speed", "Cost efficiency"]
    weights = [50, 30, 20]
    options = {"Improved onboarding":[5,4,3], "Blanket discounting":[2,3,4], "Customer-success playbooks":[4,5,5]}
    facts = [(metric, label, value) for label, scores in options.items() for metric, value in zip(columns, scores)]
    facts.extend(("Weight", metric, value) for metric, value in zip(columns, weights))
    quotes = [f"{metric} for {label} was {value}." for metric, label, value in facts]
    markup = '<page><text size="42">Northstar decision</text><row grow="1"><exhibit grow="3"/><col grow="1"><text>Board note</text></col></row></page>'

    def model(self, schema, task, data, stage="planning", image=None):
        if schema.__name__ == "ArtDirection":
            return schema.model_validate(model_response("ArtDirection", data))
        if schema.__name__ == "Intent":
            return schema(subject="Northstar", slide_count=2, supplied_figures=True, requires_chart_workbook=True)
        if schema.__name__ == "Clarification":
            return schema(questions=[])
        if schema.__name__ == "HtmlPlan":
            assert data["requires_chart_workbook"]
            return schema(title="Northstar", slides=[
                dict(title="Northstar decision", layout="research", exhibit="decision-matrix", direction="Compare initiatives"),
                dict(title="Score inputs", layout="chart", exhibit="workbook-chart", direction="Compare the raw scores")])
        if schema.__name__ == "LayoutMarkup":
            assert "Export difference:" in data["correction"]
            return schema(markup=data["previous"])
        if stage == "content-review":
            assert data["cited_source_ids"]
            return schema()
        assert schema.__name__ == "ExhibitPage", data.get("correction")
        source = data["sources"][0]["id"]
        if data["slide_number"] == 1:
            return schema(markup=markup, source_ids=[source], exhibit_data={
                "type":"decision-matrix", "columns":columns, "weights":weights, "recommended":"Customer-success playbooks",
                "rows":[dict(label=k,cells=v) for k,v in options.items()]},
                observations=[dict(source_id=source, metric=m, period=c, amount=v, quote=q)
                              for (m,c,v),q in zip(facts,quotes)])
        return schema(markup='<page><text>Score inputs</text><exhibit grow="1"/></page>', source_ids=[source],
                      exhibit_data=dict(chart="column", categories=list(options),
                                        series=[dict(name=c, values=[v[i] for v in options.values()]) for i,c in enumerate(columns)]))

    monkeypatch.setattr(Intelligence, "structured", model)
    client = TestClient(app)
    item = client.post("/api/v1/presentations", json={"prompt":"Compare three initiatives and include the editable chart workbook. " +
                                                    " ".join(quotes), "research":False}).json()
    path = "/api/v1/presentations/" + item["id"]
    for _ in range(16):
        with Session() as db:
            ticket = claim(db)
        if not ticket:
            break
        advance(*ticket)
    item = client.get(path).json()
    assert item["status"] == "COMPLETED"
    draft, chart = item["document"]["slides"]
    assert all(s["spec"]["html_region"] for s in [draft,chart])
    assert any(e.get("text") == "Exhibit data: source verified and calculated" for e in draft["elements"])
    keyed = lambda slide, key: next(e for e in slide["elements"] if e["element_key"] == key)
    bar_key = "decision-matrix:score-bar:Customer-success playbooks"
    bar = keyed(draft,bar_key)
    original_width = bar["w"]
    bar.update(edited=True,fill="#AD3820",w=original_width / 2)
    deleted_bar = "decision-matrix:score-bar:Blanket discounting"
    draft["elements"] = [e for e in draft["elements"] if e["element_key"] != deleted_bar]
    note = next(e for e in draft["elements"] if e.get("text") == "Board note")
    note.update(edited=True,text="Keep this board note")
    edited_table = keyed(draft,"decision-matrix:table")
    edited_table["edited"] = True
    edited_table["rows"][1][0] = "Improved onboarding (A)"
    visual = draft["spec"]["content"]["visual"]
    visual["rows"][2]["cells"][1] = 4
    result = client.post(f'{path}/slides/{draft["id"]}/exhibit', json={
        "revision":item["revision"],"slide":draft,"visual":visual})
    assert result.is_success, result.text
    rebuilt = result.json()
    table = keyed(rebuilt,"decision-matrix:table")
    assert [row[-1] for row in table["rows"][1:]] == ["4.3","2.7","4.2"]
    assert table["rows"][1][0] == "Improved onboarding (A)"
    assert keyed(rebuilt,bar_key)["id"] == bar["id"] and keyed(rebuilt,bar_key)["fill"] == "#AD3820"
    assert abs(keyed(rebuilt,bar_key)["w"] / original_width - 4.2 / 4.5) < 1e-8
    assert any("Improved onboarding" in w and "4.3" in w for w in rebuilt["warnings"])
    assert note in rebuilt["elements"] and all(e["element_key"] != deleted_bar for e in rebuilt["elements"])
    bindings = {b["path"]:b for b in rebuilt["spec"]["value_bindings"]}
    assert bindings["rows/2/cells/1"]["status"] == "unverified"
    assert bindings["rows/0/cells/1"]["status"] == "verified"
    assert bindings["scores/2"]["calculation"] == "weighted-average"
    visual["rows"][2]["cells"][1] = 5
    restored = client.post(f'{path}/slides/{draft["id"]}/exhibit', json={
        "revision":item["revision"],"slide":rebuilt,"visual":visual})
    assert restored.is_success and not any("Review the recommendation" in w for w in restored.json()["warnings"])
    saved = client.patch(path + "/slides/" + draft["id"], json={"slide":rebuilt,"revision":item["revision"]})
    assert saved.is_success, saved.text
    pptx = client.post(path + "/export", params={"format":"pptx"}).content
    native = Presentation(io.BytesIO(pptx))
    table = next(s.table for s in native.slides[0].shapes if s.has_table)
    assert table.cell(3,4).text == "4.2"
    obj = next(s for s in native.slides[1].shapes if s.has_chart)
    assert list(obj.chart.series[1].values) == [4,3,5]
    with ZipFile(io.BytesIO(pptx)) as package:
        workbook = next(name for name in package.namelist() if name.startswith("ppt/embeddings/") and name.endswith(".xlsx"))
        with ZipFile(io.BytesIO(package.read(workbook))) as embedded:
            sheet = ElementTree.fromstring(embedded.read("xl/worksheets/sheet1.xml"))
            assert [sheet.find(f".//{{*}}c[@r='C{i}']/{{*}}v").text for i in (2,3,4)] == ["4","3","5"]
