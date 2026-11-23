import os
import re
from dataclasses import dataclass
from functools import lru_cache
from itertools import accumulate
from math import hypot
from pathlib import Path
from PIL import ImageFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFError, TTFont
from app.domain import Chart, Document, Group, Image, Shape, Slide, Table, Text
from app.bundled_fonts import FONT_DIR
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


@lru_cache(maxsize=1)
def installed_faces():
    """Index actual font family names so Chromium, PDF and raster use the same face."""
    roots = [FONT_DIR, Path("C:/Windows/Fonts"), Path.home() / ".local/share/fonts", Path.home() / ".fonts",
             Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path("/Library/Fonts"),
             Path("/System/Library/Fonts"), Path.home() / "Library/Fonts"]
    if local := os.environ.get("LOCALAPPDATA"):
        roots.append(Path(local) / "Microsoft/Windows/Fonts")
    faces = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
                continue
            try:
                name, style = ImageFont.truetype(str(path), 12).getname()
            except OSError:
                continue
            style = style.casefold()
            if style in {"regular", "normal", "book", "roman", "medium", "bold"}:
                faces.setdefault((name.casefold(), style == "bold"), (name, str(path)))
    return faces


@lru_cache(maxsize=128)
def available_family(family):
    face = installed_faces().get((family.casefold(), False))
    return face[0] if face else None


@lru_cache(maxsize=128)
def fallback_family(family):
    name = family.casefold()
    if name in SERIF_FAMILIES:
        choices = ("Georgia", "Liberation Serif", "DejaVu Serif")
    elif name in {"consolas", "courier new", "dejavu sans mono", "liberation mono"}:
        choices = ("Consolas", "Liberation Mono", "DejaVu Sans Mono")
    else:
        choices = ("Arial", "Liberation Sans", "DejaVu Sans")
    return next((face for face in choices if available_family(face)), choices[-1])


@lru_cache(maxsize=100)
def font(size: int, bold: bool, family: str = "Arial"):
    family = family.strip().lower()
    faces = installed_faces()
    for name in (family, fallback_family(family).casefold()):
        face = faces.get((name, bold)) or faces.get((name, False))
        if face:
            return ImageFont.truetype(face[1], size)
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
    def backing(element, x, y, w, h):
        for shape, bx, by, bw, bh in reversed(elements):
            if isinstance(shape, Shape) and shape.z < element.z and shape.opacity == 1 and (
                    bx <= x + 1e-6 and by <= y + 1e-6 and bx+bw >= x+w-1e-6 and by+bh >= y+h-1e-6):
                return shape.fill
        return slide.background
    for el, x, y, w, h in elements:
        if isinstance(el, Image) and el.asset_id not in assets and not bundled_icon(el.asset_id):
            warnings.append(f"Missing image: {el.alt or el.asset_id}")
        if isinstance(el, Table):
            for row, cells in enumerate(el.rows):
                if any(text.strip() and contrast(*el.cell_style(row, col)) < 4.5 for col, text in enumerate(cells)):
                    warning = "Low table header contrast." if row == 0 and el.header_fill else "Low table cell contrast."
                    if warning not in warnings:
                        warnings.append(warning)
        if isinstance(el, Chart) and el.palette:
            if contrast(el.label_color, backing(el, x, y, w, h)) < 4.5:
                warnings.append("Low chart label contrast.")
            if len(el.series) > len(el.palette):
                warnings.append("The chart palette repeats across series. Use direct labels to distinguish them.")
        if isinstance(el, Text):
            overlap = any(min(x+w, px+pw)-max(x,px) > .008 and min(y+h, py+ph)-max(y,py) > .008
                          for px, py, pw, ph in boxes)
            if overlap:
                warnings.append(f"Overlapping text box: {el.text[:52]}")
            boxes.append((x, y, w, h))
            colors = {el.color, *(span.color for span in el.spans if span.color)}
            minimum = 3 if el.size >= 18 or el.bold and el.size >= 14 else 4.5
            surface = backing(el, x, y, w, h)
            if any(contrast(shade, surface) < minimum for shade in colors):
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
