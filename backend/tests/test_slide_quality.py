from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace

from PIL import Image as PILImage
from pptx import Presentation
from pypdf import PdfReader
import pytest

from app.chart_key import chart_key
from app.composition import OutlineItem, SlideContent, compose
from app.composition_models import StackedComparison
from app.deck_design import baseline_rules
from app.design import style_table
from app.domain import Chart, Document, Image, Shape, Slide, Table, Text, Theme
from app.evidence import commit_comparison
from app.exhibit_canvas import ExhibitCanvas
from app.exhibit_layout import quantitative
from app.html_layout.markup import PagePlan
from app.html_layout.review import evaluate_quality
from app.powerpoint import to_pptx
from app.presentations import asset_sources
from app.render_geometry import contrast, fit_slide_text, pdf_lines, raster_text
from app.rendering import preview, to_pdf
from app.workflow import evidence_inputs, html_slide_inputs, slide_inputs


@pytest.mark.parametrize("kind", ["pie", "column", "bar"])
def test_workbook_categories_series_and_magnitudes_survive_export(kind):
    series = [{"name": "Revenue", "values": [50, 30, 20]}]
    if kind == "column":
        series.append({"name": "Cost", "values": [40, -15, 10]})
    chart = Chart(x=.05, y=.1, w=.9, h=.7, chart=kind, categories=["Enterprise", "Consumer", "Government"],
                  series=series, value_suffix="m", element_key="workbook:chart")
    slide = Slide(title="Accounts", elements=[chart])
    deck = Document(title=slide.title, slides=[slide])
    assert chart_key(chart, 800, 300)[2] == []
    assert chart_key(chart, 60, 12)[2]
    pdf = PdfReader(BytesIO(to_pdf(deck, {}.get))).pages[0].extract_text()
    for text in [*chart.categories, *(s.name for s in chart.series), "50m", "30m", "20m"]:
        assert text in pdf
    image = PILImage.open(BytesIO(preview(deck, slide, {}.get)))
    assert len(image.crop((600, 160, 930, 380)).getcolors(100000)) > 5
    native = Presentation(BytesIO(to_pptx(deck, {}.get))).slides[0].shapes[0].chart
    assert native.has_legend
    if kind == "pie":
        assert native.chart_title.text_frame.text == "Revenue"
        assert native.plots[0].data_labels.show_category_name and native.plots[0].data_labels.show_value
    chart.series[0].name = " "
    assert evaluate_quality(slide, []).hard


def test_late_evidence_reaches_planning_and_both_slide_authors():
    tail = "Conclusion: maintenance fell 18%.\nQuarter | Savings\nQ3 | $82,500"
    asset = SimpleNamespace(id="report", name="Report.txt", extracted="General background. " * 550 + "\n" + tail)
    source, = asset_sources([asset])
    assert source.id == asset_sources([asset])[0].id and tail in source.excerpt
    ctx = {"sources": [source.model_dump(mode="json")]}
    item = SimpleNamespace(request={"prompt": "Show maintenance savings and the quarterly table."})
    html = PagePlan(title="Maintenance savings", direction="Quantify maintenance savings", source_ids=[source.id])
    native = OutlineItem(title=html.title, layout="metrics", required_details=["Quarterly savings"])
    for packet in (evidence_inputs(item, ctx), html_slide_inputs(item, ctx, html), slide_inputs(item, ctx, native)):
        excerpt, = packet["sources"]
        assert excerpt["id"] == source.id and tail in excerpt["excerpt"]
        assert len(excerpt["excerpt"]) < 3000


def test_theme_roles_type_floors_and_metric_identity():
    theme = Theme(background="#FFFFFF", ink="#191919", accent="#B5893B", font="Manrope")
    theme.design = baseline_rules(theme, "corporate")
    deck = Document(title="Costs", aspect_ratio="1:1", theme=theme)
    canvas = ExhibitCanvas(deck, Slide(title="Sales", background="#FFFFFF"), (0,0,1,1))
    rows = [SimpleNamespace(label=k, values=[v]) for k, v in (("North", 8), ("West", 4))]
    quantitative(canvas, SimpleNamespace(type="ranked-bar", rows=rows, highlight="North"))
    bars = [e for e in canvas.slide.elements if isinstance(e, Shape) and (e.element_key or "").endswith(":bar")]
    assert [e.fill for e in bars] == ["#B5893B", canvas.colors["neutral"]]
    assert all(contrast(e.fill, "#FFFFFF") >= 3 for e in bars)
    table = Table(x=.1,y=.1,w=.8,h=.6,rows=[["a","b"],["1","2"]])
    style_table(table, theme, preserve_palette=True)
    assert table.font == "Manrope" and table.size == 18
    body = "A maintenance team uses the quarterly results to decide which costs to review. " * 2
    content = SlideContent(title="Operations", points=[{"title": "North", "body": body}, {"title": "South", "body": body}])
    slide = compose(OutlineItem(title=content.title, layout="metrics"), content, deck, 0, set())
    warnings = fit_slide_text(deck, slide)
    paragraphs = [el for el in slide.elements if isinstance(el, Text) and el.role == "body"]
    assert len(paragraphs) == 2 and all(el.size >= 16 for el in paragraphs)
    assert warnings == []
    data = StackedComparison(label="Revenue", categories=["Product A", "Product B"],
                             series=[dict(name=s, values=[10,15]) for s in ("Retail","Wholesale")])
    title = commit_comparison(data, [[Decimal(10), Decimal(15)]] * 2, "$")
    assert "revenue" in title.lower() and "cost" not in title.lower()
    assert "33%" in title and "Product A" in title and "Product B" in title


def test_whitespace_crops_and_corner_radius_survive_export():
    code = "if ready:\n    emit(  value)"
    lines = [line for _, _, line in raster_text(code, 700, 200, 16, False, "IBM Plex Mono").lines]
    assert lines == code.splitlines()
    assert list(pdf_lines(code, 350, "Courier", 16)) == lines
    picture = PILImage.new("RGB", (200,100), "red")
    picture.paste("blue", (100,0,200,100))
    stream = BytesIO()
    picture.save(stream, "PNG")
    get_asset = lambda _: stream.getvalue()
    image = Image(x=0,y=0,w=.5,h=.5,asset_id="photo",fit="cover",focal_x=1)
    card = Shape(x=.5,y=.5,w=.4,h=.4,shape="round",radius_pt=6)
    deck = Document(title="Crop", aspect_ratio="1:1", slides=[Slide(title="Crop",elements=[image,card])])
    raster = PILImage.open(BytesIO(preview(deck, deck.slides[0], get_asset, 540)))
    assert raster.getpixel((12,12)) == (0,0,255)
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(to_pdf(deck, get_asset)) as pdf:
        page = pdf[0].render().to_pil()
        assert page.getpixel((12, 12))[:3] == (0, 0, 255)
    photo, shape = Presentation(BytesIO(to_pptx(deck, get_asset))).slides[0].shapes
    assert photo.crop_left == pytest.approx(.5) and photo.crop_right == 0
    assert shape.adjustments[0] == pytest.approx(6 / min(shape.width.pt, shape.height.pt), abs=.0001)
