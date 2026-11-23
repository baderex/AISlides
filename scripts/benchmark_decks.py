"""Run quality briefs through the FrameCraft API and worker in an isolated database."""

import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch


BRIEFS = json.loads(Path(__file__).with_name("benchmark_briefs.json").read_text(encoding="utf-8"))
WEB_BRIEF = {
    "id": "web-research", "slide_count": 3, "research": True,
    "prompt": ("Generate exactly three 16:9 slides on US annual average unemployment rates from 2020 through 2024. "
               "Use web research and prefer official Bureau of Labor Statistics sources. Slide 1: context and "
               "main finding. Slide 2: an editable line chart of the five annual rates, with years and percentages. "
               "Slide 3: a concise interpretation and dated source references. Do not invent missing observations. "
               "Generate directly in a restrained, readable financial-report style."),
}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_error(error):
    message = str(error)
    for name in ("LLM_API_KEY", "RESEARCH_API_KEY", "IMAGE_GEN_API_KEY", "IMAGE_OPENAI_API_KEY"):
        if secret := os.environ.get(name):
            message = message.replace(secret, "[redacted]")
    return message


def synthetic_launch_image(path):
    """Deterministic supplied-image fixture, not an image-generation API call."""
    from PIL import Image, ImageDraw

    picture = Image.new("RGB", (1200, 800), "#203C34")
    draw = ImageDraw.Draw(picture)
    draw.rectangle((0, 590, 1200, 800), fill="#90775B")
    for radius in range(280, 100, -4):
        weight = (280 - radius) / 180
        shade = tuple(round(a + (b-a)*weight) for a, b in zip((32,60,52), (150,126,73)))
        draw.ellipse((600-radius, 365-radius, 600+radius, 365+radius), fill=shade)
    draw.rounded_rectangle((452, 205, 748, 610), radius=36, fill="#172D27")
    draw.rounded_rectangle((485, 260, 715, 533), radius=24, fill="#F9D992")
    draw.arc((518, 96, 682, 320), 180, 360, fill="#C7D0BC", width=15)
    draw.rounded_rectangle((485, 550, 715, 590), radius=10, fill="#435A4C")
    picture.save(path)
    return path


def office_review(output, raw_document):
    from app.domain import Document
    from app.export_review import inspect_pdf, render_native_pdf, renderer

    if not (executable := renderer()):
        raise RuntimeError("LibreOffice Impress is required for the Office-render benchmark")
    raw = render_native_pdf(executable, (output / "deck.pptx").read_bytes())
    (output / "office-render.pdf").write_bytes(raw)
    findings = inspect_pdf(raw, Document.model_validate(raw_document))
    for page in findings:
        (output / f"office-slide-{page['number']:02}.jpg").write_bytes(base64.b64decode(page.pop("image")))
    return findings


