import io
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas
from app.chart_key import axis_lines, chart_layout, key_lines, pie_label
from app.domain import Chart, Document, Image, Line, Shape, Table, Text
from app.render_geometry import image_placement, pdf_face, pdf_lines, styled_pdf_layout, align_offset, physical, flattened, table_columns, table_rows, table_insets, chart_marks, chart_values, bar_value_position, stacked_labels, segment_ink, arrowheads, connector_points, TEXT_LEADING


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
                    pdf.roundRect(0, 0, w, h, (min(w,h)*.12 if el.radius_pt is None else min(el.radius_pt, min(w,h)/2)), stroke=int(bool(el.border)), fill=1)
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
                source = ImageReader(io.BytesIO(get_asset(el.asset_id)))
                (dx, dy, dw, dh), (left, top, right, bottom) = image_placement(el, *source.getSize(), w, h)
                iw, ih = dw / (1-left-right), dh / (1-top-bottom)
                path = pdf.beginPath()
                path.rect(0, 0, w, h)
                pdf.clipPath(path, stroke=0)
                pdf.drawImage(source, dx-left*iw, h-dy-dh-bottom*ih, iw, ih, mask="auto")
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
                layout = chart_layout(el, w, h)
                plot_w, plot_h = layout.plot_width, layout.plot_height
                for cell in key_lines(el, layout):
                    cx, cy = cell.x, h-cell.y
                    if cell.swatch:
                        pdf.setFillColor(HexColor(cell.swatch))
                        pdf.rect(cx, cy-3, 6, 6, stroke=0, fill=1)
                        cx += 9
                    pdf.setFillColor(HexColor(el.label_color))
                    pdf.setFont(pdf_face(el.font, cell.bold), cell.size)
                    pdf.drawString(cx, cy-cell.size*.32, cell.text)
                segment_labels = iter(stacked_labels(el))
                bar_labels = el.show_values and len(el.series) == 1 and el.chart in {"bar", "column"}
                for index, (kind, color, dims) in enumerate(chart_marks(el, plot_w, plot_h, bar_inset=layout.bar_inset)):
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
                    elif kind == "pie":
                        cx, cy, radius, begin, sweep = dims
                        if sweep:
                            pdf.setStrokeColor(HexColor(slide.background))
                            pdf.setLineWidth(1.3)
                            pdf.wedge(cx-radius, h-cy-radius, cx+radius, h-cy+radius, -begin, -sweep, stroke=1, fill=1)
                            px, py = pie_label(dims)
                            pdf.setFont(pdf_face(el.font, True), 12)
                            pdf.setFillColor(HexColor(segment_ink(color)))
                            pdf.drawCentredString(px, h-py-4, str(index+1))
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
                stacked = el.chart == "stacked_column"
                if stacked:
                    pdf.setStrokeColor(HexColor(el.label_color))
                    pdf.line(0, h - plot_h, plot_w, h - plot_h)
                pdf.setFont(pdf_face(el.font, stacked), layout.axis_size)
                pdf.setFillColor(HexColor(el.label_color))
                for label, lx, ly in axis_lines(el, layout):
                    draw_label = pdf.drawRightString if el.chart == "bar" else pdf.drawCentredString
                    draw_label(lx, h-ly-layout.axis_size*.32, label)
            pdf.restoreState()
        if lineage := evidence_notes(document, slide):
            pdf.textAnnotation(lineage, Rect=(.015 * width, .01 * height, .048 * width, .043 * height))
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()
