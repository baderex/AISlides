import io
import re
from dataclasses import dataclass
from functools import lru_cache
from itertools import accumulate
from math import hypot
from pathlib import Path

from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFError, TTFont
from reportlab.pdfgen import canvas

from app.domain import Chart, Document, Group, Image, Line, Shape, Slide, Table, Text
from app.lucide import bundled_icon, icon_asset_id


TEXT_SCALE = 2
TEXT_LEADING = 1.13
CELL_INSET_X = 6
CELL_INSET_Y = 4
MIN_TEXT_SIZE = 8
FONT_FILES = {
    "arial": ("arial.ttf", "arialbd.ttf"),
    "arial black": ("ariblk.ttf", "ariblk.ttf"),
    "calibri": ("calibri.ttf", "calibrib.ttf"),
    "cambria": ("cambria.ttc", "cambriab.ttf"),
    "georgia": ("georgia.ttf", "georgiab.ttf"),
    "consolas": ("consola.ttf", "consolab.ttf"),
    "times new roman": ("times.ttf", "timesbd.ttf"),
    "trebuchet ms": ("trebuc.ttf", "trebucbd.ttf"),
    "verdana": ("verdana.ttf", "verdanab.ttf"),
}
SERIF_FAMILIES = {"cambria", "georgia", "source serif 4", "times new roman", "serif"}


@lru_cache(maxsize=100)
def font(size: int, bold: bool, family: str = "Arial"):
    family = family.strip().lower()
    serif = family in SERIF_FAMILIES
    fallback = "DejaVuSansMono" if family == "consolas" else "DejaVuSerif" if serif else "DejaVuSans"
    alternate = fallback + ("-Bold" if bold else "") + ".ttf"
    windows = FONT_FILES.get(family, FONT_FILES["georgia" if serif else "arial"])[int(bold)]
    for candidate in (str(Path("C:/Windows/Fonts") / windows), alternate):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def wrapped(value, width, measure):
    result = []
    for paragraph in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}" if line else word
            if line and measure(candidate) > width:
                result.append(line)
                line = word
            else:
                line = candidate
            while line and measure(line) > width:
                stop = next((i for i in range(1, len(line) + 1) if measure(line[:i]) > width), len(line) + 1)
                count = max(1, stop - 1)
                result.append(line[:count])
                line = line[count:]
        if line or not paragraph.strip():
            result.append(line)
    return result


@dataclass(frozen=True)
class RasterText:
    face: ImageFont.FreeTypeFont
    lines: tuple[tuple[int, int, str], ...]
    fits: bool


@lru_cache(maxsize=1024)
def raster_text(value, width, height, size, bold, family="Arial", align="left",
                single_line=False, leading=TEXT_LEADING) -> RasterText:
    face = font(round(size * TEXT_SCALE), bold, family)

    def occupied(line):
        left, _, right, _ = face.getbbox(line, anchor="lt")
        return max(right, face.getlength(line)) - min(0, left)

    lines = value.split("\n") if single_line else wrapped(value, width, occupied)
    step = size * TEXT_SCALE * leading
    runs = []
    fits = len(lines) * step <= height
    for i, line in enumerate(lines):
        left, top, right, bottom = face.getbbox(line, anchor="lt")
        remaining = width - occupied(line)
        offset = {"left": 0, "center": remaining / 2, "right": remaining}[align]
        x, y = round(offset - min(0, left)), round(i * step)
        fits &= x + left >= 0 and x + right <= width and y + top >= 0 and y + bottom <= height
        runs.append((x, y, line))
    return RasterText(face, tuple(runs), fits)


@lru_cache(maxsize=100)
def pdf_face(family, bold):
    # Embed the same physical face used by preview when a TrueType file is available.
    path = getattr(font(12, bold, family), "path", None)
    if isinstance(path, (str, Path)) and Path(path).is_file():
        name = "Studio-" + Path(path).stem
        try:
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, path))
            return name
        except (OSError, TTFError):
            pass
    serif = family.strip().lower() in SERIF_FAMILIES
    return ("Times-Bold" if bold else "Times-Roman") if serif else ("Helvetica-Bold" if bold else "Helvetica")


@lru_cache(maxsize=1024)
def pdf_lines(value, width, face, size):
    return tuple(wrapped(value, width, lambda line: pdfmetrics.stringWidth(line, face, size)))


def styled_lines(element, width, measure):
    """Wrap styled words without losing their source offsets."""
    lines, line, space = [], [], []
    line_width = 0

    def width_of(runs):
        return sum(measure(text, bold) for text, bold, _ in runs)

    for token in re.finditer(r"\r\n|[\r\n]|[^\S\r\n]+|[^\s]+", element.text):
        if token[0] in {"\r", "\n", "\r\n"}:
            lines.append((line, line_width))
            line, space, line_width = [], [], 0
            continue
        runs = list(element.text_style(*token.span()))
        if token[0].isspace():
            space = [(token[0], runs[0][1], runs[0][2])]
            continue
        addition = width_of(runs)
        if line and line_width + width_of(space) + addition > width:
            lines.append((line, line_width))
            line, line_width = [], 0
        if line:
            line.extend(space)
            line_width += width_of(space)
        space = []
        if addition <= width:
            line.extend(runs)
            line_width += addition
            continue
        for text, bold, color in runs:
            for character in text:
                advance = measure(character, bold)
                if line and line_width + advance > width:
                    lines.append((line, line_width))
                    line, line_width = [], 0
                line.append((character, bold, color))
                line_width += advance
    return [*lines, (line, line_width)]


