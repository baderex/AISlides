import io
import re

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_DATA_LABEL_POSITION, XL_LEGEND_POSITION, XL_MARKER_STYLE
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE, MSO_CONNECTOR
from pptx.enum.text import MSO_AUTO_SIZE, MSO_VERTICAL_ANCHOR, PP_ALIGN
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.oxml.ns import qn
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches, Pt

from app.branding import SLOT_TYPES, layout_for_slide, native_layouts
from app.domain import BrandTemplate, Chart, Document, Group, Image, Line, Shape, Table, Text
from app.rendering import TEXT_LEADING, segment_ink, table_columns, table_insets


def color(value):
    return RGBColor.from_string(value[1:].upper())


def alpha(solid, opacity):
    if opacity < 1:
        channel = solid[0]
        entry = OxmlElement("a:alpha")
        entry.set("val", str(round(opacity * 100000)))
        channel.append(entry)


def set_table_rules(cell, bottom_rule):
    properties = cell._tc.get_or_add_tcPr()
    for position, side in enumerate(("lnL", "lnR", "lnT", "lnB", "lnTlToBr", "lnBlToTr")):
        name = f"a:{side}"
        if (existing := properties.find(qn(name))) is not None:
            properties.remove(existing)
        line = OxmlElement(name)
        if side == "lnB":
            shade, weight = bottom_rule
            line.set("w", str(Pt(weight)))
            solid = OxmlElement("a:solidFill")
            rgb = OxmlElement("a:srgbClr")
            rgb.set("val", shade[1:].upper())
            solid.append(rgb)
            line.append(solid)
        else:
            line.append(OxmlElement("a:noFill"))
        properties.insert(position, line)


