"""Review the measured composition and compare its editable export with the preview."""
from collections import Counter
from dataclasses import dataclass
from math import isfinite

from app.domain import Chart, Image, Line, Shape, Table, Text
from app.html_layout.markup import DRAWING_TAGS, parse_markup
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


def drawing_content(node):
    """Keep a drawing's relationships without pinning its layout coordinates.

    Named/labeled boxes and directed connectors are content. Anonymous rectangles
    may be decoration, so changes to them go through the workflow's comparison
    with the previous reviewed geometry. Ambiguous connections retain the stricter
    coordinate signature instead of guessing which entities they join.
    """
    children = node["children"]
    geometry = {"id", "x", "y", "w", "h", "x1", "y1", "x2", "y2",
                "from", "to", "head", "points", "form"}
    strict = tuple((child["tag"], child["t"], tuple(sorted(
        (k, v) for k, v in child["a"].items() if k in geometry))) for child in children)

    def value(raw):
        result = float(str(raw).removesuffix("%"))
        if not isfinite(result) or not 0 <= result <= 100:
            raise ValueError("Invalid drawing coordinate")
        return result

    def frame(child):
        return tuple(value(child["a"].get(k, 0)) for k in ("x", "y", "w", "h"))

    def distance(point, bounds):
        x, y, w, h = bounds
        return max(x-point[0], point[0]-x-w, 0)**2 + max(y-point[1], point[1]-y-h, 0)**2

    def copy(child):
        return child["t"] if child["a"].get("role") == "code" else " ".join(child["t"].split())

    def form(child):
        shape = child["a"].get("form", "rect")
        return "rect" if shape == "round" else shape

    try:
        boxes = [child for child in children if child["tag"] == "box"]
        labels = [child for child in children if child["tag"] == "label"]
        bounds, label_bounds = [frame(child) for child in boxes], [frame(child) for child in labels]
        owners = {}
        for i, (x, y, w, h) in enumerate(label_bounds):
            containers = [j for j, box in enumerate(bounds) if distance((x+w/2, y+h/2), box) == 0]
            if containers:
                smallest = min(bounds[j][2] * bounds[j][3] for j in containers)
                owners[i], = [j for j in containers if bounds[j][2] * bounds[j][3] == smallest]
        references = [("node", child["a"].get("id", ""), form(child),
                       tuple(sorted(copy(labels[i]) for i, owner in owners.items() if owner == j)))
                      for j, child in enumerate(boxes)]
        identified = {j for j, child in enumerate(boxes) if child["a"].get("id") or j in owners.values()}
        if len({references[j] for j in identified}) != len(identified):
            raise ValueError("Ambiguous drawing nodes")
        content = Counter(references[j] for j in identified)
        endpoint_labels = set()

        def endpoint(child, end):
            attrs = child["a"]
            point = tuple(value(attrs.get(k + ("1" if end == "from" else "2"), 0)) for k in ("x", "y"))
            if anchor := attrs.get(end):
                key, side = anchor.split(":")
                owner, = [j for j, box in enumerate(boxes) if box["a"].get("id") == key]
                if side not in {"top", "right", "bottom", "left"} or distance(point, bounds[owner]) > 4:
                    raise ValueError("Anchor and visible endpoint disagree")
                return references[owner], point
            # Prefer visible endpoint labels; this also covers freeform diagrams
            # with no node boxes, such as Browser -> Server.
            near = [(distance(point, box), i) for i, box in enumerate(label_bounds)]
            if near and min(d for d, _ in near) <= 100:
                nearest = min(d for d, _ in near)
                i, = [i for d, i in near if abs(d-nearest) < .0001]
                endpoint_labels.add(i)
                return ("label", copy(labels[i]), references[owners[i]] if i in owners else ()), point
            near = [(distance(point, bounds[j]), j) for j in identified]
            if near and min(d for d, _ in near) <= 100:
                nearest = min(d for d, _ in near)
                j, = [j for d, j in near if abs(d-nearest) < .0001]
                return references[j], point
            raise ValueError("Unresolved drawing endpoint")

        edges = []
        for child in children:
            if child["tag"] == "arrow":
                start, a = endpoint(child, "from")
                end, b = endpoint(child, "to")
                if start == end:
                    raise ValueError("Ambiguous freeform loop")
                edge = ("edge", start, end, child["a"].get("head") != "false")
                content[edge] += 1
                edges.append((edge, a, b))
            elif child["tag"] == "poly":
                points = [tuple(value(v) for v in pair.split(",")) for pair in child["a"]["points"].split()]
                left, top = min(p[0] for p in points), min(p[1] for p in points)
                scale = max(max(p[0] for p in points)-left, max(p[1] for p in points)-top)
                content[("polygon", tuple((round((x-left)/scale, 6), round((y-top)/scale, 6))
                                           for x, y in points))] += 1
        for j, child in enumerate(boxes):
            if j not in identified and form(child) != "rect":
                content[("shape", form(child))] += 1
        for i, child in enumerate(labels):
            if i in owners or i in endpoint_labels:
                continue
            x, y, w, h = label_bounds[i]
            point, near = (x+w/2, y+h/2), []
            for edge, a, b in edges:
                dx, dy = b[0]-a[0], b[1]-a[1]
                along = ((point[0]-a[0])*dx + (point[1]-a[1])*dy) / (dx*dx + dy*dy)
                separation = (point[0]-a[0]-along*dx)**2 + (point[1]-a[1]-along*dy)**2
                if 0 < along < 1 and separation <= (h/2+4)**2:
                    near.append((separation, edge))
            if near:
                nearest = min(d for d, _ in near)
                edge, = {edge for d, edge in near if abs(d-nearest) < .0001}
                content[("edge-label", copy(child), edge)] += 1
        return tuple(sorted(content.items(), key=repr))
    except (ValueError, KeyError, ZeroDivisionError, IndexError):
        return (("geometry", strict),)


def markup_content(markup):
    """A visual alternative must retain copy, assigned media and diagram data."""
    content = Counter()
    pending = [parse_markup(markup)]
    data_attrs = {"asset", "from", "to", "label", "value", "kind", "unit", "suffix", "decimals",
                  "periods", "start", "end", "actors", "milestone"}
    while pending:
        node = pending.pop()
        pending.extend(node["children"])
        if node["tag"] == "drawing":
            if drawing := drawing_content(node):
                content[("drawing", drawing)] += 1
        if node["t"]:
            content[("copy", node["t"] if node["a"].get("role") == "code" else " ".join(node["t"].split()))] += 1
        if node["tag"] == "matrix":
            rows = tuple(tuple(cell["t"] for cell in row["children"]) for row in node["children"])
            content[("matrix", rows)] += 1
        attributes = tuple(sorted((k, v) for k, v in node["a"].items() if k in data_attrs)) if node["tag"] not in DRAWING_TAGS else ()
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


HARD_WARNINGS = ("Chart labels", "Low text contrast", "Low table", "Low chart", "Missing image", "Overlapping text ink",
                 "Text may overflow", "Some exhibit cells", "Connector crosses label", "Export fit")


def evaluate_quality(slide, previous):
    hard = [warning for warning in slide.warnings if warning.startswith(HARD_WARNINGS)]
    for el, *_ in flattened(slide.elements):
        if isinstance(el, Chart) and (any(not name.strip() for name in el.categories)
                                      or any(not series.name.strip() for series in el.series)):
            hard.append("Chart categories and series must have visible names.")
    if any(isinstance(el, (Text, Table)) and el.size + .01 < (el.minimum_pt or 9.5) for el, *_ in flattened(slide.elements)):
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
