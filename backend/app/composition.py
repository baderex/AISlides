import re
from app.composition_models import (
    Intent, PlanningResponse, OutlineItem, Outline, Point,
    Readout, ChartContent, StackedComparison, AllocationLine, Allocation,
    Phase, RiskMitigation, Rollout, DecisionGate, Decision,
    CoverCallout, SlideContent, ResearchQueries, EvidenceSummary, ResearchBrief,
    Review, EXHIBIT_LAYOUTS, Exhibit, VISUAL_DIRECTION_LIMIT,
)
from app.branding import choose_layout, compose_branded
from app.consulting import CONSULTING_ICON_LAYOUTS, consulting_rules, consulting_theme, requested_consulting
from app.design import blend, readable, style_chart, style_slide_tables
from app.deck_design import DeckDesignSpec
from app.palette import categorical_palette
from app.diagrams import draw_diagram, draw_swimlane
from app.domain import BrandTemplate, Chart, Document, Image, Layout, Recipe, Shape, Slide, Table, Text, Theme, display_value
from app.exhibit_layout import ExhibitCanvas, compose_exhibit
from app.exhibit_evidence import STATUS_KEY, UNVERIFIED, evidence_label, exhibit_bindings
from app.rendering import contrast, fit_image, longest_word_width, physical, slide_icon, text_height
from app.visuals import VISUAL_LAYOUTS


DISPLAY_LAYOUTS = frozenset({"cover", "section", "quote", "closing"})
IMAGE_LAYOUTS = frozenset({"showcase", "editorial", "research"})
STANDARD_ICON_LAYOUTS = frozenset({"summary", "comparison", "timeline", "process", "metrics", "showcase", "editorial"})


def planned_exhibit(item: OutlineItem, has_evidence: bool, *, prefer_diagrams: bool = False,
                    supplied_breakdown: bool = False) -> Exhibit | None:
    description = f"{item.title} {item.purpose} {' '.join(item.required_details)}".lower()
    if item.recipe == "diagram_swimlane" or (
        item.layout in {"summary", "process", "comparison", "showcase", "editorial"}
        and re.search(r"\bswim[ -]?lanes?\b", description)
    ):
        return "swimlane"
    if item.exhibit and item.exhibit not in {"diagram", "visual"}:
        approval = r"\b(approve|approval|authorize|authorization|endorse|endorsement|funding request|request for funding)\b"
        if item.exhibit == "cover_callout" and not re.search(approval, f"{item.title} {item.purpose}", re.I):
            return None
        return item.exhibit if item.exhibit != "readout" or has_evidence else None
    # The outline may describe the right exhibit without selecting its field.
    if item.layout == "chart" and has_evidence and re.search(r"\b(costs?|expenses?)\b", description) and (
        supplied_breakdown or (
            re.search(r"\bcompar\w*", description)
            and re.search(r"\b(components?|breakdown|broken down)\b", description)
        )
    ):
        return "stacked_comparison"
    if item.layout in {"summary", "metrics", "research", "chart", "comparison"} and has_evidence and (
        re.search(r"\b(budget|spend|funding)\b", description)
        and re.search(r"\b(allocat\w*|line items?|breakdown|itemiz\w*|divided)\b", description)
    ):
        return "allocation"
    if item.layout in {"timeline", "process", "summary", "showcase"} and re.search(r"\b(timeline|rollout|deployment)\b", description) and (
        re.search(r"\b(risks?|mitigat\w*)\b", description)
    ):
        return "rollout"
    if item.layout in {"closing", "summary", "comparison", "metrics"} and (
        re.search(r"\b(gates?|go/no-go|success criteria|acceptance criteria)\b", description)
        and re.search(r"\b(approval|approve|decision|pause|stop rule)\b", description)
    ):
        return "decision"
    if (item.exhibit == "diagram" or prefer_diagrams) and item.layout in {"summary", "process", "comparison", "showcase", "editorial"}:
        return "diagram"
    if has_evidence and item.layout in {"closing", "summary", "metrics"} and re.search(r"\b(readout|recovery|key results)\b", description):
        return "readout"
    return None


def supports_point_icons(layout: Layout, theme: Theme, brand: BrandTemplate | None) -> bool:
    layouts = CONSULTING_ICON_LAYOUTS if theme.profile == "consulting" else STANDARD_ICON_LAYOUTS
    return brand is None and layout in layouts


def accepts_web_image(item: OutlineItem, content: SlideContent, brand: BrandTemplate | None) -> bool:
    if item.layout not in IMAGE_LAYOUTS or content.chart or content.table or content.stacked_comparison or content.visual:
        return False
    if brand:
        return any(slot.kind == "picture" for slot in choose_layout(brand.layouts, item.layout).slots)
    return True


def is_data_driven(direction: str) -> bool:
    return bool(re.search(r"\bdata[\s_-]+(?:driven|visuali[sz]ations?)\b", direction, re.IGNORECASE))


