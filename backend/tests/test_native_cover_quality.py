from io import BytesIO

import pytest
from pptx import Presentation

from app.composition import OutlineItem, SlideContent
from app.deck_design import baseline_rules
from app.domain import Document, Shape, Text, Theme
from app.powerpoint import to_pptx
from app.render_geometry import contrast, physical, validate_slide
from app.slide_design import build_slide, review_layouts


def cover(style, aspect="16:9", *, title="A clear operating plan"):
    theme = Theme(background="#FFFFFF", ink="#191919", muted="#595959",
                  accent="#B5893B", font="Manrope")
    theme.design = baseline_rules(theme, "editorial").model_copy(
        update={"cover_style": style, "cover_background": "#FFFFFF"})
    document = Document(title=title, aspect_ratio=aspect, theme=theme)
    plan = OutlineItem(title=document.title, layout="cover", lock_title=True)
    content = SlideContent(title=plan.title, subtitle="Priorities, ownership, and the next decision.")
    first = build_slide(plan, content, document, 0, set())
    slide, _ = review_layouts(document, first, 0, set(), {}.get)
    document.slides = [slide]
    return document, slide


def test_native_cover_rules_keep_graphic_accent_in_editable_export():
    document, slide = cover("rules")
    rules = [element for element in slide.elements if isinstance(element, Shape)]
    assert rules and all(element.fill == "#B5893B" for element in rules)
    assert not validate_slide(document, slide, set())
    assert all(contrast(element.color, slide.background) >= 4.5
               for element in slide.elements if isinstance(element, Text))
    exported = Presentation(BytesIO(to_pptx(document, {}.get))).slides[0]
    fills = [str(shape.fill.fore_color.rgb) for shape in exported.shapes if not shape.has_text_frame or not shape.text]
    assert fills and set(fills) == {"B5893B"}


@pytest.mark.parametrize("aspect", ["16:9", "4:3", "1:1"])
def test_native_panel_and_circles_are_distinct_and_preserve_readable_copy(aspect):
    panel_document, panel = cover("panel", aspect)
    circle_document, circles = cover("circles", aspect)
    panel_art = [element for element in panel.elements if isinstance(element, Shape)]
    circle_art = [element for element in circles.elements if isinstance(element, Shape)]
    assert panel_art and all(element.shape == "rect" for element in panel_art)
    assert circle_art and all(element.shape == "ellipse" for element in circle_art)
    for document, slide in ((panel_document, panel), (circle_document, circles)):
        assert not validate_slide(document, slide, set())
        copy = [element for element in slide.elements if isinstance(element, Text)]
        assert {element.text for element in copy} == {
            document.title, "Priorities, ownership, and the next decision."}
        assert all(element.size >= element.minimum_pt for element in copy)
        art_left = min(element.x for element in slide.elements if isinstance(element, Shape))
        assert all(element.x + element.w < art_left for element in copy)
    width, height = physical(circle_document)
    assert all(element.w * width == pytest.approx(element.h * height) for element in circle_art)


@pytest.mark.parametrize("style", ["panel", "circles"])
def test_long_locked_cover_title_keeps_its_width_and_readable_floor(style):
    title = ("A Practical Guide to Building Reliable AI Agents for Enterprise Teams "
             "in Highly Regulated Financial Services")
    document, slide = cover(style, "1:1", title=title)
    assert not validate_slide(document, slide, set())
    heading, = [element for element in slide.elements if isinstance(element, Text) and element.role == "cover"]
    art = [element for element in slide.elements if isinstance(element, Shape)]
    assert heading.text == title and heading.size >= heading.minimum_pt == 36
    assert heading.w == pytest.approx(.86)
    assert art and all(element.y + element.h < heading.y for element in art)
    exported = Presentation(BytesIO(to_pptx(document, {}.get))).slides[0]
    assert any(shape.has_text_frame and shape.text == title for shape in exported.shapes)

