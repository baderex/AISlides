from io import BytesIO
from itertools import combinations

from PIL import Image as PILImage
from pptx import Presentation
from pypdf import PdfReader
from pypdf.generic import ContentStream
from reportlab.pdfbase import pdfmetrics
import pytest

from app.chart_key import chart_layout, measure_chart
from app.deck_design import DeckDesignSpec, baseline_rules
from app.design import style_chart
from app.domain import BrandKit, Chart, DesignConstraints, Document, Slide, Theme
from app.palette import series_distinguishable
from app.powerpoint import to_pptx
from app.render_geometry import contrast, pdf_face, validate_slide
from app.rendering import preview, to_pdf


def restrained_theme(colors=("#B5893B", "#414141")):
    theme = Theme(background="#FFFFFF", ink="#191919", accent=colors[0], muted="#555555", font="Manrope")
    theme.design = baseline_rules(theme, "financial")
    theme.design.chart_colors = list(colors)
    return theme


def workbook(kind="line", *, categories=None, count=4, theme=None):
    theme = theme or restrained_theme()
    categories = categories or ["Q1", "Q2", "Q3", "Q4"]
    chart = Chart(x=.05, y=.1, w=.9, h=.8, chart=kind, element_key="workbook:chart", categories=categories,
                  series=[dict(name=name, values=[20 + 3*i + j for i in range(len(categories))])
                          for j, name in enumerate(("North", "South", "East", "West", "Central")[:count])])
    style_chart(chart, theme, theme.background)
    slide = Slide(title="Regional results", background=theme.background, elements=[chart])
    return Document(title=slide.title, theme=theme, slides=[slide]), slide, chart


def test_restrained_series_have_visible_tone_steps_in_every_export():
    deck, slide, chart = workbook()
    assert chart.palette[:2] == ["#B5893B", "#414141"]
    assert len(chart.palette) == 4
    # Gold or neutral tonal variants keep the requested art direction. Near-black
    # hex differences alone must not count as distinct series.
    assert all(int(c[1:3], 16) >= int(c[3:5], 16) >= int(c[5:7], 16) for c in chart.palette)
    assert all(contrast(c, slide.background) >= 3 for c in chart.palette)
    neutrals = [c for c in chart.palette if c[1:3] == c[3:5] == c[5:7]]
    assert all(contrast(a, b) >= 1.5 for a, b in combinations(neutrals, 2))
    assert not validate_slide(deck, slide, set())

    raw = preview(deck, slide, {}.get, pixel_width=1280)
    image = PILImage.open(BytesIO(raw))
    layout = chart.composition
    plot = image.crop((64, 72, 64 + int(layout.plot_width * 1280 / 960), 72 + int(layout.plot_height * 1280 / 960)))
    pixels = {rgb: count for count, rgb in plot.getcolors(plot.width * plot.height)}
    for shade in chart.palette:
        assert pixels.get(tuple(int(shade[i:i+2], 16) for i in (1, 3, 5)), 0) > 50

    reader = PdfReader(BytesIO(to_pdf(deck, {}.get)))
    strokes = {tuple(round(float(value)*255) for value in args) for args, operator in
               ContentStream(reader.pages[0].get_contents(), reader).operations if operator == b"RG"}
    assert all(tuple(int(shade[i:i+2], 16) for i in (1, 3, 5)) in strokes for shade in chart.palette)
    shapes = Presentation(BytesIO(to_pptx(deck, {}.get))).slides[0].shapes
    native = next(shape.chart for shape in shapes if shape.has_chart)
    assert ["#" + str(series.format.line.color.rgb) for series in native.series] == chart.palette


def test_five_monochrome_series_use_separated_tones_instead_of_falling_back_to_near_black():
    deck, slide, chart = workbook(count=5, theme=restrained_theme(("#FAFAFA", "#FBFBFB")))
    assert len(chart.palette) == 5
    assert all(contrast(a, b) >= 1.5 for a, b in combinations(chart.palette, 2))
    assert not validate_slide(deck, slide, set())