def design(direction: str, mode: str, selected: Theme | None) -> Theme:
    direction = direction.lower()
    if requested_consulting(direction) or any(term in direction for term in
        ("strategy consulting", "consulting-style", "consulting style", "consulting deck", "management consulting")):
        return selected.model_copy(update={"profile": "consulting"}) if selected else consulting_theme()
    if selected:
        return selected
    if "editorial" in direction:
        return Theme(background="#F7F2E9", ink="#27213D", accent="#9D3C30", muted="#625968", surface="#EEE2D2", font="Georgia")
    if "academic" in direction:
        return Theme(background="#F0F8F5", ink="#16352F", accent="#08745D", muted="#395E54", surface="#DAEDE6")
    if is_data_driven(direction):
        return Theme(background="#F3F6FA", ink="#122239", accent="#0F6B62", muted="#41566B", surface="#E3EDF0")
    if mode == "creative" or "bold" in direction:
        return Theme(background="#F1ECFF", ink="#281852", accent="#8737AC", muted="#57416B", surface="#E6D4F9")
    return Theme()


def select_layout(item, content, document, images, *, data_driven, allow_variants):
    role = item.layout
    if allow_variants:
        if content.swimlane and role in {"summary", "process", "comparison", "showcase", "editorial"}:
            return "process"
        if role == "section" and content.points:
            return "summary"
        if content.decision and role in {"summary", "closing"}:
            return "closing"
        if content.rollout and role in {"process", "timeline"}:
            return "timeline"
        if content.allocation and role in {"chart", "metrics", "research"}:
            return "metrics"
        if content.stacked_comparison and role == "chart":
            return "chart"
    if role in DISPLAY_LAYOUTS:
        return role
    sourced = bool(set(content.source_ids).intersection(source.id for source in document.sources))
    chart_ready = sourced and content.chart_ready and len(content.chart.values) >= 2
    if (data_driven and sourced) or allow_variants:
        if chart_ready and not (role == "research" and content.table_ready):
            return "chart"
        if content.table_ready and (sourced or not any(re.search(r"\d", cell) for row in content.table for cell in row)):
            return "research"
    if allow_variants and role == "summary":
        if content.image_asset_id in images:
            return "editorial"
        if sourced and len(content.points) >= 2 and all(point.value for point in content.points):
            return "metrics"
        if document.slides and document.slides[-1].layout == role and len(content.points) == 2:
            return "comparison"
    return role