def to_pptx(document: Document, get_asset, brand: BrandTemplate | None = None) -> bytes:
    presentation = Presentation(io.BytesIO(get_asset(brand.asset_id))) if brand else Presentation()
    if brand:
        layouts = native_layouts(presentation)
        remove_sample_slides(presentation)
        width, height = presentation.slide_width.inches, presentation.slide_height.inches
    else:
        width, height = document.dimensions
        presentation.slide_width = Inches(width)
        presentation.slide_height = Inches(height)
    presentation.core_properties.title = document.title

    def draw(shapes, elements, ox=0., oy=0., sx=1., sy=1., placeholders=None):
        nodes, connectors = {}, []
        for el in sorted(elements, key=lambda element: element.z):
            nx, ny, nw, nh = ox + el.x*sx, oy + el.y*sy, el.w*sx, el.h*sy
            x, y, w, h = Inches(nx*width), Inches(ny*height), Inches(nw*width), Inches(nh*height)
            slot = placeholders.get(el.placeholder_idx) if placeholders is not None else None
            slot_kind = SLOT_TYPES.get(slot.placeholder_format.type) if slot is not None else None
            filled = False
            if isinstance(el, Group):
                obj = shapes.add_group_shape()
                draw(obj.shapes, el.elements, nx, ny, nw, nh)
            elif isinstance(el, Text):
                filled = slot_kind in {"title", "subtitle", "body"}
                obj = slot if filled else shapes.add_textbox(x, y, w, h)
                frame = obj.text_frame
                frame.text = el.text
                frame.word_wrap = True
                frame.auto_size = MSO_AUTO_SIZE.NONE
                if not filled:
                    frame.margin_top = frame.margin_bottom = frame.margin_left = frame.margin_right = 0
                lines = re.split(r"\n", el.text) if el.spans else ()
                offset = 0
                for index, paragraph in enumerate(frame.paragraphs):
                    if el.spans:
                        line = lines[index]
                        paragraph.clear()
                        for text, bold, shade in el.text_style(offset, offset + len(line)):
                            run = paragraph.add_run()
                            run.text = text
                            run.font.bold = bold
                            run.font.color.rgb = color(shade)
                            alpha(run.font._element.solidFill, el.opacity)
                        offset += len(line) + 1
                    paragraph.alignment = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT}[el.align]
                    paragraph.line_spacing = TEXT_LEADING
                    paragraph.space_before = paragraph.space_after = Pt(0)
                    paragraph.font.name = el.font
                    paragraph.font.size = Pt(el.size)
                    paragraph.font.bold = el.bold
                    paragraph.font.color.rgb = color(el.color)
                    alpha(paragraph.font._element.solidFill, el.opacity)
            elif isinstance(el, Shape):
                style = {"rect": MSO_AUTO_SHAPE_TYPE.RECTANGLE, "round": MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE,
                         "ellipse": MSO_AUTO_SHAPE_TYPE.OVAL, "triangle": MSO_AUTO_SHAPE_TYPE.ISOSCELES_TRIANGLE,
                         "diamond": MSO_AUTO_SHAPE_TYPE.DIAMOND}[el.shape]
                obj = shapes.add_shape(style, x, y, w, h)
                obj.fill.solid()
                obj.fill.fore_color.rgb = color(el.fill)
                alpha(obj._element.spPr.solidFill, el.opacity)
                effect = obj._element.find(".//" + qn("a:effectRef"))
                if effect is not None:
                    effect.set("idx", "0")
                if el.shape == "round":
                    obj.adjustments[0] = .12
            elif isinstance(el, Line):
                start, end = el.endpoints(w, h)
                kind = MSO_CONNECTOR.ELBOW if el.route == "elbow" else MSO_CONNECTOR.STRAIGHT
                obj = shapes.add_connector(kind, round(x + start[0]), round(y + start[1]),
                                           round(x + end[0]), round(y + end[1]))
                connectors.append((obj, el.start_anchor, el.end_anchor))
                obj.line.width = Pt(el.weight)
                obj.line.color.rgb = color(el.color)
                alpha(obj._element.spPr.ln.solidFill, el.opacity)
                if el.arrows != "none":
                    for side in ("headEnd", "tailEnd") if el.arrows == "both" else ("tailEnd",):
                        arrow = OxmlElement(f"a:{side}")
                        arrow.set("type", "triangle")
                        obj._element.spPr.ln.append(arrow)
            elif isinstance(el, Image):
                raw_image = io.BytesIO(get_asset(el.asset_id))
                filled = slot_kind == "picture"
                obj = slot.insert_picture(raw_image) if filled else shapes.add_picture(raw_image, x, y, w, h)
                if filled:
                    obj.crop_top = obj.crop_bottom = obj.crop_left = obj.crop_right = 0
                obj._element.nvPicPr.cNvPr.set("descr", el.alt)
            elif isinstance(el, Table):
                filled = slot_kind == "table"
                row_count, columns = len(el.rows), len(el.rows[0])
                obj = slot.insert_table(row_count, columns) if filled else shapes.add_table(row_count, columns, x, y, w, h)
                if filled:
                    for row in obj.table.rows:
                        row.height = h // row_count
                column_widths = [Pt(cw) for _, cw in table_columns(el, w.pt)]
                if el.row_rule:
                    for j, column in enumerate(obj.table.columns):
                        column.width = column_widths[j] if j < columns - 1 else w - sum(column_widths[:-1])
                insets = [table_insets(el, cw.pt, h.pt / row_count) for cw in column_widths]
                for i, row in enumerate(el.rows):
                    rule = el.rule_after(i)
                    for j, value in enumerate(row):
                        fill, ink = el.cell_style(i, j)
                        cell = obj.table.cell(i, j)
                        inset_x, inset_y = insets[j]
                        cell.text = value
                        cell.margin_left = cell.margin_right = Pt(inset_x)
                        cell.margin_top = cell.margin_bottom = Pt(inset_y)
                        cell.text_frame.word_wrap = True
                        cell.fill.solid()
                        cell.fill.fore_color.rgb = color(fill)
                        if rule:
                            set_table_rules(cell, rule)
                            cell.vertical_anchor = MSO_VERTICAL_ANCHOR.MIDDLE
                        for paragraph in cell.text_frame.paragraphs:
                            if rule:
                                paragraph.alignment = PP_ALIGN.RIGHT if el.cell_align(j) == "right" else PP_ALIGN.LEFT
                            paragraph.font.name = el.font
                            paragraph.font.size = Pt(el.size)
                            paragraph.font.color.rgb = color(ink)
                            paragraph.font.bold = el.cell_bold(i, j)
                            paragraph.line_spacing = TEXT_LEADING
                            paragraph.space_before = paragraph.space_after = Pt(0)
            elif isinstance(el, Chart):
                chart_data = CategoryChartData()
                chart_data.categories = el.category_labels()
                for series in el.series:
                    chart_data.add_series(series.name, series.values)
                kind = {"column": XL_CHART_TYPE.COLUMN_CLUSTERED, "stacked_column": XL_CHART_TYPE.COLUMN_STACKED,
                        "bar": XL_CHART_TYPE.BAR_CLUSTERED,
                        "line": XL_CHART_TYPE.LINE, "pie": XL_CHART_TYPE.PIE}[el.chart]
                filled = slot_kind == "chart"
                obj = slot.insert_chart(kind, chart_data) if filled else shapes.add_chart(kind, x, y, w, h, chart_data)
                obj.chart.has_legend = (len(el.series) > 1 and not (el.chart == "stacked_column" and el.show_values)
                                        or el.chart == "pie")
                if el.palette or el.show_values or el.highlight is not None or el.chart == "stacked_column":
                    style_native_chart(obj.chart, el)
                elif el.chart not in {"line", "pie"}:
                    obj.chart.series[0].format.fill.solid()
                    obj.chart.series[0].format.fill.fore_color.rgb = color(el.color)
            if filled:
                placeholders.pop(el.placeholder_idx)
                obj.left, obj.top, obj.width, obj.height = x, y, w, h
                shapes._spTree.insert_element_before(obj._element, "p:extLst")
            if not isinstance(el, (Chart, Table)):
                obj.rotation = el.rotation
            if isinstance(el, (Shape, Text, Image)):
                if el.border:
                    obj.line.color.rgb = color(el.border)
                else:
                    obj.line.fill.background()
            obj.name = el.id
            if isinstance(el, Shape) and el.shape in {"rect", "diamond"}:
                nodes[el.id] = obj
        ports = {"top": 0, "left": 1, "bottom": 2, "right": 3}
        for connector, start, end in connectors:
            if start and start.shape_id in nodes:
                connector.begin_connect(nodes[start.shape_id], ports[start.side])
            if end and end.shape_id in nodes:
                connector.end_connect(nodes[end.shape_id], ports[end.side])

    sources = {source.id: source for source in document.sources}
    for item in document.slides:
        placeholders = None
        if brand:
            selected = layout_for_slide(brand, item)
            slide = presentation.slides.add_slide(layouts[selected.index])
            placeholders = {p.placeholder_format.idx: p for p in slide.placeholders}
        else:
            slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        if not brand or item.background != selected.background:
            slide.background.fill.solid()
            slide.background.fill.fore_color.rgb = color(item.background)
        draw(slide.shapes, item.elements, placeholders=placeholders)
        if brand:
            for placeholder in placeholders.values():
                placeholder._element.getparent().remove(placeholder._element)
        if item.notes or item.source_ids:
            references = [f"{source.title} — {source.url}" if source.url else source.title
                          for source in (sources[key] for key in item.source_ids)]
            slide.notes_slide.notes_text_frame.text = "\n\n".join(filter(None, [item.notes, "\n".join(references)]))
    output = io.BytesIO()
    presentation.save(output)
    return output.getvalue()


