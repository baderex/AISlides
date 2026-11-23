"""Compose from persisted semantic content and compare eligible layouts."""

import io
import re
from hashlib import sha256

from PIL import Image as PILImage, ImageDraw
from pydantic import Field, ValidationError

from app.composition import OutlineItem, SlideContent, compose
from app.consulting import consulting_rules
from app.design import readable
from app.domain import Chart, Contract, Image, Line, Shape, SlideSpec, Table, Text, TextSpan
from app.exhibit_evidence import STATUS_KEY, evidence_label, exhibit_bindings, with_badge
from app.rendering import fit_image, preview, validate_slide
from app.storage import Storage


FEATURE_BRIEF = re.compile(r"\b(features?|capabilities|specifications|product details|product design)\b", re.I)
NON_FEATURE_BRIEF = re.compile(r"\b(steps?|sequence|workflow|risks?|phases?|use cases?)\b", re.I)


def recipe_options(part, content, document):
    if content.visual:
        visual = content.visual
        if visual.type == "decision-matrix" and not visual.recommended:
            return ["standard"]
        if visual.type == "roadmap" and not any(item.kind in {"milestone", "gate"} for item in visual.items):
            return ["standard"]
        alternate = {
            "ranked-bar": "exhibit_callouts", "waterfall": "exhibit_callouts",
            "decision-matrix": "exhibit_callouts", "sankey": "exhibit_callouts",
            "architecture": "exhibit_horizontal", "roadmap": "exhibit_milestones",
        }.get(visual.type)
        return ["standard", alternate] if alternate else ["standard"]
    theme = document.theme
    rules = theme.design or (consulting_rules(theme) if theme.profile == "consulting" else None)
    if not rules:
        return ["standard"]
    if part.layout == "cover":
        if content.image_asset_id:
            return ["photo_cover"]
        if content.cover_callout:
            return ["decision_cover"]
        return ["editorial_cover", "standard"] if rules.cover_style == "rules" else ["editorial_cover"]
    if content.decision and part.layout == "closing":
        return ["decision"]
    if part.layout == "closing":
        if re.search(r"\b(luxur\w*|premium|dramatic|expressive)\b", rules.mood, re.I):
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
        if document.aspect_ratio == "1:1" or rules.spacing == "compact":
            return ["feature_rows", "feature_panels"]
        return ["feature_panels", "feature_rows"]
    return ["standard"]


GEOMETRY_FIELDS = {"x", "y", "w", "h", "rotation", "z", "start_anchor", "end_anchor", "route", "direction"}


def generated_state(element):
    return element.model_dump(mode="json", exclude={"id", "element_key", "edited", "type", "placeholder_idx"})


def snapshot_generated(slide):
    if slide.spec and slide.spec.identity_version == 2:
        slide.spec.generated_elements = {element.id: generated_state(element) for element in slide.elements}


def detach_missing_anchors(elements):
    shape_ids = {element.id for element in elements if isinstance(element, Shape)}
    for element in elements:
        if isinstance(element, Line):
            if element.start_anchor and element.start_anchor.shape_id not in shape_ids:
                element.start_anchor = None
            if element.end_anchor and element.end_anchor.shape_id not in shape_ids:
                element.end_anchor = None


