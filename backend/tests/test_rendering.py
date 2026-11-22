import io
from zipfile import ZipFile

import pytest
from PIL import Image as PILImage
from PIL import ImageChops
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from app.composition import OutlineItem, Point, SlideContent, compose
from app.consulting import consulting_theme
from app.design import style_table
from app.domain import Chart, Document, Group, Image, Line, Shape, Slide, Table, Text
from app.powerpoint import to_pptx
from app.rendering import physical, preview, table_columns, table_insets, table_text_fits, validate_slide


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


@pytest.mark.parametrize("aspect", ["16:9", "4:3", "1:1"])
def test_ruled_tables_adapt_to_available_space(aspect):
    doc = Document(title="Operating expenses", aspect_ratio=aspect)
    rows = [["Component", "Current", "New"], ["Maintenance", "$250", "$180"], ["Supplies", "$160", "$110"]]
    table = Table(x=.60, y=.48, w=.32, h=.25, rows=rows)
    style_table(table, doc.theme)
    assert table.numeric_columns == [1, 2]
    assert table.rows == rows
    width, height = physical(doc)
    columns = table_columns(table, table.w * width)
    assert columns[0][1] > columns[1][1] == columns[2][1]
    assert sum(size for _, size in columns) == pytest.approx(table.w * width)
    assert table_insets(table, columns[1][1], table.h * height / len(rows))[0] < 12
    assert validate_slide(doc, Slide(title=doc.title, elements=[table]), set()) == []

    wide = table.model_copy(update={"x": .07, "w": .86})
    style_table(wide, doc.theme)
    assert [size for _, size in table_columns(wide, wide.w * width)] == pytest.approx([wide.w * width / 3] * 3)


@pytest.mark.parametrize("theme", [{}, consulting_theme()])
def test_point_icons_fit_square_slides_and_skip_evidence_exhibits(theme):
    doc = Document(title="Secure handoff", aspect_ratio="1:1", theme=theme)
    part = OutlineItem(title=doc.title, layout="process")
    content = SlideContent(title=doc.title, points=[Point(title="Review"), Point(title="Protect", icon=" Shield "),
                                                    Point(title="Deliver", icon="missing-icon")])
    slide = compose(part, content, doc, 0, set())
    icon, = [element for element in slide.elements if isinstance(element, Image)]
    assert icon.w == pytest.approx(icon.h)
    assert icon.asset_id.startswith("lucide-shield_")
    assert any(isinstance(element, Text) and element.text == "02" for element in slide.elements)
    assert validate_slide(doc, slide, set()) == []

    part.layout = "research"
    content.table = [["Step", "Purpose"], ["Protect", "Secure handoff"]]
    slide = compose(part, content, doc, 0, set())
    assert any(isinstance(element, Table) for element in slide.elements)
    assert not any(isinstance(element, Image) for element in slide.elements)


def test_farming_headline_renders_completely_at_multiple_preview_widths():
    headline = "Water Scarcity and Agricultural Importance Define Investment Opportunity Across Middle Eastern Countries"
    doc = Document(title="Farming", theme=consulting_theme())
    part = OutlineItem(title=headline, layout="comparison")
    slide = compose(part, SlideContent(title=headline), doc, 2, set())
    title = next(element for element in slide.elements if isinstance(element, Text))
    assert validate_slide(doc, slide, set()) == []
    assert title.text == headline

    # A taller frame is a reference render with room for every line of the same heading.
    reference = Slide(title=headline, elements=[title.model_copy(update={"h": .35})], background=slide.background)
    fitted = Slide(title=headline, elements=[title], background=slide.background)
    for width in (660, 960, 1000, 1920):
        def ink(frame):
            raw = preview(doc, frame, lambda _: b"", pixel_width=width)
            image = PILImage.open(io.BytesIO(raw))
            white = PILImage.new("RGB", image.size, slide.background)
            return image.height, ImageChops.difference(image, white).getbbox()

        height, full = ink(reference)
        _, actual = ink(fitted)
        assert full[3] <= round((title.y + title.h) * height) + 1
        assert abs(full[3] - actual[3]) <= 2


@pytest.mark.parametrize("aspect", ["16:9", "4:3", "1:1"])
def test_fit_repairs_grouped_text_and_headerless_tables_and_reports_impossible_text(aspect):
    doc = Document(title="Verification", aspect_ratio=aspect)
    table = Table(x=.08, y=.38, w=.68, h=.22, size=24, rows=[
        ["Topic", "Explain the expected outcome in plain language."],
        ["Timing", "Review the result after answering the practice question."],
    ])
    heading = Text(x=0, y=0, w=.8, h=.6, size=36, text="A long headline inside a grouped frame")
    crowded = Text(x=.8, y=.992, w=.05, h=.007, size=12, text="Too much text for this frame.")
    slide = Slide(title="Fit", layout="research", elements=[
        Group(x=.08, y=.08, w=.65, h=.24, elements=[heading]), table, crowded,
    ])
    warnings = validate_slide(doc, slide, set())
    width, height = physical(doc)
    assert 8 < heading.size < 36
    assert 8 < table.size < 24 and table_text_fits(table, table.w * width, table.h * height)
    assert crowded.size == 8 and warnings == ["Text may overflow: Too much text for this frame."]


def test_fit_expands_a_tight_frame_into_free_space():
    footer = Text(x=.06, y=.88, w=.58, h=.025, size=9, text="Sources: " + "A detailed published reference. " * 5)
    page = Text(x=.88, y=.91, w=.06, h=.03, text="03", size=9)
    slide = Slide(title="References", elements=[footer, page])
    assert validate_slide(Document(title="References"), slide, set()) == []
    assert footer.h > .025 and footer.y + footer.h <= 1
    assert page.h == .03
