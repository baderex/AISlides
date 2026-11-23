"""Non-mutating review of measured slides, including the ink inside connector labels."""
from app.domain import Image, Line, Shape, Text
from app.rendering import (TEXT_LEADING, TEXT_SCALE, connector_points, flattened, physical,
                           raster_text, validate_slide)


def segment_crosses(start, end, rect):
    # Intersect the segment's parameter interval with the interior of each axis.
    first, last = 0., 1.
    for axis in range(2):
        distance = end[axis] - start[axis]
        low, high = rect[axis], rect[axis + 2]
        if distance == 0:
            if not low < start[axis] < high:
                return False
        else:
            a, b = sorted(((low - start[axis]) / distance, (high - start[axis]) / distance))
            first, last = max(a, first), min(b, last)
    return first < last


def text_rects(label, frame, width, height):
    x, y, w, h = frame
    layout = raster_text(label.text, round(w * width), round(h * height), label.size, label.bold,
                         label.font, label.align, label.single_line, label.line_spacing or TEXT_LEADING)
    for dx, dy, run in layout.lines:
        left, top, right, bottom = layout.face.getbbox(run, anchor="lt")
        if right - left > 4 and bottom - top > 4:
            yield (x + (dx + left + 2) / width, y + (dy + top + 2) / height,
                   x + (dx + right - 2) / width, y + (dy + bottom - 2) / height)


def readability_warnings(document, slide):
    layers = list(flattened(slide.elements))
    assets = {el.asset_id for el, *_ in layers if isinstance(el, Image)}
    warnings = validate_slide(document, slide.model_copy(deep=True), assets)
    shapes = {el.id: frame for el, *frame in layers if isinstance(el, Shape)}
    connectors = [(el, connector_points(el, *frame, shapes)) for el, *frame in layers if isinstance(el, Line)]
    if not connectors:
        return warnings
    width, height = (v * TEXT_SCALE for v in physical(document))
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