def style_native_chart(chart, element):
    palette = element.colors()
    chart.element.chart.get_or_add_autoTitleDeleted().val = True
    chart.font.name = element.font
    chart.font.color.rgb = color(element.label_color)
    chart.font.size = Pt(10)
    # A transparent chart area keeps axis labels on the same surface as the editor.
    properties = OxmlElement("c:spPr")
    properties.append(OxmlElement("a:noFill"))
    chart.element.insert_element_before(properties, "c:txPr", "c:externalData", "c:printSettings")
    marks = chart.series[0].points if element.chart == "pie" else chart.series
    for index, mark in enumerate(marks):
        shade = color(palette[index % len(palette)])
        if element.chart == "line":
            line_ink = color(element.context_color) if element.context_color else shade
            mark.format.line.color.rgb = line_ink
            if element.show_values:
                mark.format.line.width = Pt(2)
                mark.marker.style = XL_MARKER_STYLE.CIRCLE
                mark.marker.size = 6
                mark.marker.format.fill.solid()
                mark.marker.format.fill.fore_color.rgb = line_ink
                mark.marker.format.line.color.rgb = line_ink
        else:
            mark.format.fill.solid()
            mark.format.fill.fore_color.rgb = shade
    if element.highlight is not None:
        for index, point in enumerate(chart.series[0].points):
            shade = color(element.mark_color(index))
            if element.chart == "line":
                point.marker.style = XL_MARKER_STYLE.CIRCLE
                point.marker.size = 10 if index == element.highlight else 6
                point.marker.format.fill.solid()
                point.marker.format.fill.fore_color.rgb = shade
                point.marker.format.line.color.rgb = shade
            else:
                point.format.fill.solid()
                point.format.fill.fore_color.rgb = shade
    if element.chart != "pie":
        chart.value_axis.has_major_gridlines = False
        for axis in (chart.category_axis, chart.value_axis):
            axis.tick_labels.font.name = element.font
            axis.tick_labels.font.color.rgb = color(element.label_color)
            axis.tick_labels.font.size = Pt(13 if element.show_values else 10)
    if element.show_values and element.chart in {"line", "bar", "column"} and len(element.series) == 1:
        if element.chart == "line" and element.highlight is not None:
            low, high, step = element.focused_line_axis()
            chart.value_axis.minimum_scale = low
            chart.value_axis.maximum_scale = high
            chart.value_axis.major_unit = step
        elif element.chart != "line":
            low, high = element.value_bounds()
            if low < high:
                chart.value_axis.minimum_scale = low
                chart.value_axis.maximum_scale = high
        plot = chart.plots[0]
        plot.has_data_labels = True
        labels = plot.data_labels
        labels.show_value = True
        labels.position = XL_DATA_LABEL_POSITION.ABOVE if element.chart == "line" else XL_DATA_LABEL_POSITION.OUTSIDE_END
        if element.value_prefix or element.highlight is not None or element.chart != "line":
            prefix = element.value_prefix.replace('"', '""')
            decimals = ".00" if any(value % 1 for value in element.series[0].values) else ""
            labels.number_format = (f'"{prefix}"' if prefix else "") + f"#,##0{decimals}"
        labels.font.name = element.font
        labels.font.size = Pt(14)
        labels.font.bold = True
        labels.font.color.rgb = color(element.label_color)
    if element.chart == "stacked_column":
        chart.value_axis.minimum_scale = 0
        if chart.has_legend:
            chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        if element.show_values:
            plot = chart.plots[0]
            plot.has_data_labels = True
            labels = plot.data_labels
            labels.show_value = True
            labels.position = XL_DATA_LABEL_POSITION.CENTER
            decimals = ".00" if any(val % 1 for series in element.series for val in series.values) else ""
            prefix = element.value_prefix.replace('"', '""')
            labels.number_format = f'"{prefix}"#,##0{decimals}' if prefix else f"#,##0{decimals}"
            labels.font.name = element.font
            labels.font.size = Pt(12)
            labels.font.bold = True
            for index, series in enumerate(chart.series):
                ink = color(segment_ink(palette[index % len(palette)]))
                for point in series.points:
                    point.data_label.font.color.rgb = ink


def remove_sample_slides(presentation):
    root = presentation._element
    slide_ids = root.get_or_add_sldIdLst()
    for entry in list(slide_ids):
        slide_ids.remove(entry)
    for entry in list(root):
        if entry.tag in {qn("p:custShowLst"), qn("p:extLst")}:
            root.remove(entry)
    for rel in list(presentation.part.rels.values()):
        if rel.reltype in {RT.SLIDE, RT.VIEW_PROPS, RT.CUSTOM_XML}:
            presentation.part.drop_rel(rel.rId)
    package = presentation.part.package
    for rel in list(package._rels.values()):
        if rel.reltype in {RT.THUMBNAIL, RT.CUSTOM_PROPERTIES, RT.EXTENDED_PROPERTIES}:
            package.drop_rel(rel.rId)
    for attr in ("author", "category", "comments", "keywords", "last_modified_by", "subject"):
        setattr(presentation.core_properties, attr, "")