def styled_raster_layout(element, width):
    size = round(element.size * TEXT_SCALE)
    return styled_lines(element, width, lambda text, bold: font(size, bold, element.font).getlength(text))


def styled_pdf_layout(element, width):
    return styled_lines(element, width, lambda text, bold: pdfmetrics.stringWidth(
        text, pdf_face(element.font, bold), element.size))


def styled_text_fits(element, width, height):
    raster = styled_raster_layout(element, width * TEXT_SCALE)
    pdf = styled_pdf_layout(element, width)
    descent = min(pdfmetrics.getDescent(pdf_face(element.font, bold), element.size) for bold in (False, True))
    return (all(line_width <= width * TEXT_SCALE for _, line_width in raster) and
            len(raster) * element.size * TEXT_LEADING <= height and
            all(line_width <= width for _, line_width in pdf) and
            element.size + (len(pdf) - 1) * element.size * TEXT_LEADING - descent <= height)


def align_offset(align, remaining):
    return {"left": 0, "center": remaining / 2, "right": remaining}[align]


def text_fits(value, width, height, size, bold, family="Arial", align="left"):
    if not value.strip():
        return True
    if width <= 0 or height <= 0:
        return False
    if not raster_text(value, round(width * TEXT_SCALE), round(height * TEXT_SCALE), size, bold, family, align).fits:
        return False
    face = pdf_face(family, bold)
    lines = pdf_lines(value, width, face, size)
    extent = size + (len(lines) - 1) * size * TEXT_LEADING - pdfmetrics.getDescent(face, size)
    return extent <= height and all(pdfmetrics.stringWidth(line, face, size) <= width for line in lines)


def text_height(value, width, size, bold, family):
    """Return the larger required height in the preview and PDF text engines."""
    pdf_font = pdf_face(family, bold)
    pdf_rows = len(pdf_lines(value, width, pdf_font, size))
    raster_rows = len(raster_text(value, round(width * TEXT_SCALE), 10000, size, bold, family).lines)
    return max(raster_rows * size * TEXT_LEADING,
               size + (pdf_rows - 1) * size * TEXT_LEADING - pdfmetrics.getDescent(pdf_font, size))


def longest_word_width(value, size, bold, family):
    raster_font = font(round(size * TEXT_SCALE), bold, family)
    pdf_font = pdf_face(family, bold)
    return max((max(raster_font.getlength(word) / TEXT_SCALE, pdfmetrics.stringWidth(word, pdf_font, size))
                for word in value.split()), default=0)


def draw_raster_text(draw, layout, color, x=0, y=0):
    for left, top, line in layout.lines:
        draw.text((x + left, y + top), line, fill=color, font=layout.face, anchor="lt")


def physical(document: Document):
    return tuple(value * 72 for value in document.dimensions)


def fit_image(image: Image, document: Document, ratio: float) -> Image:
    width, height = document.dimensions
    normalized_ratio = ratio * height / width
    w = min(image.w, image.h * normalized_ratio)
    h = min(image.h, image.w / normalized_ratio)
    return image.model_copy(update={"x": image.x + (image.w - w) / 2, "y": image.y + (image.h - h) / 2, "w": w, "h": h})


def slide_icon(document: Document, name: str, x: float, y: float, h: float, background: str) -> Image:
    width, height = document.dimensions
    theme = document.theme
    color = theme.accent if contrast(theme.accent, background) >= 3 else theme.ink
    return Image(x=x, y=y, w=h * height / width, h=h, asset_id=icon_asset_id(name, color))


def flattened(elements, ox=0., oy=0., sx=1., sy=1.):
    for el in sorted(elements, key=lambda element: element.z):
        x, y, w, h = ox + el.x * sx, oy + el.y * sy, el.w * sx, el.h * sy
        if isinstance(el, Group):
            yield from flattened(el.elements, x, y, w, h)
        else:
            yield el, x, y, w, h


