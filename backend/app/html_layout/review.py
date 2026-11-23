"""Review the measured composition and compare its editable export with the preview."""
from collections import Counter
from dataclasses import dataclass

from app.domain import Chart, Image, Line, Shape, Table, Text
from app.html_layout.markup import parse_markup
from app.render_geometry import segment_crosses
from app.rendering import (TEXT_LEADING, TEXT_SCALE, connector_points, flattened, physical,
                           raster_text, validate_slide)


def text_rects(label, frame, width, height):
    x, y, w, h = frame
    layout = raster_text(label.text, round(w * width), round(h * height), label.size, label.bold,
                         label.font, label.align, label.single_line, label.line_spacing or TEXT_LEADING)
    for dx, dy, run in layout.lines:
        left, top, right, bottom = layout.face.getbbox(run, anchor="lt")
        if right - left > 4 and bottom - top > 4:
            yield (x + (dx + left + 2) / width, y + (dy + top + 2) / height,
                   x + (dx + right - 2) / width, y + (dy + bottom - 2) / height)


def severe_overlap(a, b):
    area = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    return area > .18 * min((a[2]-a[0])*(a[3]-a[1]), (b[2]-b[0])*(b[3]-b[1]))


def readability_warnings(document, slide):
    layers = list(flattened(slide.elements))
    assets = {el.asset_id for el, *_ in layers if isinstance(el, Image)}
    warnings = validate_slide(document, slide.model_copy(deep=True), assets)
    width, height = (v * TEXT_SCALE for v in physical(document))
    prior = []
    for el, *frame in layers:
        if not isinstance(el, Text) or not el.text.strip() or el.rotation:
            continue
        x, y, w, h = frame
        for other, (ox, oy, ow, oh) in prior:
            if min(x+w, ox+ow) <= max(x, ox) or min(y+h, oy+oh) <= max(y, oy):
                continue
            if any(severe_overlap(a, b) for a in text_rects(el, frame, width, height)
                   for b in text_rects(other, (ox,oy,ow,oh), width, height)):
                warnings.append(f"Overlapping text ink: {el.text[:52]}")
                break
        prior.append((el, frame))
    shapes = {el.id: frame for el, *frame in layers if isinstance(el, Shape)}
    connectors = [(el, connector_points(el, *frame, shapes)) for el, *frame in layers if isinstance(el, Line)]
    if not connectors:
        return warnings
    for label, x, y, w, h in layers:
        if not isinstance(label, Text) or label.rotation:
            continue
        rects = list(text_rects(label, (x, y, w, h), width, height))
        backgrounds = [shape.z for shape, sx, sy, sw, sh in layers if isinstance(shape, Shape)
                       and shape.shape == "rect" and not shape.polygon and shape.opacity == 1
                       and shape.z <= label.z and sx <= x and sy <= y and sx + sw >= x + w and sy + sh >= y + h]
        for line, points in connectors:
            if any(z > line.z for z in backgrounds):
                continue
            if any(segment_crosses(a, b, rect) for a, b in zip(points, points[1:]) for rect in rects):
                warnings.append(f"Connector crosses label: {label.text[:55]}")
                break
    return warnings


def composition_summary(slide):
    layers = list(flattened(slide.elements))
    texts = [(el, x, y) for el, x, y, _, _ in layers if isinstance(el, Text)]
    heading = next((item for item in texts if item[0].text == slide.title),
                   max(texts, key=lambda item: item[0].size, default=None))
    figures = [(el.type, x, y, w, h) for el, x, y, w, h in layers if isinstance(el, (Table, Chart, Image))]
    if slide.spec and (region := slide.spec.html_region):
        kind = (slide.spec.content.get("visual") or {}).get("type", "chart")
        figures.append((kind, region.x, region.y, region.w, region.h))
    if figures:
        kind, x, y, w, h = max(figures, key=lambda f: f[3] * f[4])
        position = "full width" if w >= .7 else "left" if x+w/2 < .44 else "right" if x+w/2 > .56 else "center"
        pattern = f"{kind}, {position}"
    elif any(isinstance(el, Line) for el, *_ in layers):
        pattern = "drawing"
    else:
        columns = {round(x * 5) for el, x, y in texts if not heading or el is not heading[0]}
        pattern = "text columns" if len(columns) > 1 else "text stack"
    return {"layout":slide.layout, "pattern":pattern,
            "headline":({"x":round(heading[1], 2), "y":round(heading[2], 2), "align":heading[0].align,
                         "font":heading[0].font, "pt":round(heading[0].size, 1)} if heading else None),
            "left_margin":round(min((x for el, x, *_ in layers if isinstance(el, (Text, Table, Chart, Image))), default=0), 2)}