def contact_sheet(output, pattern, filename):
    from PIL import Image, ImageDraw, ImageOps

    paths = sorted(output.glob(pattern))
    if not paths:
        return
    cell_w, cell_h = 640, 408
    columns = min(2, len(paths))
    sheet = Image.new("RGB", (cell_w * columns, cell_h * ((len(paths) + columns - 1)//columns)), "#E6E8EC")
    draw = ImageDraw.Draw(sheet)
    for i, path in enumerate(paths):
        x, y = (i % columns)*cell_w, (i // columns)*cell_h
        with Image.open(path) as image:
            thumb = ImageOps.contain(image.convert("RGB"), (cell_w-24, cell_h-40))
            sheet.paste(thumb, (x+(cell_w-thumb.width)//2, y+24))
        draw.text((x+12, y+8), path.stem, fill="#17202A")
    sheet.save(output / filename)



def checked(response):
    if response.is_error:
        raise RuntimeError(f"{response.request.method} {response.request.url.path}: {response.text[:500]}")
    return response


def export_checks(output, raw_document):
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    from app.domain import Chart, Document, Table, Text
    from app.export_review import inspect_pdf
    from app.rendering import flattened

    document = Document.model_validate(raw_document)
    native = Presentation(output / "deck.pptx")
    if len(native.slides) != len(document.slides):
        raise RuntimeError("PowerPoint slide count differs from the generated deck")
    pdf = inspect_pdf((output / "deck.pdf").read_bytes(), document)

    def native_shapes(shapes):
        for shape in shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                yield from native_shapes(shape.shapes)
            else:
                yield shape

    checks = []
    for intent, exported, rendered in zip(document.slides, native.slides, pdf):
        elements = [element for element, *_ in flattened(intent.elements)]
        all_shapes = list(native_shapes(exported.shapes))
        key_names = {el.id + ":key" for el in elements if isinstance(el, Chart) and el.needs_key}
        keys = {s.name: s.table for s in all_shapes if s.has_table and s.name in key_names}
        if keys.keys() != key_names:
            raise RuntimeError(f"PowerPoint chart data key missing on slide {rendered['number']}")
        axis_prefixes = tuple(el.id + ":axis:" for el in elements if isinstance(el, Chart) and el.needs_key)
        shapes = [s for s in all_shapes if s.name not in keys and not s.name.startswith(axis_prefixes)]
        charts = [shape.chart for shape in shapes if shape.has_chart]
        tables = [shape.table for shape in shapes if shape.has_table]
        texts = [shape.text for shape in shapes if shape.has_text_frame and shape.text]
        counts = {"chart": len(charts), "table": len(tables), "text": len(texts),
                  "line": sum(shape.shape_type == MSO_SHAPE_TYPE.LINE for shape in shapes),
                  "image": sum(shape.shape_type == MSO_SHAPE_TYPE.PICTURE for shape in shapes)}
        if any(sum(element.type == kind for element in elements) != count for kind, count in counts.items()):
            raise RuntimeError(f"PowerPoint objects differ on slide {rendered['number']}")
        if [element.text for element in elements if isinstance(element, Text)] != texts:
            raise RuntimeError(f"PowerPoint text differs on slide {rendered['number']}")
        for element, table in zip((el for el in elements if isinstance(el, Table)), tables):
            rows = [[table.cell(i, j).text for j in range(len(table.columns))] for i in range(len(table.rows))]
            if rows != element.rows:
                raise RuntimeError(f"PowerPoint table values differ on slide {rendered['number']}")
        for element, chart in zip((el for el in elements if isinstance(el, Chart)), charts):
            if [list(series.values) for series in chart.series] != [series.values for series in element.series]:
                raise RuntimeError(f"PowerPoint chart values differ on slide {rendered['number']}")
            if element.needs_key:
                key = keys[element.id + ":key"]
                for j, series in enumerate(element.series, 1):
                    if any(key.cell(i, j).text != element.format_value(v) for i, v in enumerate(series.values, 1)):
                        raise RuntimeError(f"PowerPoint chart key values differ on slide {rendered['number']}")
        checks.append({"slide": rendered["number"], "title": intent.title, "layout": intent.layout,
                       "native_objects": counts, "generation_warnings": intent.warnings,
                       "pdf_findings": rendered["issues"]})
    return checks


def generate(client, brief, output, launch_image):
    from app.db import Session
    from app.queue import claim
    from app.presentations import latest_job
    from app.workflow import advance

    asset_ids = []
    if brief.get("image"):
        with launch_image.open("rb") as image:
            asset = checked(client.post("/api/v1/assets", files={"file": (launch_image.name, image)})).json()
        asset_ids.append(asset["id"])
    request = {"prompt": brief["prompt"], "asset_ids": asset_ids, "research": brief.get("research", False),
               "mode": brief.get("mode", "professional"), "aspect_ratio": brief.get("aspect_ratio", "16:9")}
    start = time.monotonic()
    item = checked(client.post("/api/v1/presentations", json=request)).json()
    path = f"/api/v1/presentations/{item['id']}"
    question_batches = []
    for _ in range(150):
        with Session() as db:
            ticket = claim(db)
        if ticket:
            advance(*ticket)
            continue
        item = checked(client.get(path)).json()
        if item["status"] == "WAITING_FOR_USER":
            batch = checked(client.get(f"{path}/questions")).json()["questions"]
            question_batches.append(batch)
            answers = [{"question_id": question["id"], "use_default": True} for question in batch]
            checked(client.post(f"{path}/answers", json={"answers": answers}))
            continue
        if item["status"] != "COMPLETED":
            with Session() as db:
                job = latest_job(db, item["id"])
                error = job.error if job else None
                usage = job.context.get("usage") if job else None
            write_json(output / "document.json", item["document"])
            write_json(output / "run.json", {"request": request, "questions": question_batches,
                       "status": item["status"], "usage": usage, "error": safe_error(error or "see worker log")})
            raise RuntimeError(f"{brief['id']} stopped at {item['status']}: {error or 'see worker log'}")
        break
    else:
        raise RuntimeError(f"{brief['id']} exceeded the benchmark checkpoint limit")

    seconds = round(time.monotonic() - start, 2)
    document = item["document"]
    with Session() as db:
        job = latest_job(db, item["id"])
        usage = job.context.get("usage") if job else None
    write_json(output / "document.json", document)
    report = {"seconds": seconds, "request": request, "questions": question_batches,
              "usage": usage, "status": item["status"]}
    if brief.get("image"):
        report["image_sha256"] = hashlib.sha256(launch_image.read_bytes()).hexdigest()
    write_json(output / "run.json", report)
    if len(document["slides"]) != brief["slide_count"]:
        raise RuntimeError(f"{brief['id']} returned {len(document['slides'])} slides")
    for kind in ("pdf", "pptx"):
        result = checked(client.post(f"{path}/export", params={"format": kind}))
        (output / f"deck.{kind}").write_bytes(result.content)
    for index, slide in enumerate(document["slides"], 1):
        preview = checked(client.get(f"{path}/slides/{slide['id']}/preview"))
        (output / f"slide-{index:02}.png").write_bytes(preview.content)
    contact_sheet(output, "slide-*.png", "preview-contact-sheet.png")
    report["slides"] = export_checks(output, document)
    write_json(output / "run.json", report)
    if brief["id"] == WEB_BRIEF["id"] and not any(
            source.get("url") for source in document["sources"]):
        raise RuntimeError("Web research case returned no cited web sources")
    if os.environ.get("BENCHMARK_OFFICE_REVIEW") == "1":
        report["office_findings"] = office_review(output, document)
        contact_sheet(output, "office-slide-*.jpg", "office-contact-sheet.png")
        write_json(output / "run.json", report)
    print(f"{brief['id']}: {len(document['slides'])} slides in {seconds}s", flush=True)


def run(briefs, output, launch_image, scratch):
    from fastapi.testclient import TestClient

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    os.environ.update({
        "APP_ENV": "development",
        "DATABASE_URL": f"sqlite+aiosqlite:///{scratch.resolve().as_posix()}/benchmark.db",
        "LOCAL_STORAGE_PATH": str(scratch / "uploads"),
        "STORAGE_BACKEND": "local",
        "JOB_BACKEND": "local",
        "REDIS_ENABLED": "false",
        "LOCAL_SESSION": "true",
    })
    from app.auth import LOCAL_USER
    from app.db import Base, Session, User, engine
    from app.main import app
    from app.config import settings
    from app.providers import provider_configured, provider_for
    from app.skills import seed
    from app import artifacts

    def drain_artifacts(_job_id):
        # TestClient waits for uploads/previews; service the real durable queue
        # synchronously, without needing another worker process.
        for _ in range(4):
            with Session() as db:
                ticket = artifacts.claim_artifact(db, "benchmark-artifact")
            if not ticket:
                return
            artifacts.execute(*ticket)

    if not provider_configured(settings(), provider_for(settings())):
        raise RuntimeError("These briefs require a live generation provider")
    Base.metadata.create_all(engine)
    failed = []
    try:
        with Session.begin() as db:
            db.add(User(id=LOCAL_USER, email="benchmark@framecraft.test"))
            seed(db)
        with patch.object(artifacts, "notify", drain_artifacts), TestClient(app) as client:
            for brief in briefs:
                folder = output / brief["id"]
                folder.mkdir(parents=True, exist_ok=True)
                try:
                    generate(client, brief, folder, launch_image)
                except Exception as error:
                    message = safe_error(error)
                    write_json(folder / "failure.json", {"error": message, "type": type(error).__name__})
                    print(f"{brief['id']}: {message}", file=sys.stderr, flush=True)
                    failed.append(brief["id"])
    finally:
        engine.dispose()
    return failed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Directory for exported decks and run details")
    parser.add_argument("--case", choices=[brief["id"] for brief in BRIEFS], action="append")
    parser.add_argument("--launch-image", type=Path, help="Supplied Cove lantern photo for the launch brief")
    parser.add_argument("--ci-fixture", action="store_true", help="Use an original synthetic supplied lantern image")
    parser.add_argument("--web-research", action="store_true", help="Append an opt-in live web-research case")
    args = parser.parse_args()
    briefs = [brief for brief in BRIEFS if not args.case or brief["id"] in args.case]
    if args.ci_fixture and args.launch_image:
        parser.error("choose either --launch-image or --ci-fixture")
    if args.web_research:
        if not os.environ.get("RESEARCH_API_KEY"):
            parser.error("--web-research requires RESEARCH_API_KEY in the process environment")
        briefs.append(WEB_BRIEF)
    if any(brief.get("image") for brief in briefs) and not (args.ci_fixture or
            args.launch_image and args.launch_image.is_file()):
        parser.error("the product-launch brief requires --launch-image or --ci-fixture")
    with tempfile.TemporaryDirectory(prefix="framecraft-benchmark-") as scratch:
        image = args.launch_image
        if args.ci_fixture and any(brief.get("image") for brief in briefs):
            image = synthetic_launch_image(Path(scratch) / "cove-ci-fixture.png")
            briefs = [{**brief, "prompt": brief["prompt"]
                       .replace("lantern photo", "lantern illustration")
                       .replace("full photograph", "full illustration")
                       .replace("The photo illustrates", "The illustration represents")
                       .replace("'Working By Emergency Light' by Alan Levine (cogdogblog), CC BY 2.0",
                                "'AISlides synthetic test fixture'")
                       .replace("with the photo", "with the illustration")
                       .replace("Use the photo only once", "Use the illustration only once")}
                      if brief.get("image") else brief for brief in briefs]
        args.output.mkdir(parents=True, exist_ok=True)
        failed = run(briefs, args.output, image, Path(scratch))
        write_json(args.output / "summary.json", {"cases": [brief["id"] for brief in briefs], "failed": failed,
                   "passed": [brief["id"] for brief in briefs if brief["id"] not in failed]})
        return bool(failed)


if __name__ == "__main__":
    sys.exit(main())