def stable_ids(slide, namespace, recipe):
    seen = set()
    ids = {}
    for element in slide.elements:
        # A layout-local element has no safe match in another recipe. Qualify
        # the slot by geometry instead of reusing an ordinal for its shape type.
        slot = tuple(round(getattr(element, field), 6) for field in ("x", "y", "w", "h"))
        role = element.element_key
        if role is None:
            local = element.text if isinstance(element, Text) else element.asset_id if isinstance(element, Image) else slot
            role = ("layout", recipe, slide.layout, local, getattr(element, "shape", None))
        identity = (role, element.type)
        if identity in seen:
            identity = (role, element.type, recipe, slot)
        if identity in seen:
            identity = (role, element.type, element.id)
        seen.add(identity)
        digest = sha256(repr((namespace, identity)).encode()).hexdigest()[:28]
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
    if content.visual and not content.annotations and part.annotations:
        content = content.model_copy(update={"annotations": part.annotations})
    slide = compose(part, content, document, index, images, brand, data_driven=data_driven,
                    image_ratio=image_ratio, web_image_ratio=web_image_ratio, recipe=selected)
    slide.warnings.extend(content.evidence_warnings)
    if slide_id:
        slide.id = slide_id
    chart = next((element for element in slide.elements if isinstance(element, Chart)), None)
    bindings = exhibit_bindings(content, document.sources, slide.source_ids, chart)
    if chart and bindings:
        slide = with_badge(document, slide, evidence_label(bindings))
    if not brand and (kit := document.theme.brand_kit) and kit.logo_asset_id and len(slide.elements) < 100:
        logo = Image(x=.939, y=.942, w=.049, h=.044, asset_id=kit.logo_asset_id,
                     alt="Brand logo", element_key="brand:logo")
        try:
            with PILImage.open(io.BytesIO(Storage().get(kit.logo_asset_id))) as original:
                logo = fit_image(logo, document, original.width / original.height)
            slide.elements.append(logo)
        except (FileNotFoundError, OSError):
            slide.warnings.append("The brand logo could not be loaded.")
    heading = next((e for e in slide.elements if isinstance(e, Text) and e.text == slide.title), None)
    if heading:
        heading.element_key = "slide:headline"
    subtitle = next((e for e in slide.elements if isinstance(e, Text) and e is not heading and
                     e.element_key is None and e.text == content.subtitle), None)
    if subtitle:
        subtitle.element_key = "slide:subtitle"
    if not brand and heading and part.key_message and document.theme.design:
        accent = readable(document.theme.accent, slide.background, heading.color)
        matches = re.finditer(r"(?:[−-]?[$€£¥]\d[\d,.]*[kmb]?|\b\d+(?:\.\d+)?%)", heading.text, re.I)
        spans = list(heading.spans)
        for match in matches:
            if all(match.end() <= span.start or match.start() >= span.end for span in spans):
                spans.append(TextSpan(start=match.start(), end=match.end(), color=accent, bold=True))
        heading.spans = sorted(spans, key=lambda span: span.start)
    id_namespace = id_namespace or slide.id
    stable_ids(slide, id_namespace, selected)
    slide.spec = SlideSpec(plan=part.model_dump(mode="json"), content=content.model_dump(mode="json"),
                           recipe=selected, visual_priority=part.visual_priority or part.key_message,
                           image_ratio=image_ratio, web_image_ratio=web_image_ratio, data_driven=data_driven,
                           id_namespace=id_namespace, generated_ids=[e.id for e in slide.elements], generated_background=slide.background,
                           generated_notes=slide.notes, identity_version=2, available_recipes=options,
                           value_bindings=[binding.model_dump(mode="json") for binding in bindings])
    snapshot_generated(slide)
    return slide


class LayoutRating(Contract):
    candidate: int = Field(ge=1, le=3)
    hierarchy: int = Field(ge=0, le=5)
    emphasis: int = Field(ge=0, le=5)
    balance: int = Field(ge=0, le=5)
    imagery: int = Field(ge=0, le=5)
    style_match: int = Field(ge=0, le=5)

    @property
    def score(self):
        return 2 * self.hierarchy + 2 * self.emphasis + self.balance + self.imagery + self.style_match


class LayoutReview(Contract):
    ratings: list[LayoutRating] = Field(min_length=2, max_length=3)


LAYOUT_RUBRIC = (
    "Compare the numbered candidate renders as a presentation art director. Rate each candidate from 0 to 5 on "
    "hierarchy (clear reading order and focal point), emphasis (makes the stated visual priority easy to find), "
    "balance (comfortable density, purposeful whitespace, and alignment), imagery (the image adds value and the "
    "crop preserves its subject, or the decision to omit imagery suits the task), and style_match (typography, "
    "color, and composition match the design brief). Use 0 for weak, 3 for adequate, and 5 for strong. "
    "Provide one rating per candidate using the displayed numbers. Technical fit and data accuracy are checked separately."
)


def technical_score(candidate, content):
    failure = sum(1 for warning in candidate.warnings if warning.startswith(
        ("Overlapping", "Missing", "Low", "Text may", "Some exhibit", "The chart data")))
    incomplete = bool(content.diagram or content.swimlane) and not any(isinstance(el, Line) for el in candidate.elements)
    return int(incomplete), failure


