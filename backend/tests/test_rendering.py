import io
from zipfile import ZipFile

from PIL import Image as PILImage
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from app.domain import Chart, Document, Group, Image, Line, Shape, Slide, Table, Text
from app.powerpoint import to_pptx
from app.rendering import preview, validate_slide


def test_native_objects_and_embedded_data():
    image = io.BytesIO()
    PILImage.new("RGB", (10, 10), "#4433cc").save(image, "PNG")
    slide = Slide(title="Native components", notes="Presenter note.", elements=[
        Text(x=.1, y=.08, w=.3, h=.1, text="Selectable native text", opacity=.75),
        Shape(x=.1, y=.19, w=.2, h=.15, shape="ellipse", opacity=.5),
        Line(x=.35, y=.21, w=.2, h=.1, opacity=.7),
        Image(x=.1, y=.36, w=.18, h=.14, asset_id="test-image", alt="Test image"),
        Table(x=.37, y=.37, w=.5, h=.16, rows=[["Metric", "Value"], ["Sales", "12"]]),
        Chart(x=.1, y=.55, w=.5, h=.33, categories=["Jan", "Feb"], series=[{"name": "Sales", "values": [8, 12]}]),
        Group(x=.65, y=.6, w=.2, h=.25, elements=[Shape(x=.1, y=.1, w=.7, h=.7)]),
    ])
    document = Document(title="Native components", aspect_ratio="4:3", slides=[slide])
    data = to_pptx(document, lambda _: image.getvalue())
    with ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        assert "ppt/charts/chart1.xml" in archive.namelist()
        assert any(name.endswith(".xlsx") for name in archive.namelist())
        assert any(name.startswith("ppt/media/") for name in archive.namelist())
    export = Presentation(io.BytesIO(data))
    assert export.slide_width / export.slide_height == 4/3
    shapes = list(export.slides[0].shapes)
    assert [shape.shape_type for shape in shapes] == [MSO_SHAPE_TYPE.TEXT_BOX, MSO_SHAPE_TYPE.AUTO_SHAPE,
         MSO_SHAPE_TYPE.LINE, MSO_SHAPE_TYPE.PICTURE, MSO_SHAPE_TYPE.TABLE, MSO_SHAPE_TYPE.CHART, MSO_SHAPE_TYPE.GROUP]
    assert shapes[0].text == "Selectable native text"
    assert shapes[4].table.cell(1, 1).text == "12"
    assert list(shapes[5].chart.series[0].values) == [8.0, 12.0]
    assert "Presenter note." in export.slides[0].notes_slide.notes_text_frame.text
    png = PILImage.open(io.BytesIO(preview(document, slide, lambda _: image.getvalue())))
    assert png.size == (1000, 750)


def test_fit_validator_reduces_text_size():
    element = Text(x=.2, y=.2, w=.3, h=.12, text="A heading that wraps at very large font sizes", size=60)
    slide = Slide(title="Fit", elements=[element])
    doc = Document(title="Fit", slides=[slide])
    validate_slide(doc, slide, set())
    assert 8 < element.size < 60