@pytest.mark.parametrize("locked", ["brand", "request"])
def test_protected_similar_colors_are_reported_without_inventing_replacements(locked):
    theme = restrained_theme(("#000000", "#101010"))
    if locked == "brand":
        theme.brand_kit = BrandKit(protected=["chart_colors"])
    else:
        theme.constraints = DesignConstraints(palette=["#000000", "#101010"])
    deck, slide, chart = workbook(count=2, theme=theme)
    assert chart.palette == ["#000000", "#101010"]
    assert any("similar series colors" in warning for warning in validate_slide(deck, slide, set()))


def test_single_series_context_and_numbered_pie_colors_are_not_series_ambiguity():
    deck, slide, chart = workbook(count=1)
    chart.highlight, chart.context_color = 1, "#101010"
    chart.palette = ["#000000"]
    assert not validate_slide(deck, slide, set())
    chart.chart, chart.highlight = "pie", None
    chart.palette = ["#000000", "#101010"]
    assert not validate_slide(deck, slide, set())


@pytest.mark.parametrize("kind", ["line", "column", "bar", "stacked_column"])
def test_axis_labels_share_the_deck_floor_in_pdf_and_powerpoint(kind):
    deck, slide, chart = workbook(kind, count=2)
    assert not validate_slide(deck, slide, set())
    minimum = DeckDesignSpec.from_theme(deck.theme, deck.aspect_ratio, "chart").typography["label"].minimum_pt
    assert chart.composition.axis_size >= minimum == 12
    labels = []

    def collect(text, current_matrix, text_matrix, font, size):
        if any(name in text for name in chart.categories):
            labels.append(size)

    PdfReader(BytesIO(to_pdf(deck, {}.get))).pages[0].extract_text(visitor_text=collect)
    assert labels and min(labels) >= minimum
    shapes = Presentation(BytesIO(to_pptx(deck, {}.get))).slides[0].shapes
    axes = [shape for shape in shapes if shape.name.startswith(chart.id + ":axis:")]
    assert axes and all(shape.text_frame.paragraphs[0].font.size.pt == chart.composition.axis_size for shape in axes)
    native = next(shape.chart for shape in shapes if shape.has_chart)
    assert native.category_axis.tick_labels.font.size.pt >= minimum
    assert native.value_axis.tick_labels.font.size.pt >= minimum


@pytest.mark.parametrize("kind,categories", [
    ("column", [f"Region {i:02}" for i in range(1, 17)]),
    ("bar", ["Enterprise subscriptions", "Consumer subscriptions", "Government contracts"]),
])
def test_larger_axis_labels_adapt_without_losing_category_names(kind, categories):
    deck, slide, chart = workbook(kind, categories=categories, count=3)
    assert not validate_slide(deck, slide, set())
    layout = chart.composition
    assert layout.axis_size == 12
    if kind == "column":
        assert layout.numbered and layout.axis_labels == [str(i) for i in range(1, 17)]
    else:
        assert layout.axis_labels == categories
    page = PdfReader(BytesIO(to_pdf(deck, {}.get))).pages[0]
    text = page.extract_text()
    assert all(category in text for category in categories)
    if layout.numbered:
        positions = {}

        def collect(text, current_matrix, text_matrix, face, size):
            label = text.strip()
            if label in layout.axis_labels and size == layout.axis_size:
                positions[label] = (text_matrix[4], text_matrix[4] + pdfmetrics.stringWidth(label, pdf_face(chart.font, False), size))

        page.extract_text(visitor_text=collect)
        assert len(positions) == len(categories)
        assert all(positions[b][0] - positions[a][1] >= 4 for a, b in zip(layout.axis_labels, layout.axis_labels[1:]))


def test_changed_axis_floor_invalidates_cached_layout():
    _, _, chart = workbook()
    layout, _ = measure_chart(chart, 864, 432)
    layout.axis_size = 10
    layout.signature = "old-layout-before-the-label-floor"
    assert chart_layout(chart, 864, 432).axis_size == 12


def test_dark_neutral_difference_is_not_enough_to_identify_a_series():
    assert not series_distinguishable("#000000", "#101010")
    assert series_distinguishable("#000000", "#414141")
    assert series_distinguishable("#B5893B", "#707070")