def design_score(slide, part, theme):
    texts = [element for element in slide.elements if isinstance(element, Text)]
    heading = next((text for text in texts if text.text == slide.title), None)
    body = [text for text in texts if text is not heading]
    prominent = max((text.size for text in body), default=16)
    hierarchy = min(5, 3 * heading.size / max(18, prominent)) if heading else 0
    terms = set(re.findall(r"\b\w{4,}\b", (part.visual_priority or part.key_message).casefold()))
    relevant = [text for text in body if terms.intersection(re.findall(r"\b\w{4,}\b", text.text.casefold()))]
    exhibit = any(isinstance(el, (Chart, Table, Line)) for el in slide.elements)
    emphasis = min(5, (max((el.size for el in relevant), default=0) / 6) + (3 if exhibit else 1))
    small = sum(max(0, 14 - el.size) / 4 for el in body if len(el.text) >= 10)
    balance = max(0, 5 - small - max(0, len(body) - 10) * .2)
    image_area = sum(el.w * el.h for el in slide.elements if isinstance(el, Image) and el.w >= .14)
    imagery = 3 if not image_area else min(5, 2 + image_area * 6)
    rows_fit = slide.spec.recipe == "feature_rows" and theme.design and theme.design.spacing == "compact"
    style_match = 4 if rows_fit else 3
    return 2 * hierarchy + 2 * emphasis + balance + imagery + style_match


def comparison_image(finalists):
    panels = []
    for _, raw in finalists:
        with PILImage.open(io.BytesIO(raw)) as image:
            image.thumbnail((600, 375))
            panels.append(image.convert("RGB"))
    width, height = max(panel.width for panel in panels), max(panel.height for panel in panels)
    canvas = PILImage.new("RGB", (width * len(panels), height + 30), "#FFFFFF")
    title = ImageDraw.Draw(canvas)
    for index, image in enumerate(panels):
        title.text((index * width + 10, 9), f"Candidate {index + 1}", fill="#222222")
        canvas.paste(image, (index * width, 30))
    buffer = io.BytesIO()
    canvas.save(buffer, "PNG")
    return buffer.getvalue()


def review_layouts(document, slide, index, images, get_asset, *, artwork=None, evaluate=None):
    """Gate candidate layouts on technical fit, then compare their visual quality."""
    if document.theme.brand_kit and document.theme.brand_kit.logo_asset_id:
        images = images | {document.theme.brand_kit.logo_asset_id}
    slide.warnings.extend(validate_slide(document, slide, images))
    snapshot_generated(slide)
    png = preview(document, slide, get_asset, artwork=artwork)
    spec = slide.spec
    if not spec or slide.brand_layout_index is not None:
        return slide, png
    part = OutlineItem.model_validate(spec.plan)
    content = SlideContent.model_validate(spec.content)
    candidates = [(slide, png)]
    for recipe in [option for option in recipe_options(part, content, document) if option != spec.recipe][:2]:
        try:
            candidate = build_slide(part, content, document, index, images, recipe=recipe,
                                    data_driven=spec.data_driven, image_ratio=spec.image_ratio,
                                    web_image_ratio=spec.web_image_ratio, slide_id=slide.id, id_namespace=spec.id_namespace)
            candidate.warnings.extend(validate_slide(document, candidate, images))
            snapshot_generated(candidate)
            candidates.append((candidate, preview(document, candidate, get_asset, artwork=artwork)))
        except ValidationError:
            continue
    technical = min(technical_score(candidate, content) for candidate, _ in candidates)
    finalists = [item for item in candidates if technical_score(item[0], content) == technical]
    if evaluate and len(finalists) > 1:
        ratings = evaluate(part, [candidate.spec.recipe for candidate, _ in finalists], comparison_image(finalists))
        if ratings and sorted(rating.candidate for rating in ratings.ratings) == list(range(1, len(finalists) + 1)):
            winner = max(ratings.ratings, key=lambda rating: (rating.score, -rating.candidate))
            return finalists[winner.candidate - 1]
    return max(finalists, key=lambda item: design_score(item[0], part, document.theme))


