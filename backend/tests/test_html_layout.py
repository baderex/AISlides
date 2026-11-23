import io
from types import SimpleNamespace
from zipfile import ZipFile
from xml.etree import ElementTree

from fastapi.testclient import TestClient
from PIL import Image
from pptx import Presentation
from pptx.enum.text import PP_ALIGN
from sqlalchemy import select

from app.db import Job, Session
from app.domain import Document, Shape
from app.html_layout import compose
from app.html_layout.markup import PagePlan
from app.main import app
from app.providers import Intelligence, ProviderError
from app.queue import claim
from app.workflow import advance, html_slide_inputs


PALETTE = dict(background="#FAF9F5", ink="#17283C", accent="#16856B", muted="#58677A", surface="#FFFFFF",
               display="Georgia")
MATRIX = """<page><text font="display" size="70">FORM</text><row grow="1" align="center">
  <col w="100%" align="center"><row w="100%"><matrix grow="1" widths="70,30">
  <tr><td>Capability</td><td>Level</td></tr><tr><td>Inspection</td><td align="right" bold="true">4</td></tr>
  </matrix></row></col></row></page>"""


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


def test_live_route_checkpoints_browser_retry_and_exports_edited_objects(monkeypatch):
    calls = []

    def model(self, schema, task, data, stage="planning", image=None):
        calls.append(schema.__name__)
        if schema.__name__ == "Intent":
            return schema(subject="Inspection", audience="Board", visual_direction="Editorial", slide_count=1)
        if schema.__name__ == "Clarification":
            return schema(questions=[])
        if schema.__name__ == "HtmlPlan":
            return schema(title="Inspection", **PALETTE, slides=[dict(title="Inspection", direction="Show the table")])
        if schema.__name__ == "PageMarkup":
            return schema(markup="<page><script/></page>", notes="Keep these presenter notes.")
        if schema.__name__ == "LayoutMarkup":
            assert data["previous"] == "<page><script/></page>" and "correction" in data
            assert {"research", "sources", "outline", "previous_exhibit_data"}.isdisjoint(data)
            return schema(markup=MATRIX)
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
        if schema.__name__ == "Intent":
            return schema(subject="Northstar", slide_count=2, supplied_figures=True, requires_chart_workbook=True)
        if schema.__name__ == "Clarification":
            return schema(questions=[])
        if schema.__name__ == "HtmlPlan":
            assert data["requires_chart_workbook"]
            return schema(title="Northstar", **PALETTE, slides=[
                dict(title="Northstar decision", layout="research", exhibit="decision-matrix", direction="Compare initiatives"),
                dict(title="Score inputs", layout="chart", exhibit="workbook-chart", direction="Compare the raw scores")])
        assert schema.__name__ == "ExhibitPage"
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