def compose(item: OutlineItem, content: SlideContent, document: Document, index: int, image_ids: set[str],
            brand: BrandTemplate | None = None, *, web_image_ratio: float | None = None,
            image_ratio: float | None = None, data_driven: bool = False, recipe: Recipe = "standard") -> Slide:
    if not brand and document.theme.profile == "consulting" and not document.theme.design:
        theme = document.theme.model_copy(update={"design": consulting_rules(document.theme)})
        document = document.model_copy(update={"theme": theme})
    source_ids = {source.id for source in document.sources}
    rules = document.theme.design
    if content.visual:
        return compose_visual_slide(item, content, document, brand, image_ids, source_ids, recipe)
    consulting = document.theme.profile == "consulting"
    if consulting and not brand and not content.table and not content.chart and not content.diagram and not content.swimlane and not (
        content.stacked_comparison or content.image_asset_id or recipe in {"feature_panels", "feature_rows"}
    ) and item.layout in {"comparison", "research"} and content.points:
        content = content.model_copy(update={"table": [["Observation", "Implication"],
            *[[point.title, point.body] for point in content.points]]})
    layout = select_layout(item, content, document, image_ids, data_driven=data_driven,
                           allow_variants=bool(rules and not brand))
    if consulting and not brand and content.table_ready and layout == "comparison":
        layout = "research"
    item = item.model_copy(update={"layout": layout})
    if brand:
        slide = compose_branded(item, content, document, image_ids, brand)
        roles = DeckDesignSpec.from_theme(document.theme, document.aspect_ratio, layout).typography
        return style_slide_tables(slide, document.theme, preserve_palette=True, table_floor=roles["table"].minimum_pt)
    t = document.theme
    design_spec = DeckDesignSpec.from_theme(t, document.aspect_ratio, layout)
    typography = design_spec.typography
    compact = document.aspect_ratio == "1:1"
    factor = {"1:1": .68, "4:3": .83, "16:9": 1}[document.aspect_ratio]
    font_scale = max(.87, factor) if rules else factor
    ptw, pth = physical(document)
    editorial = t.font == "Georgia"
    dark = item.layout in DISPLAY_LAYOUTS and not editorial and not consulting
    bg = t.ink if dark else t.background
    ink, muted, accent = ("#FFFFFF", "#DDE1F5", "#E0DBFF") if dark else (t.ink, t.muted, t.accent)
    if rules:
        bg = rules.cover_background if item.layout in DISPLAY_LAYOUTS else t.background
        ink = readable(t.ink, bg, t.background)
        muted = readable(t.muted, bg, ink)
        accent = readable(t.accent, bg, ink)
    accent_fill = design_spec.palette["highlight"] if rules else accent
    slide = Slide(title=item.title, background=bg, layout=item.layout, notes=content.notes,
                  source_ids=list(dict.fromkeys(s for s in content.source_ids if s in source_ids)))
    elements = slide.elements

    def rect(x, y, w, h, fill, shape="rect", *, element_key=None):
        elements.append(Shape(x=x, y=y, w=w, h=h, fill=fill, shape=shape, z=-1, element_key=element_key))

    def label(value, x, y, w, h, size, color=ink, bold=False, heading=False, align="left", element_key=None, role="label"):
        if value.strip():
            face = rules.heading_font if rules and heading else t.font
            if value == item.title:
                role = "cover" if layout == "cover" else "headline"
            floor = typography[role].minimum_pt
            elements.append(Text(x=x, y=y, w=w, h=h, text=value, role=role, minimum_pt=floor, size=max(floor, size * font_scale),
                                 color=color, bold=bold, font=face, align=align, element_key=element_key))

    def fitted_label(value, x, y, w, max_h, preferred, minimum, color, *, bold=False, heading=False,
                     prefer_one_line=False, keep_words=False, align="left", element_key=None, role="label"):
        if not value.strip():
            return y
        face = rules.heading_font if rules and heading else t.font
        if value == item.title:
            role = "cover" if layout == "cover" else "headline"
        minimum = max(minimum, typography[role].minimum_pt)
        preferred = max(preferred, minimum)
        if prefer_one_line:
            preferred = next((size for size in range(preferred, max(minimum, round(.88 * preferred)) - 1, -1)
                              if text_height(value, w * ptw, size, bold, face) <= 1.35 * size), preferred)
        for size in range(preferred, minimum - 1, -1):
            height = (text_height(value, w * ptw, size, bold, face) + 4) / pth
            if height <= max_h and (not keep_words or longest_word_width(value, size, bold, face) <= .98 * w * ptw):
                break
        height = min(height, max_h)
        label(value, x, y, w, height, size / font_scale, color, bold, heading=heading, align=align, element_key=element_key, role=role)
        return y + height

    points = content.points
    image_ratio = image_ratio or web_image_ratio
    if rules and item.layout == "cover" and content.image_asset_id in image_ids and image_ratio:
        if recipe == "photo_cover_title_above":
            end = fitted_label(item.title, .06, .055, .88, .40, 48, 36, ink, heading=True)
            end = fitted_label(content.subtitle, .06, end + .016, .88, .18, 19, 14, muted, role="body")
            top = end + .028
            image = Image(x=.06, y=top, w=.88, h=.95-top, asset_id=content.image_asset_id, alt=item.title,
                          **content.picture_placement.model_dump())
        elif compact:
            image = Image(x=.055, y=.33, w=.89, h=.61, asset_id=content.image_asset_id, alt=item.title,
                          **content.picture_placement.model_dump())
            end = fitted_label(item.title, .07, .07, .86, .15, 48, 31, ink, heading=True)
            fitted_label(content.subtitle, .07, end + .014, .86, max(.06, .32 - end), 17, 13, muted, role="body")
        else:
            image = Image(x=.035, y=.045, w=.65, h=.91, asset_id=content.image_asset_id, alt=item.title,
                          **content.picture_placement.model_dump())
            end = fitted_label(item.title, .719, .29, .245, .33, 64, 30, ink, heading=True)
            fitted_label(content.subtitle, .722, end + .028, .225, .22, 19, 14, muted, role="body")
        elements.append(fit_image(image, document, image_ratio))
        return slide

    if rules and item.layout == "cover" and content.cover_callout:
        callout = content.cover_callout
        panel_ink = readable("#FFFFFF", t.accent, t.ink)
        rect(.655, .22, .275, .53, t.accent)
        end = fitted_label(item.title, .07, .225, .55, .39, 54 if compact else 59, 32, ink, heading=True)
        fitted_label(content.subtitle, .07, end + .024, .55, .21, 19, 14, muted, role="body")
        fitted_label(callout.label, .677, .27, .232, .078, 15, 12, panel_ink, bold=True)
        end = fitted_label(callout.value, .677, .36, .232, .15, 43, 27, panel_ink, heading=True)
        fitted_label(callout.detail, .677, end + .027, .232, .21, 16, 12, panel_ink, role="body")
        return slide

    if rules and item.layout == "closing" and content.decision:
        decision = content.decision
        panel_ink = readable("#FFFFFF", t.accent, t.ink)
        end = fitted_label(item.title, .07, .095, .86, .18, 36, 28, ink, heading=True)
        fitted_label(decision.if_missed, .07, end + .015, .86, max(.055, .347 - end), 17, 13, muted, role="body")
        step = .86 / len(decision.gates)
        for i, gate in enumerate(decision.gates):
            x = .07 + i * step
            rect(x, .375, step - .022, .004, accent_fill)
            threshold, measure = gate.threshold, ""
            if mixed_threshold := re.match(r"^([<>≤≥=]*\s*\d+(?:\.\d+)?\s*%)\s+(.+)$", threshold):
                threshold, measure = mixed_threshold.groups()
            fitted_label(threshold, x, .391, step - .024, .09, 33, 24, accent, heading=True)
            label(gate.label, x, .485, step - .024, .104, 16, ink, True)
            detail = "\n".join(part for part in (measure[:1].upper() + measure[1:], gate.detail) if part)
            fitted_label(detail, x, .589, step - .024, .10, 14, 11, muted, role="body")
        rect(.07, .71, .86, .24, t.accent)
        end = fitted_label(f"{decision.action} {decision.request}.", .09, .727, .82, .105, 26, 18, panel_ink, heading=True)
        scope_y = end + .009
        fitted_label(decision.scope, .09, scope_y, .82, .934 - scope_y, 15, 12, panel_ink, role="body")
        return slide

    if rules and item.layout in {"cover", "closing"}:
        centered = recipe == "closing_centered"
        align = "center" if centered else "left"
        side_art = item.layout == "cover" and rules.cover_style in {"panel", "circles"}
        if side_art:
            # Decoration moves above long locked copy rather than consuming the
            # width needed to retain its minimum readable size.
            side_art = all(text_height(value, .66 * ptw, minimum, False, face) + 4 <= height * pth
                           for value, minimum, face, height in (
                               (item.title, max(32, typography["cover"].minimum_pt), rules.heading_font, .40),
                               (content.next_step or content.subtitle, max(14, typography["body"].minimum_pt), t.font, .15),
                           ) if value.strip())
        if item.layout == "cover":
            if rules.cover_style == "panel":
                x, y, w, h = (.76, .125, .172, .59) if side_art else (.76, .07, .172, .12)
                rect(x, y, w, h, accent_fill)
                panel_rule = readable(t.muted, accent_fill, ink, minimum=3)
                for offset in (.127, .686):
                    rect(x + .163 * w, y + offset * h, .657 * w, max(.004, .014 * h), panel_rule)
            elif rules.cover_style == "circles":
                x, y, w = (.77, .09, .19) if side_art else (.81, .06, .09)
                rect(x, y, w, w * ptw / pth, accent_fill, "ellipse")
                x, y, w = (.82, .28, .135) if side_art else (.87, .10, .06)
                rect(x, y, w, w * ptw / pth, blend(bg, accent_fill, .35), "ellipse")
            elif recipe == "editorial_cover" and rules.cover_style == "rules":
                rect(.07, .165, .86, .003, accent_fill)
                rect(.07, .858, .86, .003, accent_fill)
            else:
                rect(.07, .202, .075, .004, accent_fill)
        elif centered:
            rect(.463, .212, .074, .003, accent_fill)
            rect(.20, .80, .60, .002, blend(bg, ink, .34))
        display = recipe in {"closing_display", "closing_centered"}
        end = fitted_label(item.title, .12 if centered else .07, .29 if centered else .24,
                           .76 if centered else .66 if side_art else .86,
                           .40, (60 if compact else 78) if display else (54 if compact else 62), 32, ink,
                           heading=True, align=align)
        end = fitted_label(content.next_step or content.subtitle, .12 if centered else .07, end + .023,
                           .76 if centered else .66 if side_art else .75, .15, 20, 14, muted, align=align, role="body")
        if item.layout == "closing" and not content.next_step:
            visible = [point for point in points[:3] if point.title.casefold() not in {
                item.title.casefold(), (content.next_step or content.subtitle).casefold()
            }]
            if visible:
                width = .86 / len(visible)
                for i, point in enumerate(visible):
                    label(point.title, .07 + i * width, min(.813, end + .034), width - .015, .102, 17, ink, True)
        return slide

    if not rules:
        label(f"{index + 1:02d}  /  {item.layout.upper()}", .068, .065, .65, .045, 12, accent, True)
        rect(.068, .137, .048, .008, accent_fill)
    if item.layout in DISPLAY_LAYOUTS:
        if rules and rules.cover_style == "rules":
            rect(.07, .196, .86, .003, t.accent)
            rect(.07, .90, .86, .003, t.accent)
        elif (rules and rules.cover_style == "panel") or (not rules and editorial):
            rect(.76, .125, .172, .59, t.accent)
            rect(.788, .20, .113, .008, readable(t.muted, t.accent) if rules else "#E5B4A8")
            rect(.788, .53, .113, .008, readable(t.muted, t.accent) if rules else "#E5B4A8")
            if not rules:
                label(f"{index + 1:02d}", .788, .33, .12, .14, 38, "#FFFFFF", True)
        else:
            rect(.77, .09, .19, .32, t.accent, "ellipse")
            rect(.82, .28, .135, .24, t.muted, "ellipse")
        if rules:
            end = fitted_label(item.title, .068, .25, .86 if rules.cover_style == "rules" else .66,
                               .31, typography["cover"].target_pt, 30, ink, heading=True)
            fitted_label(content.subtitle, .07, end + .032, .68, .19, 21, 15, muted, role="body")
        else:
            label(item.title, .068, .24, .66, .28, 47, ink, True, heading=True)
            label(content.subtitle, .07, .58, .64 if editorial else .70, .13, 23, muted, role="body")
        if points and item.layout == "closing":
            for i, point in enumerate(points[:3]):
                label(point.title, .07 + .295 * i, .77, .274, .13, 18, ink, True)
        elif points and item.layout == "quote":
            label(points[0].title, .07, .77, .65, .08, 18, muted)
        return slide

    body_top = .445
    if rules:
        end = fitted_label(item.title, .07, .095, .86, .20, typography["headline"].target_pt, 26, ink,
                           bold=rules.heading_font.casefold() == t.font.casefold(), heading=True, prefer_one_line=True)
        if content.subtitle:
            end = fitted_label(content.subtitle, .07, end + .018, .86, .112, typography["body"].target_pt, 14, muted, role="body")
        body_top = max(.252, min(.455, end + .035))
    else:
        label(item.title, .068, .17, .87, .17, 35, ink, True, heading=True)
        label(content.subtitle, .07, .352, .86, .08, 17, muted, role="body")
    if rules and recipe == "diagram_swimlane" and content.swimlane:
        draw_swimlane(elements, content.swimlane, body_top, t, bg, fitted_label)
        return slide
    if rules and content.diagram and item.layout in {"process", "summary", "comparison", "showcase"} and (
            recipe != "standard" or not points):
        draw_diagram(elements, content.diagram, body_top, t, bg, fitted_label, compact=compact,
                     vertical_flow=recipe == "diagram_vertical")
        return slide
    if rules and points and recipe in {"feature_panels", "feature_rows"}:
        panel = t.surface if abs(contrast(t.surface, bg) - 1) >= .16 else blend(bg, t.accent, .14)
        panel_ink = readable(ink, panel, t.ink)
        body_ink = readable(muted, panel, panel_ink)
        panel_accent = readable(accent_fill, panel, panel_ink, minimum=3)
        for index, point in enumerate(points[:3]):
            name = point.title.strip().casefold()
            if sum(other.title.strip().casefold() == name for other in points) > 1:
                name = f"{name}:{point.body.strip().casefold()}"
            identity = f"feature:{name}"
            horizontal = recipe == "feature_panels"
            gap = .018
            step = (.86 + gap) / min(len(points), 3)
            row_step = (.914 - body_top) / min(len(points), 3)
            x, y = (.07 + index * step, body_top + .015) if horizontal else (.07, body_top + index * row_step)
            w, h = (step - gap, .89 - y) if horizontal else (.86, row_step - .012)
            rect(x, y, w, h, panel, element_key=f"{identity}:background")
            rect(x, y, w if horizontal else .005, .006 if horizontal else h, panel_accent, element_key=f"{identity}:accent")
            prefix = rf"^(?:Step\s+0?{index + 1}|0?{index + 1})\s*[:.–-]\s*"
            title = re.sub(prefix, "", point.title, flags=re.I)
            if horizontal:
                icon_y = y + .044
                if point.icon:
                    icon = slide_icon(document, point.icon, x + .02, icon_y, min(.062, h * .14), panel)
                    icon.element_key = f"{identity}:icon"
                    elements.append(icon)
                end = fitted_label(title, x + .02, y + h * .28, w - .04, h * .31,
                                   28, 17, panel_ink, bold=True, heading=True, element_key=f"{identity}:heading")
                fitted_label(point.body, x + .02, end + .027, w - .04, y + h - end - .042, 16, 13, body_ink,
                             element_key=f"{identity}:body", role="body")
            else:
                split = .31
                fitted_label(title, x + .024, y + .024, split, max(.05, h - .037),
                             23, 14, panel_ink, bold=True, heading=True, element_key=f"{identity}:heading")
                fitted_label(point.body, x + split + .06, y + .024, w - split - .084, max(.05, h - .037),
                             18, 13, body_ink, element_key=f"{identity}:body", role="body")
        return slide
    first_body_element = len(elements)
    use_icons = item.layout in STANDARD_ICON_LAYOUTS and not (content.chart or content.table or content.stacked_comparison)
    if rules and item.layout == "chart" and content.stacked_comparison and slide.source_ids:
        data = content.stacked_comparison
        totals = [sum(component.values[index] for component in data.series) for index in range(len(data.categories))]
        large_enough = len(totals) == 2 and len(data.series) <= 3 and max(totals) > 0 and all(
            value >= .105 * max(totals) for component in data.series for value in component.values
        )
        chart = Chart(x=.07, y=.517, w=.55, h=.373, chart="stacked_column", categories=data.categories,
                      series=data.series, value_prefix=data.value_prefix, show_values=large_enough)
        elements.append(chart)
        label(data.label, .07, .449, .55, .057, 16, ink, True)
        rows = [["Component", *data.categories]]
        for component in data.series:
            rows.append([component.name, *[display_value(value, data.value_prefix) for value in component.values]])
        if len(totals) == 2 and totals[0] != totals[1]:
            low = min(range(2), key=totals.__getitem__)
            rect(.646, .504, .284, .185, t.surface)
            value_ink = readable(t.accent, t.surface, ink)
            fitted_label(display_value(abs(totals[0] - totals[1]), data.value_prefix), .663, .518, .25, .083,
                         35, 21, value_ink, heading=True)
            fitted_label(data.difference_label or f"Lower for {data.categories[low]}", .663, .606, .25, .066,
                         15, 11, readable(t.ink, t.surface))
            if large_enough:
                style_chart(chart, t, bg)
                for index, component in enumerate(data.series):
                    y = .716 + .057 * index
                    palette = chart.colors()
                    rect(.647, y + .006, .010, .018, palette[index % len(palette)])
                    fitted_label(component.name, .668, y, .25, .052, 14, 10, muted)
            else:
                elements.append(Table(x=.646, y=.707, w=.284, h=.181, rows=rows, size=max(9, 12 * factor)))
        else:
            elements.append(Table(x=.646, y=.54, w=.284, h=.089 * len(rows), rows=rows, size=max(9, 13 * factor)))
        label(data.caption, .07, .913, .86, .048, 13, muted)
    elif rules and item.layout == "metrics" and content.allocation:
        allocation = content.allocation
        amount = sum(line.amount for line in allocation.lines)
        total = display_value(amount, allocation.value_prefix)
        palette = categorical_palette(t, bg)
        if amount:
            x = .07
            for index, line in enumerate(allocation.lines):
                width = .86 * (line.amount / amount)
                if width > 0:
                    elements.append(Shape(x=x, y=.452, w=width, h=.052, fill=palette[index % len(palette)],
                                          border=bg, z=-1))
                    x += width
        label(total, .07, .52, .32, .15, 39, accent, True, heading=True)
        label("Total allocation", .07, .684, .32, .07, 16, muted)
        step = .38 / len(allocation.lines)
        for i, line in enumerate(allocation.lines):
            y = .526 + i * step
            rect(.414, y, .516, .003, t.surface)
            rect(.397, y + .020, .009, .015, palette[i % len(palette)])
            size = min(18, max(11, step * 207))
            label(line.label, .414, y + .009, .326, step - .011, size, ink, True)
            label(display_value(line.amount, allocation.value_prefix), .747, y + .009,
                  .183, step - .011, size, ink, True, align="right")
    elif rules and item.layout == "metrics" and content.readout and slide.source_ids:
        readout = content.readout
        step = .86 / len(readout.metrics)
        for index, metric in enumerate(readout.metrics):
            x = .07 + step * index
            lead = index == readout.highlight
            width = step - .022
            rect(x, .463, width, .004, accent_fill if lead else blend(bg, ink, .30))
            fitted_label(metric.title, x, .492, width, .075, 16, 12, ink, bold=True)
            fitted_label(metric.value, x, .586, width, .131, 42 if lead else 35, 23, accent if lead else ink, heading=True)
            fitted_label(metric.body, x, .75, width, .124, 15, 12, muted, role="body")
    elif rules and item.layout == "timeline" and content.rollout:
        rollout = content.rollout
        phase_ink = readable(ink, t.surface, t.ink, bg)
        step = .86 / len(rollout.phases)
        for i, phase in enumerate(rollout.phases):
            x = .07 + i * step
            rect(x, .473, step - .009, .168, t.surface)
            rect(x, .473, step - .009, .005, t.accent)
            label(phase.period, x + .012, .491, step - .033, .053, 15, phase_ink, True)
            label(phase.milestone, x + .012, .548, step - .033, .071, 16, phase_ink)
        if rollout.risks:
            label("Risk", .07, .676, .248, .04, 13, muted, True)
            label("Mitigation", .338, .676, .59, .04, 13, muted, True)
            for i, pair in enumerate(rollout.risks):
                y = .725 + i * .075
                rect(.07, y, .86, .002, t.surface)
                label(pair.risk, .07, y + .008, .25, .065, 15, ink, True)
                label(pair.mitigation, .338, y + .008, .592, .065, 15, ink, role="body")
    elif item.layout == "research" and content.table_ready:
        rows = [[cell[:180] for cell in row] for row in content.table]
        focus = {value.strip().casefold() for value in content.table_focus[:2]} if rules else set()
        selected = [i for i, row in enumerate(rows[1:], 1) if row[0].strip().casefold() in focus]
        criteria = {value.strip().casefold() for value in content.table_focus_columns}
        focus_columns = [i for i, header in enumerate(content.table[0][1:], 1)
                         if header.strip().casefold() in criteria] if selected else []
        if len(selected) == len(rows) - 1 and not focus_columns:
            selected = []
        full = recipe == "dense_table_full"
        elements.append(Table(x=.055 if full else .07, y=.446 if full else .46, w=.89 if full else .86,
                              h=.50 if full else .44, rows=rows, color=t.ink, fill=t.surface,
                              focus_rows=selected, focus_columns=focus_columns, element_key="exhibit:table",
                              header_fill=accent if consulting else None,
                              header_color=readable("#FFFFFF", accent) if consulting else None))
    elif content.image_asset_id in image_ids and item.layout in IMAGE_LAYOUTS:
        image = Image(x=.55, y=.45, w=.38, h=.44, asset_id=content.image_asset_id, alt=item.title,
                          **content.picture_placement.model_dump())
        elements.append(image)
        if image_ratio:
            for i, point in enumerate(points[:3]):
                y = .46 + i * .146
                label(point.title, .07, y, .43, .056, 18, ink, True)
                label(point.body, .07, y + .06, .43, .08, 15, muted, role="body")
        else:
            for i, point in enumerate(points[:2]):
                y = .46 + i * .21
                label(point.title, .07, y, .43, .06, 20, ink, True)
                label(point.body, .07, y + .07, .43, .125, 16, muted, role="body")
    elif item.layout == "chart" and slide.source_ids and content.chart_ready:
        data = content.chart
        direct_labels = bool(rules and data.chart != "pie" and len(data.values) <= 6)
        has_external_source = any(source.url and source.id in slide.source_ids for source in document.sources)
        has_table = data_driven and (not direct_labels or has_external_source)
        highlight = data.highlight if direct_labels and data.highlight is not None and data.highlight < len(data.values) else None
        callout = data.comparison_label(highlight)
        chart_width = .54 if has_table else .86
        show_caption = data_driven or bool(callout)
        try:
            elements.append(Chart(x=.07, y=.52 if callout else .51 if show_caption else .45, w=chart_width,
                                  h=.39 if show_caption else .46, categories=data.categories, show_values=direct_labels,
                                  highlight=highlight, series=[{"name": data.label, "values": data.values}],
                                  chart=data.chart, color=t.accent, value_prefix=data.value_prefix, value_suffix=data.value_suffix,
                                  element_key="exhibit:chart"))
        except ValueError:
            slide.warnings.append("The chart data could not be rendered.")
        else:
            if show_caption:
                caption_width = chart_width * .58 if callout else chart_width
                fitted_label(data.label, .07, .45, caption_width, .066, 15, 10, ink)
            if callout:
                fitted_label(callout, .08 + caption_width, .443, chart_width - caption_width - .01,
                             .073, 20, 11, accent, bold=True, align="right")
            if has_table:
                rows = [["Category", "Value"], *[[category, f"{display_value(value, data.value_prefix)}{data.value_suffix}"]
                        for category, value in zip(data.categories, data.values)]]
                elements.append(Table(x=.635, y=.45, w=.295, h=min(.44, .060 * len(rows)), rows=rows,
                                      size=max(9, min(14 * factor, 132 / len(rows))), color=ink, fill=t.surface,
                                      header_fill=accent, header_color="#FFFFFF"))
    elif item.layout == "timeline" and points:
        points = points[:3]
        width = .86 / len(points)
        rect(.07, .542, .86, .007, t.surface)
        for i, point in enumerate(points):
            x = .07 + width * i
            rect(x, .521, .026, .047, t.accent, "ellipse")
            offset = 0
            if use_icons and point.icon:
                icon = slide_icon(document, point.icon, x, .464, .047, bg)
                elements.append(icon)
                offset = icon.w + .008
            label(point.value or f"0{i + 1}", x + offset, .465, width - .024 - offset, .047, 14, accent, True)
            label(point.title, x, .605, width - .024, .095, 20, ink, True)
            label(point.body, x, .72, width - .024, .18, 16, muted, role="body")
    elif rules and item.layout in {"process", "metrics"} and points and (
        compact or len(points) > 3 or any(len(point.title) > 38 or len(point.body) > 85 for point in points)
    ):
        visible = points[:4]
        row_height = .48 / len(visible)
        for i, point in enumerate(visible):
            y = .45 + i * row_height
            with_icon = bool(use_icons and point.icon)
            if item.layout == "process" or point.value and (slide.source_ids or not any(c.isdigit() for c in point.value)):
                marker = f"{i + 1:02}" if item.layout == "process" else point.value
                label(marker, .07, y + .008, .09, row_height - .012, 23, accent, True)
            if with_icon:
                elements.append(slide_icon(document, point.icon, .124, y + .011, .040, bg))
            if i:
                rect(.07, y, .86, .002, t.surface)
            title = re.sub(rf"^Step\s+0?{i + 1}\s*[:.–-]\s*", "", point.title, flags=re.IGNORECASE)
            end = fitted_label(title or point.title, .18, y + .008, .75, row_height * .40, 21, 15, ink, bold=True)
            fitted_label(point.body, .18, end + .002, .75, max(.025, y + row_height - end - .008), 16, 13, muted, role="body")
    elif item.layout in {"comparison", "metrics", "process", "showcase"} and points:
        points = points[:2 if item.layout == "comparison" and not rules else 3]
        gap = {"compact": .012, "regular": .022, "spacious": .032}[rules.spacing] if rules else .022
        width = (.864 - gap * (len(points) - 1)) / len(points)
        card_ink = readable(t.ink, t.surface) if rules else ink
        card_muted = readable(t.muted, t.surface, card_ink) if rules else muted
        card_accent = readable(t.accent, t.surface, card_ink) if rules else accent
        for i, point in enumerate(points):
            x = .068 + i * (width + gap)
            if rules and item.layout == "comparison":
                rect(x, .463, width, .004, accent_fill)
            else:
                rect(x, .463, width, .426, t.surface, rules.card_shape if rules else "round")
            value = point.value if slide.source_ids or not any(character.isdigit() for character in point.value) else ""
            offset = 0
            if use_icons and point.icon:
                icon = slide_icon(document, point.icon, x + .018, .503, .062,
                                  bg if rules and item.layout == "comparison" else t.surface)
                elements.append(icon)
                offset = icon.w + .012
            if not value:
                value = f"0{i + 1}" if item.layout == "process" else ""
            label(value, x + .018 + offset, .495, width - .036 - offset, .083, 25, card_accent, True)
            if rules and item.layout == "comparison":
                end = fitted_label(point.title, x + .018, .575 if offset or value else .493,
                                   width - .036, .139, 20, 15, ink, bold=True)
                fitted_label(point.body, x + .018, end + .012, width - .036, .94 - end - .012, 16, 13, muted, role="body")
            else:
                label(point.title, x + .018, .596, width - .036, .09, 18, card_ink, True)
                label(point.body, x + .018, .70, width - .036, .157, 16 if not compact else 15, card_muted, role="body")
    else:
        icon_column = use_icons and any(point.icon for point in points[:3])
        text_x = .125 if icon_column else .103
        text_width = .913 - text_x
        for i, point in enumerate(points[:3]):
            y = .46 + .146 * i
            if icon_column and point.icon:
                elements.append(slide_icon(document, point.icon, .068, y + .008, .047, bg))
            else:
                rect(.07, y + .021, .014, .025, t.accent, "ellipse")
            label(point.title, text_x, y, text_width, .062, 20, ink, True)
            label(point.body, text_x, y + .063, text_width, .075, 16, muted, role="body")
    if rules:
        scale = (.955 - body_top) / (.955 - .445)
        for element in elements[first_body_element:]:
            element.y = body_top + (element.y - .445) * scale
            element.h *= scale
            if isinstance(element, Chart):
                style_chart(element, t, bg)
            if isinstance(element, Image):
                ratio = image_ratio if element.asset_id == content.image_asset_id else 1
                if ratio:
                    fitted = fit_image(element, document, ratio)
                    element.x, element.y, element.w, element.h = fitted.x, fitted.y, fitted.w, fitted.h
    if len(points) > 3:
        remainder = "\n".join(f"{p.title}: {p.body}" for p in points[3:])
        slide.notes = (slide.notes + "\n" + remainder).strip()[:7000]
    return style_slide_tables(slide, t, preserve_palette=consulting, table_floor=typography["table"].minimum_pt)


