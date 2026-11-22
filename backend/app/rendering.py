import io
from dataclasses import dataclass
from functools import lru_cache
from itertools import accumulate
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


@lru_cache(maxsize=100)
def font(size: int, bold: bool, family: str = "Arial"):
    serif = family.lower() in {"georgia", "times new roman", "serif"}
    alternate = ("DejaVuSerif" if serif else "DejaVuSans") + ("-Bold" if bold else "") + ".ttf"
    windows = ("georgiab.ttf" if bold else "georgia.ttf") if serif else ("arialbd.ttf" if bold else "arial.ttf")
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
def raster_text(value, width, height, size, bold, family="Arial", align="left") -> RasterText:
    face = font(round(size * TEXT_SCALE), bold, family)

    def occupied(line):
        left, _, right, _ = face.getbbox(line, anchor="lt")
        return max(right, face.getlength(line)) - min(0, left)

    lines = wrapped(value, width, occupied)
    step = size * TEXT_SCALE * TEXT_LEADING
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
    serif = family.lower() in {"georgia", "times new roman", "serif"}
    return ("Times-Bold" if bold else "Times-Roman") if serif else ("Helvetica-Bold" if bold else "Helvetica")


@lru_cache(maxsize=1024)
def pdf_lines(value, width, face, size):
    return tuple(wrapped(value, width, lambda line: pdfmetrics.stringWidth(line, face, size)))


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
        if isinstance(el, Text):
            def fits(frame_height):
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
            if contrast(el.color, backs[-1] if backs else slide.background) < 3:
                warnings.append(f"Low text contrast: {el.text[:55]}")
    return warnings


def table_columns(table: Table, width: float) -> list[tuple[float, float]]:
    count = len(table.rows[0])
    compact = table.row_rule and width < 360
    weights = [1.4 if compact and column not in table.numeric_columns else 1 for column in range(count)]
    total = sum(weights)
    widths = [width * weight / total for weight in weights]
    return list(zip(accumulate(widths, initial=0), widths))


def table_insets(table: Table, cell_width: float, row_height: float) -> tuple[float, float]:
    if table.row_rule:
        return min(12, cell_width * .1), min(6, row_height * .15)
    return CELL_INSET_X, CELL_INSET_Y


def table_text_fits(table: Table, width: float, height: float) -> bool:
    columns = table_columns(table, width)
    row_height = height / len(table.rows)
    for index, row in enumerate(table.rows):
        for column, (cell, (_, cell_width)) in enumerate(zip(row, columns)):
            inset_x, inset_y = table_insets(table, cell_width, row_height)
            if not text_fits(cell, cell_width - 2 * inset_x, row_height - 2 * inset_y,
                             table.size, table.cell_bold(index, column),
                             table.font, table.cell_align(column)):
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
    high, low = max(0, *values), min(0, *values)
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
                zero = w * -low / span
                end = w * (value - low) / span
                yield ("rect", shades[si % len(shades)], (min(zero, end), i * h / len(el.categories) + thickness * si, abs(end - zero), thickness * .8))
            elif el.chart == "column":
                thickness = band * .76 / len(el.series)
                yield ("rect", shades[si % len(shades)], (band * (i + .12) + thickness * si, min(baseline, pos), thickness * .85, abs(baseline - pos)))
            else:
                points.append((band * (i + .5), pos))
        if el.chart == "line" and points:
            yield ("line", shades[si % len(shades)], points)