def fit_slide_text(document: Document, slide: Slide) -> list[str]:
    width, height = physical(document)
    warnings = []
    elements = list(flattened(slide.elements))
    for index, (el, x, y, w, h) in enumerate(elements):
        if not el.edited and ((isinstance(el, Text) and el.line_spacing) or (isinstance(el, Table) and el.css)):
            continue  # Chromium already measured this frame at the authored font size.
        if isinstance(el, Text):
            def fits(frame_height):
                if el.spans:
                    return styled_text_fits(el, w * width, frame_height * height)
                return text_fits(el.text, w * width, frame_height * height, el.size, el.bold, el.font, el.align)

            warning = f"Text may overflow: {el.text[:75]}"
        elif isinstance(el, Table):
            def fits(frame_height):
                return table_text_fits(el, w * width, frame_height * height)

            warning = "Some exhibit cells need shorter text."
        else:
            continue
        while el.size > MIN_TEXT_SIZE and not fits(h):
            el.size = max(MIN_TEXT_SIZE, el.size - 1)
        if fits(h):
            continue

        # Use the room below the frame only if it stays within the same parent and clear of neighboring elements.
        scale = h / el.h
        limit = min(1.0, y + (1 - el.y) * scale)
        for _, other_x, other_y, other_w, _ in elements:
            if other_y >= y + h - 1e-8 and other_x < x + w and x < other_x + other_w:
                limit = min(limit, other_y)
        available = limit - y
        if el.rotation % 360 or available <= h or not fits(available):
            warnings.append(warning)
            continue
        low, high = h, available
        while (high - low) * height > .5:
            middle = (low + high) / 2
            if fits(middle):
                high = middle
            else:
                low = middle
        el.h = min(1 - el.y, high / scale)
        elements[index] = (el, x, y, w, high)
    return warnings


def validate_slide(document: Document, slide: Slide, assets: set[str]) -> list[str]:
    warnings = fit_slide_text(document, slide)
    boxes = []
    elements = list(flattened(slide.elements))
    for el, x, y, w, h in elements:
        if isinstance(el, Image) and el.asset_id not in assets and not bundled_icon(el.asset_id):
            warnings.append(f"Missing image: {el.alt or el.asset_id}")
        if isinstance(el, Table):
            for row in range(min(2, len(el.rows))):
                fill, ink, header = el.row_style(row)
                if contrast(ink, fill) < 4.5:
                    warnings.append("Low table header contrast." if header else "Low table cell contrast.")
        if isinstance(el, Chart) and el.palette and contrast(el.label_color, slide.background) < 4.5:
            warnings.append("Low chart label contrast.")
        if isinstance(el, Text):
            overlap = any(min(x+w, px+pw)-max(x,px) > .008 and min(y+h, py+ph)-max(y,py) > .008
                          for px, py, pw, ph in boxes)
            if overlap:
                warnings.append(f"Overlapping text box: {el.text[:52]}")
            boxes.append((x, y, w, h))
            backs = [shape.fill for shape, bx, by, bw, bh in elements
                     if isinstance(shape, Shape) and shape.z < el.z and bx <= x and by <= y and bx + bw >= x+w and by+bh >= y+h]
            colors = {el.color, *(span.color for span in el.spans if span.color)}
            if any(contrast(shade, backs[-1] if backs else slide.background) < 3 for shade in colors):
                warnings.append(f"Low text contrast: {el.text[:55]}")
    return warnings


def table_columns(table: Table, width: float) -> list[tuple[float, float]]:
    if table.css:
        return tracks(table.css.columns, width)
    count = len(table.rows[0])
    compact = table.row_rule and width < 360
    mixed = table.row_rule and count >= 4 and 0 < len(table.numeric_columns) < count
    weights = [1.5 if (compact or mixed) and column not in table.numeric_columns else 1 for column in range(count)]
    return tracks(weights, width)


def tracks(weights, length):
    total = sum(weights)
    sizes = [length * weight / total for weight in weights]
    return list(zip(accumulate(sizes, initial=0), sizes))


def table_rows(table: Table, height: float):
    return tracks(table.css.rows if table.css else [1] * len(table.rows), height)


def table_insets(table: Table, cell_width: float, row_height: float) -> tuple[float, float]:
    if table.css:
        return 6, 3  # 8px horizontal and 4px vertical cell padding on a 96ppi canvas.
    if table.row_rule:
        return min(12, cell_width * .1), min(6, row_height * .15)
    return CELL_INSET_X, CELL_INSET_Y


def table_text_fits(table: Table, width: float, height: float) -> bool:
    columns = table_columns(table, width)
    for index, (row, (_, row_height)) in enumerate(zip(table.rows, table_rows(table, height))):
        for column, (cell, (_, cell_width)) in enumerate(zip(row, columns)):
            inset_x, inset_y = table_insets(table, cell_width, row_height)
            if not text_fits(cell, cell_width - 2 * inset_x, row_height - 2 * inset_y,
                             table.size, table.cell_bold(index, column),
                             table.font, table.cell_align(column, index)):
                return False
    return True


def contrast(first, second):
    def luminance(color):
        values = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
        values = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in values]
        return .2126 * values[0] + .7152 * values[1] + .0722 * values[2]
    dark, light = sorted([luminance(first), luminance(second)])
    return (light + .05) / (dark + .05)


