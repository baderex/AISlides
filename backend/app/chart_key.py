"""Lay out the data key beside workbook marks in physical slide points."""
from dataclasses import dataclass

from app.render_geometry import font, wrapped


@dataclass(frozen=True)
class KeyCell:
    text: str
    x: float
    y: float
    size: float
    swatch: str | None = None
    bold: bool = False


def chart_key(chart, width, height):
    if not chart.needs_key:
        return width, [], []
    plot = width * .47
    left = width * .51
    key_width = width - left
    labels = key_width * .4
    column = (key_width-labels) / len(chart.series)
    size = 14 if len(chart.series) <= 2 and len(chart.categories) <= 6 else 12
    colors = chart.colors()
    pie = chart.chart == "pie"
    rows = [[("", None), *[(s.name, None if pie else colors[j % len(colors)])
                             for j, s in enumerate(chart.series)]]]
    for i, category in enumerate(chart.categories):
        rows.append([(category, colors[i % len(colors)] if pie else None),
                     *[(chart.format_value(s.values[i]), None) for s in chart.series]])
    cells = []
    top = 0
    narrow = False
    for i, row in enumerate(rows):
        face = font(size*2, i == 0, chart.font)
        wrapped_row = []
        for j, (text, swatch) in enumerate(row):
            available = (labels if j == 0 else column) - (12 if swatch else 4)
            narrow |= bool(text) and available < size
            lines = wrapped(text, max(1, available)*2, face.getlength)
            wrapped_row.append((lines, swatch))
        leading = size * 1.2
        line_count = max(len(lines) for lines, _ in wrapped_row)
        for j, (lines, swatch) in enumerate(wrapped_row):
            x = left if j == 0 else left + labels + (j-1)*column
            for k, line in enumerate(lines):
                y = top + (line_count-len(lines)+1)*leading/2 + k*leading
                cells.append(KeyCell(line, x + (9 if swatch and k else 0), y, size,
                                     swatch if k == 0 else None, i == 0))
        top += leading*line_count + 6
    inset = max(0, (height-top+6)/2)
    cells = [KeyCell(c.text, c.x, c.y+inset, c.size, c.swatch, c.bold) for c in cells]
    issues = ["Chart labels need more space. Enlarge the exhibit or simplify its categories and series."] if (
        narrow or top-6 > height) else []
    return plot, cells, issues
