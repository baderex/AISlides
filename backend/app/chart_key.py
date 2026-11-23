"""Measure the plot, category labels, and editable data key in slide points."""
from dataclasses import dataclass
from hashlib import sha256
from itertools import accumulate
from math import cos, hypot, radians, sin

from app.domain import Chart, ChartLayout
from app.render_geometry import flattened, font, physical, wrapped


@dataclass(frozen=True)
class KeyCell:
    text: str
    x: float
    y: float
    size: float
    swatch: str | None = None
    bold: bool = False


def key_data(chart, numbered):
    colors = chart.colors()
    pie = chart.chart == "pie"
    yield [("", None), *[(s.name, None if pie else colors[j % len(colors)]) for j, s in enumerate(chart.series)]]
    for i, category in enumerate(chart.categories):
        name = f"{i + 1}. {category}" if numbered else category
        yield [(name, colors[i % len(colors)] if pie else None),
               *[(chart.format_value(s.values[i]), None) for s in chart.series]]


def layout_signature(chart, width, height):
    fields = {"chart", "categories", "series", "font", "element_key", "show_values", "value_prefix", "value_suffix"}
    data = f"{width:.4f},{height:.4f},{chart.axis_size}:" + chart.model_dump_json(include=fields)
    return sha256(data.encode()).hexdigest()


def chart_layout(chart, width, height):
    layout = chart.composition
    if layout and layout.signature == layout_signature(chart, width, height):
        return layout
    return measure_chart(chart, width, height)[0]


def measure_chart(chart, width, height):
    size = 14 if len(chart.series) <= 2 and len(chart.categories) <= 6 else 12
    faces = [font(2 * size, bold, chart.font) for bold in (False, True)]
    pie = chart.chart == "pie"
    bar = chart.chart == "bar"
    stacked = chart.chart == "stacked_column"
    axis_size = chart.axis_size
    axis = font(2 * axis_size, stacked, chart.font)
    categories = chart.category_labels()
    numbered = pie
    issues = []
    # Numbered references are also available if long axis labels cannot be set legibly.
    for _ in range(2):
        matrix = list(key_data(chart, numbered)) if chart.needs_key else []
        ideal = [max(faces[i == 0].getlength(text) / 2 + (21 if swatch else 12)
                     for i, (text, swatch) in enumerate(column)) for column in zip(*matrix)]
        key_width = min(sum(ideal), width * .55)
        columns = [v * key_width / sum(ideal) for v in ideal]
        gap = min(24, .035 * width) if matrix else 0
        plot = width - key_width - gap
        inset = min(plot * .45, max(axis.getlength(s) / 2 for s in categories) + 7) if bar else 0
        available = inset - 5 if bar else plot / len(categories) - 4
        labels = [str(i + 1) for i in range(len(categories))] if numbered else categories
        lines = [wrapped(label, max(1, available) * 2, axis.getlength) for label in labels]
        if pie or numbered or not matrix or all(
            len(row) <= 3 and all(axis.getlength(word) / 2 <= available for word in label.split())
            for label, row in zip(labels, lines)
        ):
            break
        numbered = True

    heights = []
    for i, row in enumerate(matrix):
        line_count = 1
        for (text, swatch), column in zip(row, columns):
            available = column - (12 if swatch else 4)
            if text and available < size:
                issues.append("Chart labels need more space in the data key.")
            line_count = max(line_count, len(wrapped(text, max(1, available)*2, faces[i == 0].getlength)))
        heights.append(line_count * size * 1.2 + (2 if len(matrix) > 10 else 5))
    if sum(heights) > height + .01:
        issues.append("Chart labels need more height for the data key.")
    label_height = max(map(len, lines)) * axis_size * 1.2 + 5
    plot_h = height if pie else height * .96 if bar else min(height * .87, max(1, height - label_height))
    if (bar and label_height-5 > plot_h / len(categories)) or (not pie and not bar and plot_h < height * .38):
        issues.append("Chart labels need more room on the category axis.")
    if not pie and any(axis.getlength(line) / 2 > (inset - 4 if bar else plot / len(categories)) for row in lines for line in row):
        issues.append("Chart labels need more width on the category axis.")
    if pie:
        total = sum(chart.series[0].values)
        radius = .45 * min(plot, plot_h)
        face = font(24, True, chart.font)
        if not total:
            issues.append("Chart labels need positive slice values for a pie.")
        for i, value in enumerate(chart.series[0].values):
            if value:
                clearance = min(.3 * radius, .7 * radius * sin(radians(min(90, 180 * value/total))))
                if 2 * clearance < hypot(face.getlength(str(i+1)) / 2, 12) + 2:
                    issues.append("Chart labels need more room inside the pie slices. Enlarge the chart or use bars.")
                    break
    layout = ChartLayout(signature=layout_signature(chart, width, height),
                         width=width, height=height, plot_width=plot, plot_height=plot_h, bar_inset=inset,
                         axis_labels=["\n".join(row) for row in lines], axis_size=axis_size, numbered=numbered,
                         key_size=size, key_left=width-key_width, key_top=max(0, (height-sum(heights))/2),
                         columns=columns, rows=heights)
    chart.composition = layout
    return layout, list(dict.fromkeys(issues))


def refresh_chart_layouts(document):
    width, height = physical(document)
    for slide in document.slides:
        for el, _, _, w, h in flattened(slide.elements):
            if isinstance(el, Chart):
                measure_chart(el, w * width, h * height)


def key_lines(chart, layout):
    size = layout.key_size
    top = layout.key_top
    for i, (row, height) in enumerate(zip(key_data(chart, layout.numbered), layout.rows)):
        face = font(size*2, i == 0, chart.font)
        for (text, swatch), x, column in zip(row, accumulate([layout.key_left, *layout.columns]), layout.columns):
            available = max(1, column - (12 if swatch else 4))
            lines = wrapped(text, available*2, face.getlength)
            for k, line in enumerate(lines):
                cy = top + height/2 + (k-(len(lines)-1)/2)*size*1.2
                yield KeyCell(line, x + (9 if swatch and k else 0), cy, size, swatch if k == 0 else None, i == 0)
        top += height


def chart_key(chart, width, height):
    layout, issues = measure_chart(chart, width, height)
    return layout.plot_width, list(key_lines(chart, layout)), issues


def pie_label(dims):
    cx, cy, radius, begin, sweep = dims
    angle = radians(begin + sweep / 2)
    return cx + .70 * radius * cos(angle), cy + .70 * radius * sin(angle)


def axis_lines(chart, layout):
    """Yield text and its centerline anchor in points for each category."""
    if chart.chart == "pie":
        return
    count = len(layout.axis_labels)
    for i, label in enumerate(layout.axis_labels):
        lines = label.split("\n")
        for j, line in enumerate(lines):
            if chart.chart == "bar":
                x = layout.bar_inset - 4
                y = (i + .5) * layout.plot_height / count + (j - (len(lines)-1)/2) * layout.axis_size * 1.2
            else:
                x = (i + .5) * layout.plot_width / count
                y = layout.plot_height + 3 + (j + .5) * layout.axis_size * 1.2
            yield line, x, y