def chart_marks(el: Chart, w: float, h: float):
    values = [v for series in el.series for v in series.values]
    low, high = el.value_bounds()
    span = high - low or 1
    band = w / len(el.categories)
    shades = el.colors()
    if el.chart == "stacked_column":
        scale = h * .95 / (max(el.totals()) or 1)
        for i in range(len(el.categories)):
            bottom = h
            for si, series in enumerate(el.series):
                height = series.values[i] * scale
                if height:
                    yield ("rect", shades[si % len(shades)], (band * (i + .26), bottom - height, band * .48, height))
                bottom -= height
        return
    if el.chart == "pie":
        total = sum(values) or 1
        angle = 0
        for index, value in enumerate(values):
            sweep = value / total * 360
            yield ("pie", shades[index % len(shades)], (w / 2, h / 2, min(w, h) * .45, angle, sweep))
            angle += sweep
        return
    for si, series in enumerate(el.series):
        points = []
        for i, value in enumerate(series.values):
            baseline = h * high / span
            pos = h * (high - value) / span
            if el.chart == "bar":
                thickness = h / len(el.categories) * .72 / len(el.series)
                inset = w * .24 if el.show_values else 0
                zero = inset + (w - inset) * -low / span
                end = inset + (w - inset) * (value - low) / span
                y = i * h / len(el.categories) + thickness * si
                yield ("rect", el.mark_color(i, si), (min(zero, end), y, abs(end - zero), thickness * .8))
            elif el.chart == "column":
                thickness = band * .76 / len(el.series)
                yield ("rect", el.mark_color(i, si), (band * (i + .12) + thickness * si, min(baseline, pos), thickness * .85, abs(baseline - pos)))
            else:
                if el.show_values and el.highlight is None:
                    pos = h * .13 + pos * .82
                points.append((band * (i + .5), pos))
        if el.chart == "line" and points:
            yield ("line", el.context_color or shades[si % len(shades)], points)


def chart_values(element, points):
    if element.show_values and len(element.series) == 1:
        for i, (value, (x, y)) in enumerate(zip(element.series[0].values, points)):
            focus = i == element.highlight
            yield element.format_value(value), x, y, element.mark_color(i), focus


def bar_value_position(element, index, geometry, gap):
    x, y, w, h = geometry
    value = element.series[0].values[index]
    text = element.format_value(value)
    if element.chart == "bar":
        return text, x + w + gap if value >= 0 else x - gap, y + h / 2, "lm" if value >= 0 else "rm"
    return text, x + w / 2, y - gap if value >= 0 else y + h + gap, "mb" if value >= 0 else "mt"


def stacked_labels(element):
    if element.chart == "stacked_column" and element.show_values:
        for values in zip(*(series.values for series in element.series)):
            for value in values:
                if value:
                    yield element.format_value(value)


def segment_ink(color):
    return "#FFFFFF" if contrast(color, "#FFFFFF") >= 4.5 else "#000000"


def arrowheads(line, points, size):
    if line.arrows == "none":
        return
    pairs = [(a, b) for a, b in zip(points, points[1:]) if a != b]
    if not pairs:
        return
    tips = [pairs[-1]]
    if line.arrows == "both":
        a, b = pairs[0]
        tips.append((b, a))
    for start, end in tips:
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = hypot(dx, dy)
        ux, uy = dx / length, dy / length
        depth = min(size, length / (2 if line.arrows == "both" else 1))
        base_x, base_y = end[0] - depth * ux, end[1] - depth * uy
        yield (end, (base_x - uy * depth * .45, base_y + ux * depth * .45),
               (base_x + uy * depth * .45, base_y - ux * depth * .45))


def dashed_segments(points, scale):
    for (x, y), (ex, ey) in zip(points, points[1:]):
        dx, dy = ex - x, ey - y
        length = hypot(dx, dy)
        start = 0
        while start < length:
            end = min(start + 5 * scale, length)
            yield [(x + dx * offset / length, y + dy * offset / length) for offset in (start, end)]
            start += 8 * scale


def connector_points(line, x, y, width, height, nodes):
    start, end = line.endpoints(width, height)
    points = [(x + start[0], y + start[1]), (x + end[0], y + end[1])]
    for index, anchor in enumerate((line.start_anchor, line.end_anchor)):
        if not anchor or anchor.shape_id not in nodes:
            continue
        nx, ny, nw, nh = nodes[anchor.shape_id]
        dx, dy = {"top": (.5, 0), "left": (0, .5), "bottom": (.5, 1), "right": (1, .5)}[anchor.side]
        points[index] = nx + nw * dx, ny + nh * dy
    if line.route == "elbow":
        a, b = points
        middle = (a[0] + b[0]) / 2
        return [a, (middle, a[1]), (middle, b[1]), b]
    return points


