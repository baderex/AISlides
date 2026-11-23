import io

from fastapi.testclient import TestClient
from PIL import Image
from pptx import Presentation
from pptx.enum.text import PP_ALIGN
from sqlalchemy import select

from app.db import Job, Session
from app.domain import Document
from app.html_layout import compose
from app.main import app
from app.providers import Intelligence, ProviderError
from app.queue import claim
from app.workflow import advance


PALETTE = dict(background="#FAF9F5", ink="#17283C", accent="#16856B", muted="#58677A", surface="#FFFFFF",
               display="Georgia")
MATRIX = """<page><text font="display" size="70">FORM</text><row grow="1" align="center">
  <col w="100%" align="center"><row w="100%"><matrix grow="1" widths="70,30">
  <tr><td>Capability</td><td>Level</td></tr><tr><td>Inspection</td><td align="right" bold="true">4</td></tr>
  </matrix></row></col></row></page>"""


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
            return schema(markup=MATRIX if "correction" in data else "<page><script/></page>")
        raise AssertionError(f"Unexpected legacy generation request: {schema.__name__}")

    monkeypatch.setattr("app.providers.provider_for", lambda config: "compatible")
    monkeypatch.setattr("app.providers.provider_configured", lambda config, provider: True)
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
    assert calls.count("HtmlPlan") == 1 and calls.count("PageMarkup") == 2
    saved = item["document"]
    slide = saved["slides"][0]
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
