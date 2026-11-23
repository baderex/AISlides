"""Place structured exhibits inside CSS frames, retaining their data for the editor."""
from app.composition import OutlineItem, SlideContent
from app.design import style_chart
from app.domain import Chart, Slide, SlideSpec
from app.exhibit_evidence import STATUS_KEY, evidence_label, exhibit_bindings, with_badge
from app.exhibit_layout import compose_exhibit
from app.html_layout.review import readability_warnings
from app.slide_design import detach_missing_anchors, generated_state, preserve_edits, recipe_options, snapshot_generated, stable_ids
from app.render_geometry import fit_slide_text


def fill_region(document, slide, region, content, recipe, namespace, workbook=None):
    layer = Slide(title=slide.title, layout=slide.layout, background=region.background or slide.background, source_ids=slide.source_ids)
    bounds = region.model_dump(include={"x", "y", "w", "h"})
    if workbook:
        chart = Chart(**bounds, **workbook.model_dump(), element_key="workbook:chart",
                      color=document.theme.accent, label_color=document.theme.ink, font=document.theme.font)
        style_chart(chart, document.theme, layer.background)
        layer.elements.append(chart)
    else:
        compose_exhibit(document, layer, content, tuple(bounds[k] for k in ("x", "y", "w", "h")),
                        recipe=recipe, score_bars=True)
    layer.warnings.extend(fit_slide_text(document, layer))
    stable_ids(layer, namespace, recipe)
    # Reserve space in DOM paint order for internal fills, connectors and labels.
    for element in layer.elements:
        element.z = region.z * 10 + min(9, max(0, element.z + 5))
    return layer


def attach_region(document, slide, region, page, plan, observations):
    workbook = page.exhibit_data if plan.exhibit == "workbook-chart" else None
    visual = None if workbook else page.exhibit_data
    if visual and visual.type != plan.exhibit:
        raise ValueError("The exhibit type must match the planned exhibit.")
    content = SlideContent(title=slide.title, visual=visual, source_ids=slide.source_ids[:12],
                           illustrative_exhibit=page.illustrative, exhibit_observations=observations)
    part = OutlineItem(title=slide.title, layout=plan.layout)
    layer = fill_region(document, slide, region, content, "standard", slide.id, workbook)
    region.member_ids = [e.id for e in layer.elements]
    for element in slide.elements:
        element.z *= 10
    slide.elements.extend(layer.elements)
    slide.warnings.extend(layer.warnings)
    chart = layer.elements[0] if workbook else None
    bindings = exhibit_bindings(content, document.sources, slide.source_ids, chart)
    if bindings:
        slide = with_badge(document, slide, evidence_label(bindings))
    slide.spec = SlideSpec(plan=part.model_dump(mode="json"), content=content.model_dump(mode="json"),
                           html_region=region, identity_version=2, id_namespace=slide.id,
                           generated_background=slide.background, generated_notes=slide.notes,
                           available_recipes=recipe_options(part, content, document) if visual else ["standard"],
                           value_bindings=[b.model_dump(mode="json") for b in bindings],
                           generated_ids=[e.id for e in slide.elements])
    snapshot_generated(slide)
    return slide


def rebuild_region(document, draft, content, recipe):
    slide = draft.model_copy(deep=True)
    spec = slide.spec
    region = spec.html_region
    prior = {e.id: e for e in slide.elements}
    former = set(region.member_ids)
    removed = set(spec.deleted_ids) | (former - prior.keys())
    try:
        layer = fill_region(document, slide, region, content, recipe, spec.id_namespace or slide.id)
        region.member_ids = [e.id for e in layer.elements]
        rebuilt = []
        baseline = {k: v for k, v in spec.generated_elements.items() if k not in former}
        for element in layer.elements:
            baseline[element.id] = generated_state(element)
            if element.id in removed:
                continue
            old = prior.get(element.id)
            if old and old.edited and element.id in spec.generated_elements:
                element = preserve_edits(element, old, spec.generated_elements[element.id], recipe != spec.recipe,
                                         True, measured_exhibit=True)
            rebuilt.append(element)
        slide.elements = [e for e in slide.elements if e.id not in former] + rebuilt
        bindings = exhibit_bindings(content, document.sources, slide.source_ids)
        if bindings:
            slide = with_badge(document, slide, evidence_label(bindings))
        else:
            slide.elements = [e for e in slide.elements if e.element_key != STATUS_KEY]
        spec.content = content.model_dump(mode="json")
        spec.value_bindings = [b.model_dump(mode="json") for b in bindings]
        spec.deleted_ids = sorted(removed)
        spec.generated_ids = [k for k in spec.generated_ids if k not in former] + region.member_ids
        spec.generated_elements = baseline
        spec.recipe = recipe
        spec.available_recipes = recipe_options(OutlineItem.model_validate(spec.plan), content, document)
        detach_missing_anchors(slide.elements)
        slide.warnings = list(dict.fromkeys([*layer.warnings, *readability_warnings(document, slide)]))[:30]
        return Slide.model_validate(slide.model_dump())
    except ValueError:
        return None