def preview(document: Document, slide: Slide, get_asset, pixel_width=1000, *, artwork=None) -> bytes:
    from app.exhibit_evidence import display_evidence

    slide = display_evidence(document, slide)
    ptw, pth = physical(document)
    scale = pixel_width / ptw
    bounds = pixel_width, round(pth * scale)
    image = PILImage.new("RGBA", bounds, slide.background)
    if artwork is not None:
        with PILImage.open(io.BytesIO(artwork)) as backdrop:
            image.alpha_composite(backdrop.convert("RGBA").resize(bounds, PILImage.Resampling.LANCZOS))
    elements = list(flattened(slide.elements))
    nodes = {item.id: geometry for item, *geometry in elements if isinstance(item, Shape)}
    for el, nx, ny, nw, nh in elements:
        x, y, w, h = round(nx * bounds[0]), round(ny * bounds[1]), max(1, round(nw * bounds[0])), max(1, round(nh * bounds[1]))
        layer = PILImage.new("RGBA", (w, h))
        draw = ImageDraw.Draw(layer)
        if isinstance(el, Text):
            width, height = round(nw * ptw * TEXT_SCALE), round(nh * pth * TEXT_SCALE)
            text_layer = PILImage.new("RGBA", (max(1, width), max(1, height)))
            ink = ImageDraw.Draw(text_layer)
            if el.spans:
                for index, (runs, used) in enumerate(styled_raster_layout(el, width)):
                    left = align_offset(el.align, width - used)
                    top = index * el.size * TEXT_SCALE * TEXT_LEADING
                    for text, bold, shade in runs:
                        face = font(round(el.size * TEXT_SCALE), bold, el.font)
                        ink.text((left, top), text, fill=shade, font=face, anchor="lt")
                        left += face.getlength(text)
            else:
                layout = raster_text(el.text, width, height, el.size, el.bold, el.font, el.align,
                                     el.single_line and not el.edited, el.line_spacing or TEXT_LEADING)
                draw_raster_text(ink, layout, el.color)
            layer = text_layer.resize((w, h), PILImage.Resampling.LANCZOS)
        elif isinstance(el, Shape):
            if el.polygon:
                draw.polygon([(px * (w - 1), py * (h - 1)) for px, py in el.polygon], fill=el.fill, outline=el.border)
            elif el.shape == "triangle":
                draw.polygon([(w/2, 0), (w-1, h-1), (0, h-1)], fill=el.fill, outline=el.border)
            elif el.shape == "diamond":
                draw.polygon([(w/2, 0), (w-1, h/2), (w/2, h-1), (0, h/2)], fill=el.fill, outline=el.border)
            elif el.shape == "ellipse":
                draw.ellipse((0, 0, w-1, h-1), fill=el.fill, outline=el.border)
            elif el.shape == "round":
                draw.rounded_rectangle((0, 0, w-1, h-1), radius=min(w, h)*.12, fill=el.fill, outline=el.border)
            else:
                draw.rectangle((0, 0, w-1, h-1), fill=el.fill, outline=el.border)
        elif isinstance(el, Line):
            if el.start_anchor or el.end_anchor or el.route == "elbow" or el.arrows != "none" or el.weight > 2:
                points = connector_points(el, nx, ny, nw, nh, nodes)
                pixels = [(round(px * bounds[0]), round(py * bounds[1])) for px, py in points]
                margin = max(1, round(max(6, el.weight * 3.5) * scale))
                x = min(px for px, _ in pixels) - margin
                y = min(py for _, py in pixels) - margin
                w = max(px for px, _ in pixels) - x + margin + 1
                h = max(py for _, py in pixels) - y + margin + 1
                layer = PILImage.new("RGBA", (w, h))
                draw = ImageDraw.Draw(layer)
                line_points = [(px - x, py - y) for px, py in pixels]
            else:
                line_points = el.endpoints(w - 1, h - 1)
            for segment in dashed_segments(line_points, scale) if el.dashed else [line_points]:
                draw.line(segment, fill=el.color, width=max(1, round(el.weight * scale)))
            for tip in arrowheads(el, line_points, max(6, el.weight * 3.5) * scale):
                draw.polygon(tip, fill=el.color)
        elif isinstance(el, Image):
            try:
                with PILImage.open(io.BytesIO(get_asset(el.asset_id))) as source:
                    layer = source.convert("RGBA").resize((w, h))
            except (OSError, ValueError):
                draw.rectangle((0, 0, w-1, h-1), outline="#BB3457", width=3)
        elif isinstance(el, Table):
            width, height = round(nw * ptw * TEXT_SCALE), round(nh * pth * TEXT_SCALE)
            table_layer = PILImage.new("RGBA", (max(1, width), max(1, height)))
            table_draw = ImageDraw.Draw(table_layer)
            columns = table_columns(el, nw * ptw)
            rows = table_rows(el, nh * pth)
            for row, (cells, (top, rh)) in enumerate(zip(el.rows, rows)):
                for col, (cell, (left, cw)) in enumerate(zip(cells, columns)):
                    fill, ink = el.cell_style(row, col)
                    inset_x, inset_y = table_insets(el, cw, rh)
                    text_width = round((cw - 2 * inset_x) * TEXT_SCALE)
                    text_height = round((rh - 2 * inset_y) * TEXT_SCALE)
                    table_draw.rectangle(tuple(round(edge * TEXT_SCALE) for edge in (left, top, left + cw, top + rh)),
                                         fill=fill, outline=None if el.row_rule else "#FFFFFF")
                    if text_width > 0 and text_height > 0 and cell.strip():
                        layout = raster_text(cell, text_width, text_height, el.size, el.cell_bold(row, col), el.font,
                                             el.cell_align(col, row))
                        tx, ty = round((left + inset_x) * TEXT_SCALE), round((top + inset_y) * TEXT_SCALE)
                        if el.row_rule:
                            extent = len(layout.lines) * el.size * TEXT_SCALE * TEXT_LEADING
                            ty += max(0, round((text_height - extent) / 2))
                        if layout.fits:
                            draw_raster_text(table_draw, layout, ink, tx, ty)
                        else:
                            cell_layer = PILImage.new("RGBA", (text_width, text_height))
                            draw_raster_text(ImageDraw.Draw(cell_layer), layout, ink)
                            table_layer.alpha_composite(cell_layer, (tx, ty))
            for row, (top, rh) in enumerate(rows):
                if rule := el.rule_after(row):
                    shade, weight = rule
                    stroke = max(1, round(weight * TEXT_SCALE))
                    rule_y = min(round((top + rh) * TEXT_SCALE), height - (stroke + 1) // 2)
                    table_draw.line((0, rule_y, width, rule_y), fill=shade, width=stroke)
            layer = table_layer.resize((w, h), PILImage.Resampling.LANCZOS)
        elif isinstance(el, Chart):
            plot_h = h * (.96 if el.chart == "bar" and el.show_values else .87)
            segment_labels = iter(stacked_labels(el))
            bar_labels = el.show_values and len(el.series) == 1 and el.chart in {"bar", "column"}
            for index, (kind, color, dims) in enumerate(chart_marks(el, w, plot_h)):
                if kind == "rect":
                    rx, ry, rw, rh = dims
                    draw.rectangle((rx, ry, rx+rw, ry+rh), fill=color)
                    if value := next(segment_labels, None):
                        face = font(max(1, round(12 * scale)), True, el.font)
                        if face.getlength(value) <= rw and rh >= 13 * scale:
                            draw.text((rx + rw / 2, ry + rh / 2), value, fill=segment_ink(color), font=face, anchor="mm")
                    if bar_labels:
                        value, px, py, anchor = bar_value_position(el, index, dims, 4 * scale)
                        draw.text((px, py), value, fill=color if index == el.highlight else el.label_color,
                                  font=font(max(1, round(12 * scale)), True, el.font), anchor=anchor)
                        if el.chart == "bar":
                            draw.text((w * .22, py), el.categories[index][:20], fill=el.label_color,
                                      font=font(max(1, round(11 * scale)), False, el.font), anchor="rm")
                elif kind == "pie":
                    cx, cy, radius, begin, sweep = dims
                    draw.pieslice((cx-radius, cy-radius, cx+radius, cy+radius), begin, begin+sweep, fill=color)
                else:
                    if len(dims) > 1:
                        draw.line(dims, fill=color, width=max(2, round(2 * scale)))
                    for value, px, py, shade, focus in chart_values(el, dims):
                        radius = (5 if focus else 3) * scale
                        draw.ellipse((px-radius, py-radius, px+radius, py+radius), fill=shade)
                        draw.text((px, py - (9 if focus else 7) * scale), value, fill=shade if focus else el.label_color,
                                  font=font(max(1, round(14 * scale)), True, el.font), anchor="mb")
            if el.chart not in {"pie", "bar"}:
                stacked = el.chart == "stacked_column"
                if stacked:
                    draw.line((0, plot_h, w, plot_h), fill=el.label_color)
                face = font(max(1, round((13 if stacked or el.show_values else 10) * scale)), stacked, el.font)
                for index, category in enumerate(el.category_labels()):
                    draw.text((w*(index+.5)/len(el.categories), plot_h+3), category if stacked else category[:20],
                              fill=el.label_color, font=face, anchor="mt")
        if el.rotation:
            layer = layer.rotate(-el.rotation, expand=True, resample=PILImage.Resampling.BICUBIC)
        if el.opacity < 1:
            layer.putalpha(layer.getchannel("A").point(lambda channel: round(channel * el.opacity)))
        image.alpha_composite(layer, (x + (w-layer.width)//2, y + (h-layer.height)//2))
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def to_pdf(document: Document, get_asset) -> bytes:
    from app.exhibit_evidence import display_evidence, evidence_notes

    buffer = io.BytesIO()
    width, height = physical(document)
    pdf = canvas.Canvas(buffer, pagesize=(width, height))
    pdf.setTitle(document.title)
    for slide in document.slides:
        slide = display_evidence(document, slide)
        pdf.setFillColor(HexColor(slide.background))
        pdf.rect(0, 0, width, height, stroke=0, fill=1)
        elements = list(flattened(slide.elements))
        nodes = {item.id: geometry for item, *geometry in elements if isinstance(item, Shape)}
        for el, nx, ny, nw, nh in elements:
            x, y, w, h = nx*width, (1-ny-nh)*height, nw*width, nh*height
            pdf.saveState()
            pdf.setFillAlpha(el.opacity)
            pdf.setStrokeAlpha(el.opacity)
            pdf.translate(x+w/2, y+h/2)
            pdf.rotate(-el.rotation)
            pdf.translate(-w/2, -h/2)
            if isinstance(el, Text):
                name = pdf_face(el.font, el.bold)
                pdf.setFillColor(HexColor(el.color))
                pdf.setFont(name, el.size)
                path = pdf.beginPath()
                path.rect(0, 0, w, h)
                pdf.clipPath(path, stroke=0)
                if el.spans:
                    for index, (runs, used) in enumerate(styled_pdf_layout(el, w)):
                        line = pdf.beginText(align_offset(el.align, w - used), h - el.size - index * el.size * TEXT_LEADING)
                        for text, bold, shade in runs:
                            line.setFont(pdf_face(el.font, bold), el.size)
                            line.setFillColor(HexColor(shade))
                            line.textOut(text)
                        pdf.drawText(line)
                else:
                    lines = el.text.split("\n") if el.single_line and not el.edited else pdf_lines(el.text, w, name, el.size)
                    for i, line in enumerate(lines):
                        fn, px = {"left": (pdf.drawString, 0), "center": (pdf.drawCentredString, w/2),
                                  "right": (pdf.drawRightString, w)}[el.align]
                        fn(px, h-el.size-i*el.size*(el.line_spacing or TEXT_LEADING), line)
            elif isinstance(el, Shape):
                pdf.setFillColor(HexColor(el.fill))
                if el.border:
                    pdf.setStrokeColor(HexColor(el.border))
                if el.polygon:
                    path = pdf.beginPath()
                    first, *rest = el.polygon
                    path.moveTo(first[0] * w, (1 - first[1]) * h)
                    for px, py in rest:
                        path.lineTo(px * w, (1 - py) * h)
                    path.close()
                    pdf.drawPath(path, stroke=int(bool(el.border)), fill=1)
                elif el.shape == "ellipse":
                    pdf.ellipse(0, 0, w, h, stroke=int(bool(el.border)), fill=1)
                elif el.shape == "round":
                    pdf.roundRect(0, 0, w, h, min(w,h)*.12, stroke=int(bool(el.border)), fill=1)
                elif el.shape in {"triangle", "diamond"}:
                    path = pdf.beginPath()
                    path.moveTo(w/2, h)
                    if el.shape == "diamond":
                        path.lineTo(w, h/2)
                        path.lineTo(w/2, 0)
                        path.lineTo(0, h/2)
                    else:
                        path.lineTo(w, 0)
                        path.lineTo(0, 0)
                    path.close()
                    pdf.drawPath(path, stroke=int(bool(el.border)), fill=1)
                else:
                    pdf.rect(0, 0, w, h, stroke=int(bool(el.border)), fill=1)
            elif isinstance(el, Image):
                pdf.drawImage(ImageReader(io.BytesIO(get_asset(el.asset_id))), 0, 0, w, h, mask="auto")
            elif isinstance(el, Line):
                pdf.setStrokeColor(HexColor(el.color))
                pdf.setLineWidth(el.weight)
                if el.dashed:
                    pdf.setDash(5, 3)
                if el.start_anchor or el.end_anchor or el.route == "elbow":
                    points = connector_points(el, nx, ny, nw, nh, nodes)
                    line_points = [((px - nx) * width, (py - ny) * height) for px, py in points]
                else:
                    line_points = el.endpoints(w, h)
                for start, end in zip(line_points, line_points[1:]):
                    pdf.line(start[0], h - start[1], end[0], h - end[1])
                pdf.setFillColor(HexColor(el.color))
                for tip in arrowheads(el, line_points, max(6, el.weight * 3.5)):
                    path = pdf.beginPath()
                    path.moveTo(tip[0][0], h - tip[0][1])
                    for px, py in tip[1:]:
                        path.lineTo(px, h - py)
                    path.close()
                    pdf.drawPath(path, stroke=0, fill=1)
            elif isinstance(el, Table):
                rows = table_rows(el, h)
                columns = table_columns(el, w)
                for row, (cells, (offset, rh)) in enumerate(zip(el.rows, rows)):
                    for col, (cell, (left, cw)) in enumerate(zip(cells, columns)):
                        fill, ink = el.cell_style(row, col)
                        top = h-offset-rh
                        inset_x, inset_y = table_insets(el, cw, rh)
                        tw, th = cw - 2 * inset_x, rh - 2 * inset_y
                        pdf.setFillColor(HexColor(fill))
                        pdf.setStrokeColor(HexColor("#FFFFFF"))
                        pdf.rect(left, top, cw, rh, stroke=int(not el.row_rule), fill=1)
                        if tw <= 0 or th <= 0 or not cell.strip():
                            continue
                        face = pdf_face(el.font, el.cell_bold(row, col))
                        lines = pdf_lines(cell, tw, face, el.size)
                        pad = inset_y
                        if el.row_rule:
                            extent = el.size + (len(lines) - 1) * el.size * TEXT_LEADING - pdfmetrics.getDescent(face, el.size)
                            pad = max(inset_y, (rh - extent) / 2)
                        pdf.saveState()
                        path = pdf.beginPath()
                        path.rect(left + inset_x, top + inset_y, tw, th)
                        pdf.clipPath(path, stroke=0)
                        pdf.setFont(face, el.size)
                        pdf.setFillColor(HexColor(ink))
                        for j, line in enumerate(lines):
                            yy = top + rh - pad - el.size - j * el.size * TEXT_LEADING
                            if el.cell_align(col, row) == "center":
                                pdf.drawCentredString(left + cw / 2, yy, line)
                            elif el.cell_align(col, row) == "right":
                                pdf.drawRightString(left + cw - inset_x, yy, line)
                            else:
                                pdf.drawString(left + inset_x, yy, line)
                        pdf.restoreState()
                for row, (offset, rh) in enumerate(rows):
                    if rule := el.rule_after(row):
                        shade, weight = rule
                        rule_y = max(weight / 2, h - offset - rh)
                        pdf.setStrokeColor(HexColor(shade))
                        pdf.setLineWidth(weight)
                        pdf.line(0, rule_y, w, rule_y)
            elif isinstance(el, Chart):
                plot_h = h * (.96 if el.chart == "bar" and el.show_values else .87)
                segment_labels = iter(stacked_labels(el))
                bar_labels = el.show_values and len(el.series) == 1 and el.chart in {"bar", "column"}
                for index, (kind, color, dims) in enumerate(chart_marks(el, w, plot_h)):
                    pdf.setFillColor(HexColor(color))
                    pdf.setStrokeColor(HexColor(color))
                    if kind == "rect":
                        rx, ry, rw, rh = dims
                        pdf.rect(rx, h-ry-rh, rw, rh, stroke=0, fill=1)
                        if value := next(segment_labels, None):
                            face = pdf_face(el.font, True)
                            if pdfmetrics.stringWidth(value, face, 12) <= rw and rh >= 13:
                                pdf.setFont(face, 12)
                                pdf.setFillColor(HexColor(segment_ink(color)))
                                pdf.drawCentredString(rx + rw / 2, h - ry - rh / 2 - 4, value)
                        if bar_labels:
                            value, px, py, anchor = bar_value_position(el, index, dims, 4)
                            pdf.setFont(pdf_face(el.font, True), 12)
                            pdf.setFillColor(HexColor(color if index == el.highlight else el.label_color))
                            if anchor == "lm":
                                pdf.drawString(px, h - py - 4, value)
                            elif anchor == "rm":
                                pdf.drawRightString(px, h - py - 4, value)
                            else:
                                pdf.drawCentredString(px, h - py - (10 if anchor == "mt" else 0), value)
                            if el.chart == "bar":
                                pdf.setFont(pdf_face(el.font, False), 11)
                                pdf.setFillColor(HexColor(el.label_color))
                                pdf.drawRightString(w * .22, h - py - 4, el.categories[index][:20])
                    elif kind == "pie":
                        cx, cy, radius, begin, sweep = dims
                        pdf.wedge(cx-radius, h-cy-radius, cx+radius, h-cy+radius, -begin, -sweep, stroke=0, fill=1)
                    else:
                        pdf.setLineWidth(2 if el.show_values else 1)
                        for (x1, y1), (x2, y2) in zip(dims, dims[1:]):
                            pdf.line(x1, h-y1, x2, h-y2)
                        for value, px, py, shade, focus in chart_values(el, dims):
                            pdf.setFillColor(HexColor(shade))
                            pdf.circle(px, h-py, 5 if focus else 3, stroke=0, fill=1)
                            pdf.setFillColor(HexColor(shade if focus else el.label_color))
                            pdf.setFont(pdf_face(el.font, True), 14)
                            pdf.drawCentredString(px, h-py+(10 if focus else 8), value)
                if el.chart not in {"pie", "bar"}:
                    stacked = el.chart == "stacked_column"
                    if stacked:
                        pdf.setStrokeColor(HexColor(el.label_color))
                        pdf.line(0, h - plot_h, w, h - plot_h)
                    pdf.setFont(pdf_face(el.font, stacked), 13 if stacked or el.show_values else 10)
                    pdf.setFillColor(HexColor(el.label_color))
                    for i, category in enumerate(el.category_labels()):
                        pdf.drawCentredString(w*(i+.5)/len(el.categories), 3, category if stacked else category[:20])
            pdf.restoreState()
        if lineage := evidence_notes(document, slide):
            pdf.textAnnotation(lineage, Rect=(.015 * width, .01 * height, .048 * width, .043 * height))
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()