def preview(document: Document, slide: Slide, get_asset, pixel_width=1000) -> bytes:
    ptw, pth = physical(document)
    scale = pixel_width / ptw
    bounds = pixel_width, round(pth * scale)
    image = PILImage.new("RGBA", bounds, slide.background)
    for el, nx, ny, nw, nh in flattened(slide.elements):
        x, y, w, h = round(nx * bounds[0]), round(ny * bounds[1]), max(1, round(nw * bounds[0])), max(1, round(nh * bounds[1]))
        layer = PILImage.new("RGBA", (w, h))
        draw = ImageDraw.Draw(layer)
        if isinstance(el, Text):
            width, height = round(nw * ptw * TEXT_SCALE), round(nh * pth * TEXT_SCALE)
            text_layer = PILImage.new("RGBA", (max(1, width), max(1, height)))
            layout = raster_text(el.text, width, height, el.size, el.bold, el.font, el.align)
            draw_raster_text(ImageDraw.Draw(text_layer), layout, el.color)
            layer = text_layer.resize((w, h), PILImage.Resampling.LANCZOS)
        elif isinstance(el, Shape):
            if el.shape == "triangle":
                draw.polygon([(w/2, 0), (w-1, h-1), (0, h-1)], fill=el.fill, outline=el.border)
            elif el.shape == "ellipse":
                draw.ellipse((0, 0, w-1, h-1), fill=el.fill, outline=el.border)
            elif el.shape == "round":
                draw.rounded_rectangle((0, 0, w-1, h-1), radius=min(w, h)*.12, fill=el.fill, outline=el.border)
            else:
                draw.rectangle((0, 0, w-1, h-1), fill=el.fill, outline=el.border)
        elif isinstance(el, Line):
            draw.line((0, 0, w-1, h-1), fill=el.color, width=max(1, round(el.weight * scale)))
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
            rh = nh * pth / len(el.rows)
            for row, cells in enumerate(el.rows):
                fill, ink, _ = el.row_style(row)
                for col, (cell, (left, cw)) in enumerate(zip(cells, columns)):
                    top = row * rh
                    inset_x, inset_y = table_insets(el, cw, rh)
                    text_width = round((cw - 2 * inset_x) * TEXT_SCALE)
                    text_height = round((rh - 2 * inset_y) * TEXT_SCALE)
                    table_draw.rectangle(tuple(round(edge * TEXT_SCALE) for edge in (left, top, left + cw, top + rh)),
                                         fill=fill, outline=None if el.row_rule else "#FFFFFF")
                    if text_width > 0 and text_height > 0 and cell.strip():
                        layout = raster_text(cell, text_width, text_height, el.size, el.cell_bold(row, col), el.font,
                                             el.cell_align(col))
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
            for row in range(len(el.rows)):
                if rule := el.rule_after(row):
                    shade, weight = rule
                    stroke = max(1, round(weight * TEXT_SCALE))
                    rule_y = min(round((row + 1) * rh * TEXT_SCALE), height - (stroke + 1) // 2)
                    table_draw.line((0, rule_y, width, rule_y), fill=shade, width=stroke)
            layer = table_layer.resize((w, h), PILImage.Resampling.LANCZOS)
        elif isinstance(el, Chart):
            plot_h = h * .87
            for kind, color, dims in chart_marks(el, w, plot_h):
                if kind == "rect":
                    rx, ry, rw, rh = dims
                    draw.rectangle((rx, ry, rx+rw, ry+rh), fill=color)
                elif kind == "pie":
                    cx, cy, radius, begin, sweep = dims
                    draw.pieslice((cx-radius, cy-radius, cx+radius, cy+radius), begin, begin+sweep, fill=color)
                elif len(dims) > 1:
                    draw.line(dims, fill=color, width=3)
            if el.chart not in {"pie", "bar"}:
                stacked = el.chart == "stacked_column"
                if stacked:
                    draw.line((0, plot_h, w, plot_h), fill=el.label_color)
                face = font(max(1, round((13 if stacked else 10) * scale)), stacked, el.font)
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
    buffer = io.BytesIO()
    width, height = physical(document)
    pdf = canvas.Canvas(buffer, pagesize=(width, height))
    pdf.setTitle(document.title)
    for slide in document.slides:
        pdf.setFillColor(HexColor(slide.background))
        pdf.rect(0, 0, width, height, stroke=0, fill=1)
        for el, nx, ny, nw, nh in flattened(slide.elements):
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
                for i, line in enumerate(pdf_lines(el.text, w, name, el.size)):
                    fn, px = {"left": (pdf.drawString, 0), "center": (pdf.drawCentredString, w/2), "right": (pdf.drawRightString, w)}[el.align]
                    fn(px, h-el.size-i*el.size*TEXT_LEADING, line)
            elif isinstance(el, Shape):
                pdf.setFillColor(HexColor(el.fill))
                if el.border:
                    pdf.setStrokeColor(HexColor(el.border))
                if el.shape == "ellipse":
                    pdf.ellipse(0, 0, w, h, stroke=int(bool(el.border)), fill=1)
                elif el.shape == "round":
                    pdf.roundRect(0, 0, w, h, min(w,h)*.12, stroke=int(bool(el.border)), fill=1)
                elif el.shape == "triangle":
                    path = pdf.beginPath()
                    path.moveTo(w/2, h)
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
                pdf.line(0, h, w, 0)
            elif isinstance(el, Table):
                rh = h/len(el.rows)
                columns = table_columns(el, w)
                for row, cells in enumerate(el.rows):
                    fill, ink, _ = el.row_style(row)
                    for col, (cell, (left, cw)) in enumerate(zip(cells, columns)):
                        top = h-(row+1)*rh
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
                            if el.cell_align(col) == "right":
                                pdf.drawRightString(left + cw - inset_x, yy, line)
                            else:
                                pdf.drawString(left + inset_x, yy, line)
                        pdf.restoreState()
                for row in range(len(el.rows)):
                    if rule := el.rule_after(row):
                        shade, weight = rule
                        rule_y = max(weight / 2, h - (row + 1) * rh)
                        pdf.setStrokeColor(HexColor(shade))
                        pdf.setLineWidth(weight)
                        pdf.line(0, rule_y, w, rule_y)
            elif isinstance(el, Chart):
                plot_h = .87*h
                for kind, color, dims in chart_marks(el, w, plot_h):
                    pdf.setFillColor(HexColor(color))
                    pdf.setStrokeColor(HexColor(color))
                    if kind == "rect":
                        rx, ry, rw, rh = dims
                        pdf.rect(rx, h-ry-rh, rw, rh, stroke=0, fill=1)
                    elif kind == "pie":
                        cx, cy, radius, begin, sweep = dims
                        pdf.wedge(cx-radius, h-cy-radius, cx+radius, h-cy+radius, -begin, -sweep, stroke=0, fill=1)
                    else:
                        for (x1, y1), (x2, y2) in zip(dims, dims[1:]):
                            pdf.line(x1, h-y1, x2, h-y2)
                if el.chart not in {"pie", "bar"}:
                    stacked = el.chart == "stacked_column"
                    if stacked:
                        pdf.setStrokeColor(HexColor(el.label_color))
                        pdf.line(0, h - plot_h, w, h - plot_h)
                    pdf.setFont(pdf_face(el.font, stacked), 13 if stacked else 10)
                    pdf.setFillColor(HexColor(el.label_color))
                    for i, category in enumerate(el.category_labels()):
                        pdf.drawCentredString(w*(i+.5)/len(el.categories), 3, category if stacked else category[:20])
            pdf.restoreState()
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()
