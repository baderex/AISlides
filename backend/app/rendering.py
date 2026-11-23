"""Raster preview adapter and public rendering helpers."""
import io
from PIL import Image as PILImage
from PIL import ImageDraw
from app.chart_key import chart_key
from app.domain import Chart, Document, Image, Line, Shape, Slide, Table, Text
from app.pdf_render import to_pdf
from app.render_geometry import (
    font, wrapped, RasterText, raster_text, pdf_face,
    pdf_lines, styled_lines, styled_raster_layout, styled_pdf_layout, styled_text_fits,
    align_offset, text_fits, text_height, longest_word_width, draw_raster_text,
    physical, image_placement, fit_image, slide_icon, flattened, fit_slide_text,
    validate_slide, table_columns, tracks, table_rows, table_insets,
    table_text_fits, contrast, chart_marks, chart_values, bar_value_position,
    stacked_labels, segment_ink, arrowheads, dashed_segments, connector_points,
    TEXT_SCALE, TEXT_LEADING, CELL_INSET_X, CELL_INSET_Y, MIN_TEXT_SIZE,
    FONT_FILES, SERIF_FAMILIES,
)


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
                draw.rounded_rectangle((0, 0, w-1, h-1), radius=min(w, h)*.12 if el.radius_pt is None else min(el.radius_pt * scale, min(w,h)/2), fill=el.fill, outline=el.border)
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
                    (dx, dy, dw, dh), (left, top, right, bottom) = image_placement(el, *source.size, w, h)
                    box = (left * source.width, top * source.height, (1-right) * source.width, (1-bottom) * source.height)
                    picture = source.convert("RGBA").resize((max(1, round(dw)), max(1, round(dh))),
                                                            PILImage.Resampling.LANCZOS, box)
                    layer.alpha_composite(picture, (round(dx), round(dy)))
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
            plot_pt, cells, _ = chart_key(el, nw * ptw, nh * pth)
            plot_w = plot_pt * scale
            for cell in cells:
                cx, cy = cell.x * scale, cell.y * scale
                if cell.swatch:
                    draw.rectangle((cx, cy-3*scale, cx+6*scale, cy+3*scale), fill=cell.swatch)
                    cx += 9 * scale
                draw.text((cx, cy), cell.text, fill=el.label_color, anchor="lm",
                          font=font(max(1, round(cell.size * scale)), cell.bold, el.font))
            plot_h = h * (.96 if el.chart == "bar" and el.show_values else .87)
            segment_labels = iter(stacked_labels(el))
            bar_labels = el.show_values and len(el.series) == 1 and el.chart in {"bar", "column"}
            for index, (kind, color, dims) in enumerate(chart_marks(el, plot_w, plot_h)):
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
                    if el.chart == "bar" and index < len(el.categories):
                        draw.text((plot_w * .22, ry + rh/2), el.categories[index], fill=el.label_color,
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
                    draw.line((0, plot_h, plot_w, plot_h), fill=el.label_color)
                face = font(max(1, round((13 if stacked or el.show_values else 10) * scale)), stacked, el.font)
                for index, category in enumerate(el.category_labels()):
                    draw.text((plot_w*(index+.5)/len(el.categories), plot_h+3), category if stacked else category[:20],
                              fill=el.label_color, font=face, anchor="mt")
        if el.rotation:
            layer = layer.rotate(-el.rotation, expand=True, resample=PILImage.Resampling.BICUBIC)
        if el.opacity < 1:
            layer.putalpha(layer.getchannel("A").point(lambda channel: round(channel * el.opacity)))
        image.alpha_composite(layer, (x + (w-layer.width)//2, y + (h-layer.height)//2))
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "PNG", optimize=True)
    return buffer.getvalue()
