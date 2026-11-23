"""Run quality briefs through the FrameCraft API and worker in an isolated database."""

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path


BRIEFS = json.loads(Path(__file__).with_name("benchmark_briefs.json").read_text(encoding="utf-8"))


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
            raise RuntimeError(f"{brief['id']} stopped at {item['status']}: {item.get('error') or 'see worker log'}")
        break
    else:
        raise RuntimeError(f"{brief['id']} exceeded the benchmark checkpoint limit")

    seconds = round(time.monotonic() - start, 2)
    document = item["document"]
    if len(document["slides"]) != brief["slide_count"]:
        raise RuntimeError(f"{brief['id']} returned {len(document['slides'])} slides")
    for kind in ("pdf", "pptx"):
        result = checked(client.post(f"{path}/export", params={"format": kind}))
        (output / f"deck.{kind}").write_bytes(result.content)
    for index, slide in enumerate(document["slides"], 1):
        preview = checked(client.get(f"{path}/slides/{slide['id']}/preview"))
        (output / f"slide-{index:02}.png").write_bytes(preview.content)
    (output / "document.json").write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"seconds": seconds, "request": request, "questions": question_batches}
    if brief.get("image"):
        report["image_sha256"] = hashlib.sha256(launch_image.read_bytes()).hexdigest()
    report["slides"] = export_checks(output, document)
    (output / "run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
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

    if not provider_configured(settings(), provider_for(settings())):
        raise RuntimeError("These briefs require a live generation provider")
    Base.metadata.create_all(engine)
    failed = []
    try:
        with Session.begin() as db:
            db.add(User(id=LOCAL_USER, email="benchmark@framecraft.test"))
            seed(db)
        with TestClient(app) as client:
            for brief in briefs:
                folder = output / brief["id"]
                folder.mkdir(parents=True)
                try:
                    generate(client, brief, folder, launch_image)
                except RuntimeError as error:
                    (folder / "failure.json").write_text(json.dumps({"error": str(error)}, indent=2), encoding="utf-8")
                    print(f"{brief['id']}: {error}", file=sys.stderr, flush=True)
                    failed.append(brief["id"])
    finally:
        engine.dispose()
    return failed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Directory for exported decks and run details")
    parser.add_argument("--case", choices=[brief["id"] for brief in BRIEFS], action="append")
    parser.add_argument("--launch-image", type=Path, help="Supplied Cove lantern photo for the launch brief")
    args = parser.parse_args()
    briefs = [brief for brief in BRIEFS if not args.case or brief["id"] in args.case]
    if any(brief.get("image") for brief in briefs) and not (args.launch_image and args.launch_image.is_file()):
        parser.error("the product-launch brief requires --launch-image")
    with tempfile.TemporaryDirectory(prefix="framecraft-benchmark-") as scratch:
        return bool(run(briefs, args.output, args.launch_image, Path(scratch)))


if __name__ == "__main__":
    sys.exit(main())
