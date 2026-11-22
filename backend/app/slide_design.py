"""Compose from persisted semantic content and compare eligible layouts."""

import re
from collections import Counter
from hashlib import sha256

from pydantic import ValidationError

from app.composition import OutlineItem, SlideContent, compose
from app.design import readable
from app.domain import Image, Line, Shape, SlideSpec, Text, TextSpan
from app.rendering import preview, validate_slide


FEATURE_BRIEF = re.compile(r"\b(features?|capabilities|specifications|product details|product design)\b", re.I)
NON_FEATURE_BRIEF = re.compile(r"\b(steps?|sequence|workflow|risks?|phases?|use cases?)\b", re.I)


def recipe_options(part, content, document):
    theme = document.theme
    if not theme.design or theme.profile != "standard":
        return ["standard"]
    if part.layout == "cover":
        if content.image_asset_id:
            return ["photo_cover"]
        if content.cover_callout:
            return ["decision_cover"]
        return ["editorial_cover", "standard"] if theme.design.cover_style == "rules" else ["editorial_cover"]
    if content.decision and part.layout == "closing":
        return ["decision"]
    if part.layout == "closing":
        if re.search(r"\b(luxur\w*|premium|dramatic|expressive)\b", theme.design.mood, re.I):
            return ["closing_centered", "closing_display"]
        return ["closing_display", "closing_centered"]
    if content.swimlane and part.layout in {"summary", "process", "comparison", "showcase", "editorial"}:
        return ["diagram_swimlane"]
    if content.diagram:
        lanes = content.diagram.lanes
        if len(lanes) == 1 and lanes[0].kind == "flow" and len(lanes[0].nodes) > 2:
            if document.aspect_ratio == "1:1" and len(lanes[0].nodes) == 4:
                options = ["diagram_vertical", "diagram_flow"]
            else:
                options = ["diagram_flow", "diagram_vertical"]
        else:
            options = ["diagram_paths" if len(lanes) > 1 or lanes[0].kind == "branch" else "diagram_flow"]
        return [*options, "standard"] if content.points else options
    if content.allocation:
        return ["allocation"]
    if content.rollout:
        return ["rollout"]
    if content.readout:
        return ["readout"]
    if content.chart_ready or content.stacked_comparison:
        return ["chart"]
    if content.table_ready:
        return ["dense_table", "dense_table_full"] if len(content.table) >= 6 else ["dense_table"]
    topic = " ".join((part.topic, part.title, part.purpose))
    if (
        part.layout in {"summary", "editorial", "showcase"} and 2 <= len(content.points) <= 3
        and not content.image_asset_id and all(not point.value for point in content.points)
        and not NON_FEATURE_BRIEF.search(topic)
        and (part.recipe in {"feature_panels", "feature_rows"} or FEATURE_BRIEF.search(topic))
    ):
        if document.aspect_ratio == "1:1" or theme.design.spacing == "compact":
            return ["feature_rows", "feature_panels"]
        return ["feature_panels", "feature_rows"]
    return ["standard"]


def stable_ids(slide, namespace):
    counts = Counter()
    ids = {}
    for element in slide.elements:
        if isinstance(element, Text):
            identity = ("text", element.text)
        elif isinstance(element, Image):
            identity = ("image", element.asset_id)
        else:
            identity = (element.type,)
        counts[identity] += 1
        digest = sha256(repr((namespace, identity, counts[identity])).encode()).hexdigest()[:28]
        ids[element.id] = element.id = f"e-{digest}"
    for element in slide.elements:
        if isinstance(element, Line):
            for anchor in (element.start_anchor, element.end_anchor):
                if anchor and anchor.shape_id in ids:
                    anchor.shape_id = ids[anchor.shape_id]


def build_slide(part, content, document, index, images, brand=None, *, image_ratio=None, web_image_ratio=None,
                data_driven=False, recipe=None, slide_id=None, id_namespace=None):
    options = recipe_options(part, content, document) if not brand else ["standard"]
    preferred = recipe or part.recipe
    selected = preferred if preferred in options else options[0]
    slide = compose(part, content, document, index, images, brand, data_driven=data_driven,
                    image_ratio=image_ratio, web_image_ratio=web_image_ratio, recipe=selected)
    if slide_id:
        slide.id = slide_id
    if not brand and document.theme.profile == "standard":
        if part.key_message and document.theme.design:
            heading = next((e for e in slide.elements if isinstance(e, Text) and e.text == slide.title), None)
            if heading:
                accent = readable(document.theme.accent, slide.background, heading.color)
                matches = re.finditer(r"(?:[−-]?[$€£¥]\d[\d,.]*[kmb]?|\b\d+(?:\.\d+)?%)", heading.text, re.I)
                heading.spans = [TextSpan(start=m.start(), end=m.end(), color=accent, bold=True) for m in matches]
        id_namespace = id_namespace or slide.id
        stable_ids(slide, id_namespace)
        slide.spec = SlideSpec(plan=part.model_dump(mode="json"), content=content.model_dump(mode="json"),
                               recipe=selected, visual_priority=part.visual_priority or part.key_message,
                               image_ratio=image_ratio, web_image_ratio=web_image_ratio, data_driven=data_driven,
                               id_namespace=id_namespace, generated_ids=[e.id for e in slide.elements], generated_background=slide.background,
                               generated_notes=slide.notes)
    return slide


