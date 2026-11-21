import io
from functools import lru_cache
from pathlib import Path

from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.domain import Chart, Document, Group, Image, Line, Shape, Slide, Table, Text


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
    for paragraph in value.splitlines() or [""]:
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
        if line or not paragraph:
            result.append(line)
    return result


def physical(document: Document):
    return tuple(value * 72 for value in document.dimensions)


def fit_image(image: Image, document: Document, ratio: float) -> Image:
    width, height = document.dimensions
    normalized_ratio = ratio * height / width
    w = min(image.w, image.h * normalized_ratio)
    h = min(image.h, image.w / normalized_ratio)
    return image.model_copy(update={"x": image.x + (image.w - w) / 2, "y": image.y + (image.h - h) / 2, "w": w, "h": h})


def flattened(elements, ox=0., oy=0., sx=1., sy=1.):
    for el in sorted(elements, key=lambda element: element.z):
        x, y, w, h = ox + el.x * sx, oy + el.y * sy, el.w * sx, el.h * sy
        if isinstance(el, Group):
            yield from flattened(el.elements, x, y, w, h)
        else:
            yield el, x, y, w, h


def validate_slide(document: Document, slide: Slide, assets: set[str]) -> list[str]:
    width, height = physical(document)
    warnings = []
    boxes = []
    for el, x, y, w, h in flattened(slide.elements):
        if isinstance(el, Image) and el.asset_id not in assets:
            warnings.append(f"Missing image: {el.alt or el.asset_id}")
        if isinstance(el, Table) and el.header_fill:
            cell_width = w * width / len(el.rows[0]) - 14
            cell_height = h * height / len(el.rows) - 8
            while not table_text_fits(el, cell_width, cell_height):
                if el.size <= 8:
                    warnings.append("Some exhibit cells need shorter text.")
                    break
                el.size = max(8, el.size - 1)
        if isinstance(el, Text):
            overlap = any(min(x+w, px+pw)-max(x,px) > .008 and min(y+h, py+ph)-max(y,py) > .008
                          for px, py, pw, ph in boxes)
            if overlap:
                warnings.append(f"Overlapping text box: {el.text[:52]}")
            boxes.append((x, y, w, h))
            while el.size > 8:
                face = font(round(el.size * 2), el.bold, el.font)
                lines = wrapped(el.text, w * width * 2, face.getlength)
                if len(lines) * el.size * 1.13 <= h * height:
                    break
                el.size = max(8, el.size - 1)
            face = font(round(el.size * 2), el.bold, el.font)
            if len(wrapped(el.text, w * width * 2, face.getlength)) * el.size * 1.13 > h * height:
                warnings.append(f"Text may overflow: {el.text[:75]}")
            backs = [shape.fill for shape, bx, by, bw, bh in flattened(slide.elements)
                     if isinstance(shape, Shape) and shape.z < el.z and bx <= x and by <= y and bx + bw >= x+w and by+bh >= y+h]
            if contrast(el.color, backs[-1] if backs else slide.background) < 3:
                warnings.append(f"Low text contrast: {el.text[:55]}")
    return warnings


def table_text_fits(table: Table, width: float, height: float) -> bool:
    for index, row in enumerate(table.rows):
        _, _, header = table.row_style(index)
        face = font(round(table.size * 2), header)
        for cell in row:
            if len(wrapped(cell, width * 2, face.getlength)) * table.size * 1.13 > height:
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
    shades = [el.color, "#8B72DA", "#279990", "#BF693F", "#637B92"]
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
                yield ("rect", shades[si], (min(zero, end), i * h / len(el.categories) + thickness * si, abs(end - zero), thickness * .8))
            elif el.chart == "column":
                thickness = band * .76 / len(el.series)
                yield ("rect", shades[si], (band * (i + .12) + thickness * si, min(baseline, pos), thickness * .85, abs(baseline - pos)))
            else:
                points.append((band * (i + .5), pos))
        if el.chart == "line" and points:
            yield ("line", shades[si], points)


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
            face = font(max(1, round(el.size * scale)), el.bold, el.font)
            step = el.size * scale * 1.13
            for i, line in enumerate(wrapped(el.text, w, face.getlength)):
                offset = {"left": 0, "center": (w-face.getlength(line))/2, "right": w-face.getlength(line)}[el.align]
                draw.text((offset, i * step), line, fill=el.color, font=face, anchor="lt")
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
            rh, cw = h/len(el.rows), w/len(el.rows[0])
            size = el.size * scale
            for row, cells in enumerate(el.rows):
                fill, ink, header = el.row_style(row)
                face = font(max(1, round(size)), header)
                for col, cell in enumerate(cells):
                    left, top = col*cw, row*rh
                    draw.rectangle((left, top, left+cw, top+rh), fill=fill, outline="#FFFFFF")
                    for j, line in enumerate(wrapped(cell, cw-10, face.getlength)):
                        offset = 5 + j*size*1.12
                        if offset + size > rh:
                            break
                        draw.text((left+5, top+offset), line, fill=ink, font=face, anchor="lt")
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
                face = font(max(1, round(10 * scale)), False)
                for index, category in enumerate(el.categories):
                    draw.text((w*(index+.5)/len(el.categories), plot_h+3), category[:20], fill="#292C49", font=face, anchor="mt")
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
                name = ("Times-Bold" if el.bold else "Times-Roman") if el.font.lower() == "georgia" else ("Helvetica-Bold" if el.bold else "Helvetica")
                pdf.setFillColor(HexColor(el.color))
                pdf.setFont(name, el.size)
                path = pdf.beginPath()
                path.rect(0, 0, w, h)
                pdf.clipPath(path, stroke=0)
                for i, line in enumerate(wrapped(el.text, w, lambda line: pdf.stringWidth(line, name, el.size))):
                    fn, px = {"left": (pdf.drawString, 0), "center": (pdf.drawCentredString, w/2), "right": (pdf.drawRightString, w)}[el.align]
                    fn(px, h-el.size-i*el.size*1.13, line)
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
                rh, cw = h/len(el.rows), w/len(el.rows[0])
                for row, cells in enumerate(el.rows):
                    fill, ink, header = el.row_style(row)
                    face = "Helvetica-Bold" if header else "Helvetica"
                    pdf.setFont(face, el.size)
                    for col, cell in enumerate(cells):
                        top, left = h-(row+1)*rh, col*cw
                        pdf.setFillColor(HexColor(fill))
                        pdf.setStrokeColor(HexColor("#FFFFFF"))
                        pdf.rect(left, top, cw, rh, stroke=1, fill=1)
                        pdf.setFillColor(HexColor(ink))
                        for j, line in enumerate(wrapped(cell, cw-8, lambda line: pdf.stringWidth(line, face, el.size))):
                            yy = top+rh-el.size-3-j*el.size*1.12
                            if yy < top + 2:
                                break
                            pdf.drawString(left+4, yy, line)
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
                    pdf.setFont("Helvetica", 10)
                    pdf.setFillColor(HexColor("#292C49"))
                    for i, category in enumerate(el.categories):
                        pdf.drawCentredString(w*(i+.5)/len(el.categories), 3, category[:20])
            pdf.restoreState()
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()