def recompose_slide(slide, document, index, *, keep_recipe, replacement_content=None, selected_recipe=None, brand=None):
    spec = slide.spec
    if not spec or (slide.brand_layout_index is not None and brand is None):
        return None
    if spec.html_region:
        if replacement_content is None:
            return None  # Restyling uses the existing element-level CSS layout.
        from app.html_layout.exhibits import rebuild_region
        return rebuild_region(document, slide, replacement_content, selected_recipe or spec.recipe)
    try:
        part = OutlineItem.model_validate(spec.plan)
        content = replacement_content or SlideContent.model_validate(spec.content)
        content.source_ids = slide.source_ids[:12]
        generated = set(spec.generated_ids)
        old = {e.id: e for e in slide.elements}
        removed = set(spec.deleted_ids) | (generated - old.keys())
        if spec.identity_version < 2 and (removed or any(el.edited and el.id in generated for el in slide.elements)):
            return None
        images = {e.asset_id for e in slide.elements if isinstance(e, Image)}
        if content.image_asset_id:
            images.add(content.image_asset_id)
        recipe = selected_recipe or (spec.recipe if keep_recipe else recipe_options(part, content, document)[0])
        deck_so_far = document.model_copy(update={"slides": document.slides[:index]})
        result = build_slide(part, content, deck_so_far, index, images, brand, recipe=recipe,
                             data_driven=spec.data_driven, image_ratio=spec.image_ratio,
                             web_image_ratio=spec.web_image_ratio, slide_id=slide.id, id_namespace=spec.id_namespace)
        if len(removed) > 1000:
            return None
        result.spec.deleted_ids = sorted(removed)
        layout_changed = result.spec.recipe != spec.recipe or result.layout != slide.layout
        result.warnings.extend(validate_slide(document, result, images))
        snapshot_generated(result)
        new_elements = []
        for element in result.elements:
            if element.element_key == STATUS_KEY:
                new_elements.append(element)
                continue
            if element.id in removed:
                continue
            previous = old.get(element.id)
            baseline = spec.generated_elements.get(element.id)
            if previous and previous.edited and baseline is not None:
                element = preserve_edits(element, previous, baseline, layout_changed, replacement_content is not None)
            new_elements.append(element)
        new_elements.extend(element.model_copy(deep=True) for element in slide.elements
                            if element.id not in generated and element.element_key != STATUS_KEY)
        result.elements = new_elements
        if len(result.elements) > 100:
            return None
        detach_missing_anchors(result.elements)
        if slide.background != spec.generated_background:
            result.background = slide.background
        if slide.notes != spec.generated_notes:
            result.notes = slide.notes
        if slide.title != part.title:
            result.title = slide.title
        result.source_ids = slide.source_ids
        checked = result.model_copy(deep=True)
        result.warnings = list(dict.fromkeys([*result.warnings, *validate_slide(document, checked, images)]))[:30]
        result.elements = [before if before.edited or before.id not in generated and before.id in old else after
                           for before, after in zip(result.elements, checked.elements)]
        return result
    except ValidationError:
        return None


def preserve_edits(element, previous, baseline, layout_changed, data_changed, *, measured_exhibit=False):
    changes = {field: value for field, value in generated_state(previous).items()
               if value != baseline.get(field) and (not layout_changed or field not in GEOMETRY_FIELDS)}
    if data_changed:
        new = generated_state(element)
        data_fields = {"text", "spans", "rows"}
        if measured_exhibit:
            data_fields.update(GEOMETRY_FIELDS - {"z", "rotation"})
        edited_rows = changes.get("rows")
        changes = {field: value for field, value in changes.items()
                   if field not in data_fields or new.get(field) == baseline.get(field)}
        if measured_exhibit and isinstance(element, Table) and edited_rows and "rows" not in changes:
            old_rows, new_rows = baseline.get("rows"), new["rows"]
            if (isinstance(old_rows, list) and len(old_rows) == len(new_rows) == len(edited_rows)
                    and all(isinstance(old, list) and len(old) == len(row) == len(edited)
                            for old, row, edited in zip(old_rows, new_rows, edited_rows))):
                merged = [row[:] for row in new_rows]
                for i, row in enumerate(new_rows):
                    for j, value in enumerate(row):
                        if (row[0] == old_rows[i][0] and new_rows[0][j] == old_rows[0][j]
                                and value == old_rows[i][j]):
                            merged[i][j] = edited_rows[i][j]
                if merged != new_rows:
                    changes["rows"] = merged
    if not changes:
        return element
    candidate = {**element.model_dump(mode="json"), **changes, "edited": True}
    try:
        return type(element).model_validate(candidate)
    except ValidationError:
        candidate = {**element.model_dump(mode="json"),
                     **{key: value for key, value in changes.items() if key not in GEOMETRY_FIELDS}, "edited": True}
        return type(element).model_validate(candidate)