def compose_visual_slide(item, content, document, brand, image_ids, source_ids, recipe):
    role = VISUAL_LAYOUTS[content.visual.type]
    heading = item.title.strip() or content.title.strip() or "Untitled slide"
    if brand:
        header = content.model_copy(update={"points": [], "subtitle": "", "chart": None, "table": None,
                                            "image_asset_id": None})
        slide = compose_branded(item.model_copy(update={"layout": role, "title": heading}), header, document,
                                image_ids, brand)
        layout = next(layout for layout in brand.layouts if layout.index == slide.brand_layout_index)
        slot = max((slot for slot in layout.slots if slot.kind in {"body", "chart", "table"}),
                   key=lambda slot: slot.w * slot.h, default=None)
        top = max([.23, *(element.y + element.h + .02 for element in slide.elements)])
        slot_top = max(top, slot.y) if slot else top
        if slot and slot.w >= .45 and slot.y + slot.h - slot_top >= .36:
            x, top, w, bottom = slot.x, slot_top, slot.w, slot.y + slot.h
        else:
            before_heading = min((element.y for element in slide.elements), default=0) - .025
            top, bottom = (.055, before_heading) if before_heading - .055 > .945 - top else (min(top, .90), .945)
            x, w = .06, .88
        frame = ExhibitCanvas(document, slide, (x, top, w, bottom - top))
    else:
        slide = Slide(title=heading, layout=role, background=document.theme.background, notes=content.notes,
                      source_ids=list(dict.fromkeys(key for key in content.source_ids if key in source_ids)))
        frame = ExhibitCanvas(document, slide, (.06, .055, .88, .89))
        frame.label(heading, 0, 0, 1, .184, 35, role="headline", bold=True, emphasis=content.decorations.emphasis if content.decorations else ())
        heading_box = slide.elements[-1]
        top = max(.17, heading_box.y + heading_box.h + .042)
        frame = ExhibitCanvas(document, slide, (.06, top, .88, .945 - top))
    if content.subtitle:
        frame.label(content.subtitle, 0, 0, 1, .115, 15, color=frame.muted, role="body")
    start = .13 if content.subtitle else 0
    frame.line((0, start), (1, start), color=frame.rule, element_key="exhibit:header-rule")
    bounds = (frame.x, frame.y + frame.h * (start + .025), frame.w, frame.h * (1 - start - .025))
    status = evidence_label(exhibit_bindings(content, document.sources, slide.source_ids,
                                             planned_annotations=item.annotations))
    if status == UNVERIFIED and not slide.source_ids:
        slide.warnings.append("Quantitative exhibit omitted because no source was cited.")
        frame.label("Source data needed", 0, start + .18, 1, .18, 20, color=frame.muted,
                    element_key="exhibit:missing-data")
        return slide
    if status == UNVERIFIED:
        slide.warnings.append("Some exhibit values lack matching source observations.")
    if status:
        frame.label(status, 0, .956, 1, .044, 11, color=frame.ink, bold=True, element_key=STATUS_KEY, role="footnote")
        bounds = (*bounds[:3], bounds[3] - frame.h * .06)
    compose_exhibit(document, slide, content, bounds, annotations=content.annotations,
                    planned_annotations=item.annotations, recipe=recipe)
    return slide