def review_layouts(document, slide, index, images, get_asset):
    """Render the planned layout and at most one eligible alternative."""
    original = slide
    slide.warnings.extend(validate_slide(document, slide, images))
    png = preview(document, slide, get_asset)
    spec = slide.spec
    if not spec:
        return slide, png
    part = OutlineItem.model_validate(spec.plan)
    content = SlideContent.model_validate(spec.content)
    options = recipe_options(part, content, document)
    alternatives = [option for option in options if option != spec.recipe]
    if not alternatives:
        return slide, png

    def score(candidate):
        warning_cost = sum(
            6 if warning.startswith(("Overlapping", "Missing", "Low", "Text may", "Some exhibit")) else 1
            for warning in candidate.warnings
        )
        type_cost = 30 if content.diagram and not any(isinstance(el, Line) for el in candidate.elements) else 0
        small_text = sum(max(0, 13 - el.size) / 3 for el in candidate.elements
                         if isinstance(el, Text) and len(el.text) >= 10)
        return warning_cost + type_cost + small_text

    try:
        candidate = build_slide(part, content, document, index, images, recipe=alternatives[0],
                                data_driven=spec.data_driven, image_ratio=spec.image_ratio,
                                web_image_ratio=spec.web_image_ratio, slide_id=slide.id)
        candidate.warnings.extend(validate_slide(document, candidate, images))
        candidate_png = preview(document, candidate, get_asset)
    except ValidationError:
        return slide, png
    # Preserve deliberate composition choices unless the alternative clearly improves fit.
    if score(candidate) + 2 < score(original):
        return candidate, candidate_png
    return slide, png


def recompose_slide(slide, document, index, *, keep_recipe):
    spec = slide.spec
    if not spec:
        return None
    try:
        part = OutlineItem.model_validate(spec.plan)
        content = SlideContent.model_validate(spec.content)
        generated = set(spec.generated_ids)
        old = {e.id: e for e in slide.elements}
        removed = set(spec.deleted_ids) | (generated - old.keys())
        images = {e.asset_id for e in slide.elements if isinstance(e, Image)}
        if content.image_asset_id:
            images.add(content.image_asset_id)
        recipe = spec.recipe if keep_recipe else recipe_options(part, content, document)[0]
        deck_so_far = document.model_copy(update={"slides": document.slides[:index]})
        result = build_slide(part, content, deck_so_far, index, images, recipe=recipe,
                             data_driven=spec.data_driven, image_ratio=spec.image_ratio,
                             web_image_ratio=spec.web_image_ratio, slide_id=slide.id, id_namespace=spec.id_namespace)
        if result.spec is None:
            return None
        if len(removed) > 1000:
            return None
        result.spec.deleted_ids = sorted(removed)
        overrides = {e.id: e.model_copy(deep=True) for e in slide.elements if e.edited or e.id not in generated}
        result.elements = [overrides.pop(e.id, e) for e in result.elements if e.id not in removed]
        result.elements.extend(overrides.values())
        if len(result.elements) > 100:
            return None
        remaining = {e.id for e in result.elements if isinstance(e, Shape)}
        for e in result.elements:
            if isinstance(e, Line):
                if e.start_anchor and e.start_anchor.shape_id not in remaining:
                    e.start_anchor = None
                if e.end_anchor and e.end_anchor.shape_id not in remaining:
                    e.end_anchor = None
        if slide.background != spec.generated_background:
            result.background = slide.background
        if slide.notes != spec.generated_notes:
            result.notes = slide.notes
        if slide.title != part.title:
            result.title = slide.title
        result.source_ids = slide.source_ids
        checked = result.model_copy(deep=True)
        result.warnings = validate_slide(document, checked, images)[:30]
        result.elements = [before if before.edited or before.id not in generated and before.id in old else after
                           for before, after in zip(result.elements, checked.elements)]
        return result
    except ValidationError:
        return None