def design_memory(slides):
    summaries = [composition_summary(slide) for slide in slides]
    return {"patterns_used":dict(Counter(item["pattern"] for item in summaries)), "recent":summaries[-3:]}


def markup_content(markup):
    """A visual alternative must retain copy, assigned media and diagram data."""
    content = Counter()
    pending = [parse_markup(markup)]
    data_attrs = {"asset", "from", "to", "label", "value", "kind", "unit", "suffix", "decimals",
                  "periods", "start", "end", "actors", "milestone"}
    while pending:
        node = pending.pop()
        pending.extend(node["children"])
        if node["t"]:
            content[("copy", " ".join(node["t"].split()))] += 1
        if node["tag"] == "matrix":
            rows = tuple(tuple(cell["t"] for cell in row["children"]) for row in node["children"])
            content[("matrix", rows)] += 1
        attributes = tuple(sorted((k, v) for k, v in node["a"].items() if k in data_attrs))
        if attributes:
            content[(node["tag"], attributes)] += 1
        if node["tag"] in {"waterfall", "gantt", "sequence", "sankey"}:
            entries = tuple(tuple(sorted((k,v) for k,v in child["a"].items() if k in data_attrs))
                            for child in node["children"])
            content[(node["tag"], attributes, entries)] += 1
    return content


@dataclass
class Quality:
    hard: list[str]
    soft: list[str]
    penalty: float


HARD_WARNINGS = ("Low text contrast", "Low table", "Low chart", "Missing image", "Overlapping text ink",
                 "Text may overflow", "Some exhibit cells", "Connector crosses label", "Export fit")


def evaluate_quality(slide, previous):
    hard = [warning for warning in slide.warnings if warning.startswith(HARD_WARNINGS)]
    if any(isinstance(el, (Text, Table)) and el.size < 9.5 for el, *_ in flattened(slide.elements)):
        hard.append("Text falls below a readable size. Enlarge the exhibit or shorten the labels.")
    if not any(isinstance(el, (Chart, Table, Image, Line)) or isinstance(el, Text) and el.text.strip()
               or isinstance(el, Shape) and el.fill.casefold() != slide.background.casefold()
               for el, *_ in flattened(slide.elements) if el.opacity > 0):
        hard.append("The page has no visible content. Include the planned headline or visual.")
    if any(el.element_key == "exhibit:missing-data" for el in slide.elements):
        hard.append("The planned exhibit is missing. Supply the required exhibit data.")
    soft = []
    layers = list(flattened(slide.elements))
    text = [el for el, *_ in layers if isinstance(el, Text) and el.text.strip()]
    if len(text) >= 3 and max(t.size for t in text) < 1.25 * sorted(t.size for t in text)[len(text)//2]:
        soft.append("Give the headline a clearer size hierarchy.")
    if len([el for el, *_ in layers if isinstance(el, Shape)]) > max(16, 4 * len(text)) and not slide.spec:
        soft.append("Reduce decorative shapes around the primary content.")
    if len(text) >= 2 and slide.layout not in {"cover", "closing", "quote", "section"} and max(
            (y+h for el, x, y, w, h in layers if isinstance(el, (Text, Table, Chart, Image))), default=1) < .40:
        soft.append("Distribute content through more of the canvas.")
    recent = previous[-2:]
    if len(recent) == 2 and len({s.layout for s in [*recent, slide]}) > 1 and all(
            composition_summary(s)["pattern"] == composition_summary(slide)["pattern"] for s in recent):
        soft.append("Vary the primary composition from the two preceding slides.")
    soft.extend(w for w in slide.warnings if w.startswith(("Export difference", "Overlapping text box")))
    return Quality(hard, soft, len(soft))
