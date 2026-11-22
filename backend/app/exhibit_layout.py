"""Deterministic layout for the semantic exhibit contracts."""

from collections import defaultdict, deque
from itertools import accumulate
from math import sqrt
from textwrap import shorten

from app.design import blend, readable
from app.domain import ConnectorAnchor, Line, Shape, Table, Text, TextSpan, display_value
from app.lucide import ICON_NAMES
from app.rendering import longest_word_width, physical, slide_icon, text_height
from app.visuals import (CodeVisual, FlowVisual, GraphVisual, MatrixVisual, QuantitativeVisual,
                         ScatterVisual, ScheduleVisual, SequenceVisual, SwimlaneVisual, VennVisual)


class ExhibitCanvas:
    """Map exhibit-local geometry into the slide body or a template slot."""

    def __init__(self, document, slide, bounds, *, highlight_band=None):
        self.document, self.slide = document, slide
        self.x, self.y, self.w, self.h = bounds
        self.theme = document.theme
        self.ink = readable(self.theme.ink, slide.background)
        self.muted = readable(self.theme.muted, slide.background, self.ink)
        self.accent = readable(self.theme.accent, slide.background, self.ink)
        self.surface = blend(slide.background, self.accent, .10)
        self.rule = blend(slide.background, self.ink, .24)
        palette = self.theme.design.chart_colors if self.theme.design else []
        self.palette = list(dict.fromkeys([self.accent, *palette, "#34847C", "#B46839", "#7767A9"]))[:4]
        self.ptw, self.pth = physical(document)
        self.marks = {}
        self.highlight_band = highlight_band

    def box(self, x, y, w, h):
        return dict(x=self.x + x * self.w, y=self.y + y * self.h, w=w * self.w, h=h * self.h)

    def shape(self, x, y, w, h, fill=None, *, shape="rect", border=None, opacity=1, z=-2):
        if w <= 0 or h <= 0:
            return None
        element = Shape(**self.box(x, y, w, h), fill=fill or self.surface, shape=shape,
                        border=border, opacity=opacity, z=z)
        self.slide.elements.append(element)
        return element

    def label(self, value, x, y, w, h, size=15, *, color=None, bold=False, align="left", font=None, emphasis=(),
              centered=False):
        if not value.strip() or w <= 0 or h <= 0:
            return
        bounds = self.box(x, y, w, h)
        font = font or self.theme.font
        width, height = bounds["w"] * self.ptw, bounds["h"] * self.pth
        while size > 8 and (text_height(value, width, size, bold, font) + 3 > height or
                            longest_word_width(value, size, bold, font) > width * .99):
            size -= 1
        bounds["h"] = min(height, text_height(value, width, size, bold, font) + 3) / self.pth
        if centered:
            bounds["y"] += (h * self.h - bounds["h"]) / 2
        matches = sorted((value.index(term), value.index(term) + len(term)) for term in emphasis if term in value)
        spans = []
        for start, end in matches:
            if not spans or start >= spans[-1].end:
                spans.append(TextSpan(start=start, end=end, bold=True))
        self.slide.elements.append(Text(**bounds, text=value, size=size, font=font, color=color or self.ink,
                                        bold=bold, align=align, spans=spans))

    def line(self, a, b, *, color=None, arrow=False, weight=1.2, dashed=False, z=-1,
             route="straight", anchors=(None, None)):
        x0, y0 = a
        x1, y1 = b
        if a == b:
            return
        if x0 == x1:
            x, y, w, h = max(0, x0 - .001), min(y0, y1), .002, abs(y1 - y0)
            direction = "down" if y1 > y0 else "up"
        elif y0 == y1:
            x, y, w, h = min(x0, x1), max(0, y0 - .001), abs(x1 - x0), .002
            direction = "right" if x1 > x0 else "left"
        else:
            x, y, w, h = min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)
            direction = {(True, True): "diagonal", (True, False): "diagonal-up",
                          (False, True): "diagonal-down-left", (False, False): "diagonal-up-left"}[x1 > x0, y1 > y0]
        self.slide.elements.append(Line(**self.box(x, y, w, h), direction=direction, color=color or self.muted,
                                        arrows="end" if arrow else "none", dashed=dashed, weight=weight, z=z, route=route,
                                        start_anchor=anchors[0], end_anchor=anchors[1]))

    def mark(self, label, x, y):
        self.marks[label] = (x, y)


def fmt(value, signed=False):
    string = display_value(value)
    return f"+{string}" if signed and value > 0 else string


def scale(values, low, high):
    lower, upper = min(values), max(values)
    if lower == upper:
        return lambda _: (low + high) / 2
    return lambda value: low + (high - low) * (value - lower) / (upper - lower)


def quantitative(c, v):
    kind = v.type
    rows = v.rows
    values = [r.values[0] for r in rows]
    if kind == "waterfall":
        waterfall(c, v)
    elif kind in {"mekko", "stacked-bar"}:
        composition(c, v)
    elif kind in {"slope", "pareto"}:
        course(c, v)
    elif kind == "funnel":
        height = .91 / len(rows)
        panel = blend(c.slide.background, c.accent, .19)
        for i, row in enumerate(rows):
            y, fraction = .025 + i * height, row.values[0] / values[0]
            width = .51 * fraction
            if width:
                c.shape(.27 - width / 2, y, width, height * .84, panel, border=c.accent)
            c.label(row.label, .55, y + .01, .29, height * .72, bold=True)
            c.label(fmt(row.values[0]), .85, y + .01, .14, height * .72, align="right")
            c.mark(row.label, .27 + width / 2, y + height * .42)
    else:
        tornado = kind == "tornado"
        rows = sorted(rows, key=lambda row: (row.values[-1] - row.values[0]) if tornado else row.values[0],
                      reverse=True)
        origin = v.baseline if tornado else 0
        domain = [origin, *(value for row in rows for value in row.values)]
        x = scale(domain, .32 if tornado else .39, .89 if tornado else .91)
        c.line((x(origin), .04), (x(origin), .87), color=c.rule)
        height = .81 / len(rows)
        for i, row in enumerate(rows):
            left, right = row.values if tornado else sorted((origin, row.values[0]))
            position = x(row.values[0])
            y = .04 + i * height
            color = c.accent if not v.highlight or v.highlight == row.label else c.rule
            if row.label == c.highlight_band:
                c.shape(0, y, 1, height, c.surface, z=-4)
            if left != right:
                c.shape(x(left), y + height * .20, x(right) - x(left), height * .53, color)
            c.label(row.label, 0, y + .01, .241 if tornado else .30, height * .88, bold=True)
            if tornado:
                c.label(fmt(left), x(left) - .071, y + .01, .067, height * .88, 11, align="right")
                c.label(fmt(right), x(right) + .006, y + .01, .097, height * .88, 11)
            else:
                c.label(fmt(row.values[0]), position + .009 if row.values[0] >= 0 else position - .081,
                        y + .01, .077, height * .88, 11, align="left" if row.values[0] >= 0 else "right")
            c.mark(row.label, x(right), y + height * .46)
        if tornado:
            c.label(f"Baseline {fmt(origin)}", .32, .89, .56, .10, 11, align="center")


def waterfall(c, v):
    totals = list(accumulate((row.values[0] for row in v.rows), initial=v.baseline))
    labels = [v.start_label, *(row.label for row in v.rows), v.end_label]
    intervals = [(0, totals[0]), *zip(totals, totals[1:]), (0, totals[-1])]
    h = scale([0, *totals], .69, .16)
    c.line((0, h(0)), (1, h(0)), color=c.rule)
    step = 1 / len(labels)
    for i, (label, (start, end)) in enumerate(zip(labels, intervals)):
        x = i * step + step * .18
        width = step * .64
        top, bottom = sorted((h(start), h(end)))
        total = i == 0 or i == len(labels) - 1
        value = end if total else v.rows[i - 1].values[0]
        color = c.accent if total or value >= 0 else "#A44742"
        if bottom > top:
            c.shape(x, top, width, bottom - top, color)
        else:
            c.line((x, top), (x + width, top), color=color, weight=2)
        if i < len(labels) - 1:
            c.line((x + width, h(end)), (x + step, h(end)), color=c.muted, dashed=True)
        c.label(fmt(value, not total), i * step, top - .14, step, .12, 12, bold=True, align="center")
        c.label(label, i * step + .004, .73, step - .008, .22, 12, align="center")
        c.mark(label, x + width / 2, top)


def composition(c, v):
    rows, split = v.rows, v.type == "mekko"
    maximum = max(sum(row.values) for row in rows) or 1
    widths = [row.width if split else 1 for row in rows]
    span = sum(widths)
    x = 0
    for row, weight in zip(rows, widths):
        column = weight / span
        width = column * (.98 if split else .72)
        left = x + (column - width) / 2
        bottom = .68
        total = sum(row.values)
        for i, value in enumerate(row.values):
            height = .55 * value / (total if split else maximum)
            if height:
                fill = c.palette[i]
                c.shape(left, bottom - height, width, height, fill)
                if height >= .11 and width >= .075:
                    c.label(f"{value / total:.0%}" if split else fmt(value), left, bottom - height, width, height,
                            13, color=readable("#FFFFFF", fill), bold=True, align="center", centered=True)
            bottom -= height
        c.label(fmt(row.width if split else total), left, max(0, bottom - .105), width, .095, 11, bold=True, align="center")
        c.label(row.label, x, .70, column, .14, 11, align="center")
        c.mark(row.label, x + column / 2, bottom)
        x += column
    for i, name in enumerate(v.series):
        step = 1 / len(v.series)
        c.shape(i * step + .01, .89, .022, .035, c.palette[i])
        c.label(name, i * step + .04, .86, step - .044, .11, 11)


def course(c, v):
    if v.type == "slope":
        positions = scale([value for row in v.rows for value in row.values], .84, .19)
        labels = v.series or [v.start_label, v.end_label]
        c.label(labels[0], 0, 0, .40, .12, bold=True, align="right")
        c.label(labels[1], .60, 0, .40, .12, bold=True)
        slots = []
        for side in range(2):
            end = .13
            column = {}
            for row in sorted(enumerate(v.rows), key=lambda pair: positions(pair[1].values[side])):
                i, metric = row
                top = max(end, positions(metric.values[side]) - .043)
                column[i] = top
                end = top + .088
            bottom = .904
            for i in reversed(column):
                column[i] = min(column[i], bottom)
                bottom = column[i] - .088
            slots.append(column)
        for i, row in enumerate(v.rows):
            left, right = map(positions, row.values)
            color = c.accent if not v.highlight or row.label == v.highlight else c.muted
            c.line((.41, left), (.59, right), color=color, weight=2 if row.label == v.highlight else 1.2)
            c.line((.39, slots[0][i] + .043), (.41, left), color=color)
            c.line((.59, right), (.61, slots[1][i] + .043), color=color)
            c.label(f"{row.label}  {fmt(row.values[0])}", 0, slots[0][i], .38, .086, 12, align="right")
            c.label(f"{fmt(row.values[1])}  {row.label}", .62, slots[1][i], .38, .086, 12)
            c.mark(row.label, .59, right)
    else:
        rows = sorted(v.rows, key=lambda row: row.values[0], reverse=True)
        values = [row.values[0] for row in rows]
        step = .92 / len(rows)
        c.line((.04, .70), (.96, .70), color=c.rule)
        previous = None
        for i, (row, cumulative) in enumerate(zip(rows, accumulate(values))):
            fraction = cumulative / sum(values)
            height = .48 * values[i] / max(values)
            x = .04 + step * (i + .5)
            y = .65 - .54 * fraction
            if height:
                c.shape(x - step * .30, .70 - height, step * .60, height, blend(c.slide.background, c.accent, .55))
            if previous:
                c.line(previous, (x, y), color=c.accent, weight=2)
            c.label(f"{fraction:.0%}", x - step / 2, y - .105, step, .10, 11, bold=True, align="center")
            c.label(fmt(values[i]), x - step / 2, .71, step, .087, 11, align="center")
            c.label(row.label, x - step / 2, .80, step, .18, 11, align="center")
            c.mark(row.label, x, y)


def matrix(c, v):
    heat = v.type == "heatmap"
    weighted = bool(v.weights)
    weights = [w / sum(v.weights) for w in v.weights]
    show_key = heat or v.type == "raci"
    headers = ["", *[f"{name} ({weight:.0%})" for name, weight in zip(v.columns, weights)]] if weighted else ["", *v.columns]
    if weighted:
        headers.append("Weighted score")
    values = [value for row in v.rows for value in row.cells if isinstance(value, (float, int))]
    if v.levels:
        positions = {level: i / (len(v.levels) - 1) for i, level in enumerate(v.levels)}
        fraction = positions.__getitem__
    elif heat:
        fraction = scale(values, 0, 1)
    rows = [headers]
    fills = [[None] * len(headers)]
    inks = [[None] * len(headers)]
    low = blend(c.slide.background, c.accent, .10)
    high = blend(c.slide.background, c.accent, .93)
    for i, row in enumerate(v.rows, 1):
        selected = row.label == v.recommended
        cells = [f"{row.label} ★" if selected else row.label,
                 *[value if isinstance(value, str) else fmt(value) for value in row.cells]]
        if weighted:
            cells.append(fmt(round(sum(value * weight for value, weight in zip(row.cells, weights)), 3)))
        rows.append(cells)
        colors = [None, *[blend(low, high, fraction(value)) for value in row.cells]] if heat else [None] * len(cells)
        fills.append(colors)
        inks.append([readable(c.ink, fill) if fill else None for fill in colors])
        c.mark(row.label, .98, (i + .5) * (.84 if show_key else .98) / (len(v.rows) + 1))
    c.slide.elements.append(Table(**c.box(0, 0, 1, .84 if show_key else .98), rows=rows, fill=c.slide.background,
                                  color=c.ink, size=max(9, min(15, 96 / len(rows))), font=c.theme.font,
                                  header_fill=c.accent, header_color=readable("#FFFFFF", c.accent),
                                  focus_rows=[i for i, row in enumerate(v.rows, 1)
                                              if row.label in (v.recommended, c.highlight_band)],
                                  focus_fill=c.surface, focus_ink=c.accent, row_rule=c.rule,
                                  numeric_columns=[i for i in range(1, len(headers)) if all(
                                      isinstance(row.cells[i - 1], (int, float)) for row in v.rows
                                  )] if not weighted else list(range(1, len(headers))),
                                  cell_fills=fills if heat else [], cell_inks=inks if heat else []))
    if heat:
        if v.levels:
            step = 1 / len(v.levels)
            for i, level in enumerate(v.levels):
                color = blend(low, high, fraction(level))
                c.shape(i * step, .872, step * .96, .102, color)
                c.label(level, i * step + .006, .882, step * .94, .086, 11, color=readable(c.ink, color), align="center")
        else:
            for i in range(5):
                c.shape(.38 + .05 * i, .895, .05, .045, blend(low, high, i / 4))
            c.label(f"{v.scale_low} {fmt(min(values))}".strip(), 0, .86, .36, .13, 11, align="right")
            c.label(f"{fmt(max(values))} {v.scale_high}".strip(), .65, .86, .35, .13, 11)
    elif v.type == "raci":
        c.label("R  Responsible     A  Accountable     C  Consulted     I  Informed", 0, .86, 1, .13, 11)


def scatter(c, v):
    points = v.points
    if points[0].quadrant:
        qualitative_quadrants(c, v)
        return
    horizontal = [p.horizontal for p in points] + ([] if v.horizontal_split is None else [v.horizontal_split])
    vertical = [p.vertical for p in points] + ([] if v.vertical_split is None else [v.vertical_split])
    x, y = scale(horizontal, .19, .83), scale(vertical, .75, .21)
    c.label(v.vertical_label, .10, 0, .80, .095, 13, bold=True)
    c.label(v.horizontal_label, .12, .90, .76, .10, 13, bold=True, align="center")
    c.line((.12, .095), (.12, .83), color=c.rule)
    c.line((.12, .83), (.93, .83), color=c.rule)
    c.label(fmt(min(horizontal)), .09, .83, .14, .073, 10, align="center")
    c.label(fmt(max(horizontal)), .77, .83, .14, .073, 10, align="center")
    c.label(fmt(min(vertical)), 0, .71, .11, .09, 10, align="right")
    c.label(fmt(max(vertical)), 0, .17, .11, .09, 10, align="right")
    if v.horizontal_split is not None:
        c.line((x(v.horizontal_split), .095), (x(v.horizontal_split), .83), color=c.rule, dashed=True)
    if v.vertical_split is not None:
        c.line((.12, y(v.vertical_split)), (.93, y(v.vertical_split)), color=c.rule, dashed=True)
    for i, name in enumerate(v.quadrants):
        c.label(name, .135 if i % 2 == 0 else .69, .10 if i < 2 else .745, .22, .074, 10, color=c.muted,
                align="left" if i % 2 == 0 else "right")
    largest = max((p.size or 0 for p in points))
    occupied = []
    for point in points:
        px, py = x(point.horizontal), y(point.vertical)
        max_height = min(.17, .25 * c.w * c.ptw / (c.h * c.pth))
        height = max_height * sqrt(point.size / largest) if point.size and v.type == "bubble-chart" else .035
        width = height * c.h * c.pth / (c.w * c.ptw)
        fill = c.accent if not v.highlight or point.label == v.highlight else c.muted
        c.shape(px - width / 2, py - height / 2, width, height, fill, shape="ellipse", opacity=.78)
        label = f"{point.label} ({fmt(point.size)})" if v.type == "bubble-chart" else point.label
        candidates = [(px + width / 2 + .012, py - .042), (px - width / 2 - .202, py - .042),
                      (px - .095, py + height / 2), (px - .095, py - height / 2 - .085)]
        proposals = [(bx, by) for bx, by in candidates if .12 <= bx <= .81 and .105 <= by <= .82]
        def overlap(box):
            bx, by = box
            return sum(max(0, min(bx + .19, ox + .19) - max(bx, ox)) *
                       max(0, min(by + .085, oy + .085) - max(by, oy)) for ox, oy in occupied)
        bx, by = min(proposals or [(min(.80, max(.12, px - .095)), py - .042)], key=overlap)
        c.label(label, bx, by, .19, .085, 11, bold=True)
        occupied.append((bx, by))
        c.mark(point.label, px, py)


def qualitative_quadrants(c, v):
    names = ("upper-left", "upper-right", "lower-left", "lower-right")
    c.label(f"{v.vertical_label} ↑", .03, 0, .94, .10, 14, bold=True)
    c.label(f"{v.horizontal_label} →", .03, .90, .94, .10, 14, bold=True, align="right")
    c.line((.5, .12), (.5, .88), color=c.rule)
    c.line((.02, .50), (.98, .50), color=c.rule)
    for i, name in enumerate(names):
        x, y = .035 + .48 * (i % 2), .13 + .38 * (i // 2)
        if v.quadrants:
            c.label(v.quadrants[i], x, y, .42, .07, 13, color=c.muted, bold=True)
        labels = [point.label for point in v.points if point.quadrant == name]
        height = min(.105, .28 / max(1, len(labels)))
        for j, label in enumerate(labels):
            top = y + .068 + height * j
            fill = c.accent if label == v.highlight else c.surface
            c.shape(x, top, .42, height * .93, fill)
            c.label(label, x + .012, top, .39, height * .93, 13, color=readable(c.ink, fill),
                    bold=label == v.highlight, centered=True)
            c.mark(label, x + .42, top + height / 2)
def graph_ranks(v):
    nodes = [node.id for node in v.nodes]
    if v.type == "layered-stack":
        return dict(zip(nodes, range(len(nodes))))
    children, parents = defaultdict(list), defaultdict(list)
    for edge in v.edges:
        if edge.source != edge.target:
            children[edge.source].append(edge.target)
            parents[edge.target].append(edge.source)
    pending = {key: len(parents[key]) for key in nodes}
    ranks = {key: 0 for key in nodes if pending[key] == 0}
    queue = deque(ranks)
    seen = set()
    for key in [*ranks, *nodes]:
        if key in seen:
            continue
        if not queue:
            ranks.setdefault(key, 0)
            queue.append(key)
        while queue:
            source = queue.popleft()
            seen.add(source)
            for target in children[source]:
                if target in seen:
                    continue
                ranks[target] = max(ranks.get(target, 0), ranks[source] + 1)
                pending[target] -= 1
                if pending[target] == 0:
                    queue.append(target)
    return {key: ranks.get(key, 0) for key in nodes}


def graph(c, v):
    ranks = graph_ranks(v)
    stack = v.type == "layered-stack"
    sankey = v.type == "sankey"
    rank_count = max(ranks.values()) + 1
    vertical = stack or v.type in {"issue-tree", "driver-tree", "organization-chart"} or (rank_count > 5 and not sankey)
    groups = list(dict.fromkeys(node.group for node in v.nodes))
    boxes, local = {}, {}
    cross_start = 0
    major = .94 / rank_count
    group_sizes = {group: max(sum(n.group == group and ranks[n.id] == rank for n in v.nodes)
                             for rank in range(rank_count)) for group in groups}
    for group in groups:
        cohorts = defaultdict(list)
        for node in v.nodes:
            if node.group == group:
                cohorts[ranks[node.id]].append(node)
        cross = .95 * group_sizes[group] / sum(group_sizes.values())
        start = cross_start + .025
        strip = .062 if group else 0
        if group:
            if vertical:
                c.shape(start, 0, cross - .01, 1, c.surface, border=c.rule, z=-6)
                c.label(group, start + .01, 0, cross - .03, strip, 12, bold=True)
            else:
                c.shape(0, start, 1, cross - .01, c.surface, border=c.rule, z=-6)
                c.label(group, .013, start, .32, min(strip, cross * .3), 12, bold=True)
        for rank, nodes in cohorts.items():
            gutter = min(strip, cross * .29)
            cell = (cross if vertical else cross - gutter) / len(nodes)
            for i, node in enumerate(nodes):
                along = .03 + major * rank
                across = start + cell * i + (0 if vertical else gutter)
                if vertical:
                    x, y = across + cell * .11, along + (.057 if group else .025)
                    w, h = cell * .78, major * (.58 if group else .65)
                else:
                    x, y = along + major * .05, across + cell * .10
                    w, h = major * .72, min(.53, cell * .70)
                if sankey:
                    w = min(w, .12)
                if node.role == "decision":
                    shape, inset = "diamond", .20
                else:
                    shape = "round" if node.role in {"start", "end"} or v.type == "state-machine" else "rect"
                    inset = .06
                fill = blend(c.slide.background, c.accent, .13 if node.role != "store" else .25)
                box = c.shape(x, y, w, h, fill, border=c.accent, shape=shape)
                boxes[node.id], local[node.id] = box, (x, y, w, h)
                text_x, text_w = x + w * inset, w * (1 - 2 * inset)
                if node.icon in ICON_NAMES:
                    icon_x, icon_y = c.x + text_x * c.w, c.y + (y + h * .10) * c.h
                    icon = slide_icon(c.document, node.icon, icon_x, icon_y,
                                      min(w * c.w * c.ptw / c.pth * .19, h * c.h * .34), fill)
                    c.slide.elements.append(icon)
                    offset = icon.w / c.w * 1.13
                    text_x, text_w = text_x + offset, text_w - offset
                details = "\n".join(node.fields) if node.fields else node.detail
                c.label(node.label, text_x, y + h * .11, text_w, h * (.28 if details else .78), 15,
                        bold=True, align="center", centered=not details)
                if details:
                    c.label(details, x + w * inset, y + h * .40, w * (1 - 2 * inset), h * .55, 11,
                            align="left" if node.fields else "center")
                c.mark(node.label, x + w, y + h / 2)
        cross_start += cross
    if sankey:
        draw_weighted_links(c, v, local)
        return
    pairs = {(edge.source, edge.target) for edge in v.edges}
    order = {node.id: i for i, node in enumerate(v.nodes)}
    for edge in v.edges:
        sx, sy, sw, sh = local[edge.source]
        tx, ty, tw, th = local[edge.target]
        if edge.source == edge.target:
            start, end = (sx + sw, sy + sh / 2), (sx + sw / 2, sy + sh)
            bend_x, bend_y = min(.996, sx + sw + .029), min(.995, sy + sh + .04)
            c.line(start, (bend_x, start[1]))
            c.line((bend_x, start[1]), (bend_x, bend_y))
            c.line((bend_x, bend_y), (end[0], bend_y))
            c.line((end[0], bend_y), end, arrow=True)
            lx, ly = bend_x - .096, bend_y + .006
        else:
            same_rank = ranks[edge.source] == ranks[edge.target]
            if vertical != same_rank:
                down = ty > sy
                ports = ("bottom", "top") if down else ("top", "bottom")
                start, end = (sx + sw / 2, sy + sh if down else sy), (tx + tw / 2, ty if down else ty + th)
            else:
                right = tx > sx
                ports = ("right", "left") if right else ("left", "right")
                start, end = (sx + sw if right else sx, sy + sh / 2), (tx if right else tx + tw, ty + th / 2)
            anchors = (
                ConnectorAnchor(shape_id=boxes[edge.source].id, side=ports[0]),
                ConnectorAnchor(shape_id=boxes[edge.target].id, side=ports[1]))
            reversed_pair = (edge.target, edge.source) in pairs and order[edge.source] > order[edge.target]
            if reversed_pair:
                # Leave distinct routes for triggers in opposite directions.
                if ports[0] in {"top", "bottom"}:
                    start, end = (start[0] + sw * .26, start[1]), (end[0] + tw * .26, end[1])
                else:
                    start, end = (start[0], start[1] + sh * .26), (end[0], end[1] + th * .26)
                anchors = (None, None)
            c.line(start, end, arrow=True, z=-3, anchors=anchors)
            lx = (start[0] + end[0]) / 2 - .085
            ly = (start[1] + end[1]) / 2 + (.01 if reversed_pair else -.073)
        graph_label(c, edge.label, lx, ly)


def graph_label(c, text, x, y):
    if not text:
        return
    width, height = .17, .068
    occupied = [element for element in c.slide.elements if isinstance(element, Text)]

    def overlap(position):
        bounds = c.box(*position, width, height)
        bx, by, bw, bh = (bounds[key] for key in ("x", "y", "w", "h"))
        return sum(max(0, min(bx + bw, el.x + el.w) - max(bx, el.x)) *
                   max(0, min(by + bh, el.y + el.h) - max(by, el.y)) for el in occupied)

    candidates = [(min(1 - width, max(0, x + dx)), min(1 - height, max(0, y + dy)))
                  for dx, dy in ((0, 0), (0, -.075), (0, .085), (0, .16), (-.09, 0), (.09, 0))]
    c.label(text, *min(candidates, key=overlap), width, height, 10, bold=True, align="center")


def draw_weighted_links(c, visual, boxes):
    outgoing, incoming = defaultdict(list), defaultdict(list)
    for i, edge in enumerate(visual.edges):
        outgoing[edge.source].append((i, edge))
        incoming[edge.target].append((i, edge))
    max_flow = max(sum(edge.weight for _, edge in edges) for edges in [*outgoing.values(), *incoming.values()])
    point_scale = min(20, min(h for _, _, _, h in boxes.values()) * c.h * c.pth * .78) / max_flow
    ports = {}
    for direction, groups in (("out", outgoing), ("in", incoming)):
        for key, edges in groups.items():
            x, y, w, h = boxes[key]
            weights = [edge.weight * point_scale / (c.h * c.pth) for _, edge in edges]
            cursor = y + (h - sum(weights)) / 2
            for (index, _), weight in zip(edges, weights):
                ports[direction, index] = (x + w if direction == "out" else x, cursor + weight / 2)
                cursor += weight
    for index, edge in enumerate(visual.edges):
        start, end = ports["out", index], ports["in", index]
        c.line(start, end, color=c.accent, weight=max(.5, edge.weight * point_scale), z=-3)
        graph_label(c, f"{edge.label} {fmt(edge.weight)}".strip(),
                    (start[0] + end[0]) / 2 - .085, (start[1] + end[1]) / 2 - .085)


def schedule(c, v):
    left = .25
    boundaries = v.boundaries or list(range(len(v.periods) + 1))
    positions = list(map(scale(boundaries, left, 1), boundaries))
    row = .84 / len(v.items)
    coords = {}
    for i, period in enumerate(v.periods):
        x, end = positions[i:i + 2]
        step = end - x
        if period == c.highlight_band:
            c.shape(x, .11, step, .87, c.surface, z=-5)
        c.label(period, x + step * .025, 0, step * .95, .11, 11, bold=True, align="center")
        c.line((x, .11), (x, .98), color=c.rule)
    for i, item in enumerate(v.items):
        y = .12 + i * row
        x, end = positions[item.start], positions[item.end + 1]
        width = end - x
        mid = y + row * .42
        if item.label == c.highlight_band:
            c.shape(0, y, 1, row * .93, c.surface, z=-5)
        c.label(item.label, .003, y + .005, left - .025, row * .84, 13, bold=True)
        if item.kind in {"gate", "milestone"}:
            marker_width = min(.023, width * .32)
            x, end = x + width / 2 - marker_width / 2, x + width / 2 + marker_width / 2
            c.shape(x, mid - row * .21, marker_width, row * .42, c.accent if item.kind == "gate" else c.muted,
                    shape="diamond" if item.kind == "gate" else "ellipse")
        else:
            x, end = x + width * .04, end - width * .04
            c.shape(x, mid - row * .21, end - x, row * .42,
                    c.accent if item.kind == "phase" else blend(c.slide.background, c.accent, .55), shape="round")
        c.line((0, y + row * .93), (1, y + row * .93), color=c.rule, z=-4)
        coords[item.id] = (x, end, mid)
        c.mark(item.label, end, mid)
    for item in v.items:
        x, _, mid = coords[item.id]
        for key in item.predecessors:
            _, end, past_y = coords[key]
            c.line((end, past_y), (x, mid), arrow=True, route="elbow")


def sequence(c, v):
    step = 1 / len(v.participants)
    for i, title in enumerate(v.participants):
        x = (i + .5) * step
        c.shape(x - step * .40, .015, step * .80, .13, c.surface, border=c.accent)
        c.label(title, x - step * .38, .035, step * .76, .095, 14, bold=True, align="center")
        c.line((x, .145), (x, .98), color=c.rule, dashed=True, z=-4)
    height = .80 / len(v.messages)
    for i, message in enumerate(v.messages):
        start, end = [(key + .5) * step for key in (message.source, message.target)]
        y = .17 + (i + .8) * height
        c.line((start, y), (end, y), arrow=True, dashed=message.kind == "return")
        left = min(start, end)
        c.label(message.label, left + .006, y - min(.092, height * .79), abs(end - start) - .012,
                min(.086, height * .75), 12, align="center")
        c.mark(message.label, end, y)


def venn(c, v):
    height = min(.76, .50 * c.w * c.ptw / (c.h * c.pth))
    width = height * c.h * c.pth / (c.w * c.ptw)
    for i, (name, only) in enumerate(((v.left, v.left_only), (v.right, v.right_only))):
        center = .5 + (i - .5) * width * .64
        fill = blend(c.slide.background, c.palette[i], .38)
        c.shape(center - width / 2, .54 - height / 2, width, height, fill,
                shape="ellipse", opacity=.68, border=c.palette[i])
        title_width = width * .60
        c.label(name, center - title_width / 2, 0, title_width, .14, 15, bold=True, align="center")
        x = center + (i - .5) * width * .31
        c.label(only, x - width * .17, .40, width * .34, .28, 12, align="center")
        c.mark(name, x, .54)
    c.label(v.intersection, .5 - width * .17, .40, width * .34, .28, 12, bold=True, align="center")


def code(c, v):
    height = min(.12, .88 / len(v.lines))
    c.label(v.language, .022, 0, .95, .10, 13, color=c.muted, bold=True)
    c.shape(0, .105, 1, height * len(v.lines), c.surface)
    for i, line in enumerate(v.lines):
        y = .105 + i * height
        if line.highlight:
            c.shape(.002, y, .996, height, blend(c.slide.background, c.accent, .22), z=-1)
            c.shape(.002, y, .006, height, c.accent, z=-1)
        c.label(str(i + 1), .015, y + height * .08, .054, height * .85, 12, color=c.muted,
                font="Courier New", align="right")
        c.label(line.code.expandtabs(4), .09, y + height * .08, .90, height * .85, 14,
                font="Courier New", bold=line.highlight)
        c.mark(str(i + 1), .98, y + height / 2)


def legacy_diagram(c, v):
    from app.diagrams import draw_diagram, draw_swimlane

    elements = []
    top = .25
    left, right, bottom = .07, .93, .955
    theme = c.theme
    def label(value, x, y, w, h, preferred, minimum, color, **options):
        if value.strip():
            height = min(h, .049 + .019 * (len(value) > 38))
            elements.append(Text(x=x, y=y, w=w, h=height, text=value, color=color, size=preferred,
                                 bold=options.get("bold", False), align=options.get("align", "left")))
            return y + height
        return y

    if isinstance(v, FlowVisual):
        draw_diagram(elements, v.diagram, top, theme, c.slide.background, label, compact=False)
    else:
        draw_swimlane(elements, v.swimlane, top, theme, c.slide.background, label)
    for element in elements:
        x, y = (element.x - left) / (right - left), (element.y - top) / (bottom - top)
        w, h = element.w / (right - left), element.h / (bottom - top)
        if isinstance(element, Text):
            c.label(element.text, x, y, w, h, round(element.size), color=element.color, bold=element.bold,
                    align=element.align)
            c.mark(element.text, x + w, y + h / 2)
        else:
            c.slide.elements.append(element.model_copy(update=c.box(x, y, w, h)))


DRAW = {QuantitativeVisual: quantitative, MatrixVisual: matrix, ScatterVisual: scatter, GraphVisual: graph,
        ScheduleVisual: schedule, SequenceVisual: sequence, VennVisual: venn, CodeVisual: code,
        FlowVisual: legacy_diagram, SwimlaneVisual: legacy_diagram}


def compose_exhibit(document, slide, content, bounds, *, annotations=(), planned_annotations=()):
    visual, details = content.visual, content.decorations
    x, y, w, h = bounds
    annotations = list(annotations) or planned_annotations
    notes = [(item if isinstance(item, str) else item.text, None if isinstance(item, str) else item.target)
             for item in annotations]
    if details and details.callout:
        notes.append((details.callout, None))
    sources = [source.title for source in document.sources if source.id in slide.source_ids]
    citation = ", ".join(shorten(title, width=55, placeholder="…") for title in sources[:2])
    if len(sources) > 2:
        citation += f" +{len(sources) - 2}"
    footer = "  ·  ".join(filter(None, (details.footnote if details else "", f"Sources: {citation}" if citation else "")))
    frame = ExhibitCanvas(document, slide, bounds)
    if details and any((details.kicker, details.key_metric, details.delta, details.status is not None)):
        kicker_x = 0
        if details.kicker and details.icon in ICON_NAMES:
            icon = slide_icon(document, details.icon, x, y + h * .023, min(.026, h * .070), slide.background)
            slide.elements.append(icon)
            kicker_x = icon.w / w + .008
        frame.label(details.kicker, kicker_x, 0, .30 - kicker_x, .12, 12, color=frame.muted, bold=True, centered=True)
        frame.label(details.key_metric, .32, 0, .29, .12, 23, color=frame.accent, bold=True)
        frame.label(details.delta, .62, 0, .13, .12, 15, color=frame.accent, bold=True)
        if details.status is not None:
            frame.label(f"{'○◔◑◕●'[details.status]}  {details.status_label}", .77, 0, .23, .12, 13, bold=True)
        frame.line((0, .125), (1, .125), color=frame.rule)
        y, h = y + h * .15, h * .85
    unit = visual.unit
    if isinstance(visual, ScatterVisual) and visual.size_label:
        unit = "  ·  ".join(filter(None, (unit, f"Bubble area: {visual.size_label}")))
    if isinstance(visual, QuantitativeVisual):
        if visual.type == "mekko":
            unit = "  ·  ".join(filter(None, ("Height: share of category", f"Width: {unit or 'category size'}")))
        elif visual.type == "pareto":
            unit = "  ·  ".join(filter(None, (unit, "Line: cumulative share")))
    lower = .09 if footer else 0
    upper = .08 if unit else 0
    if unit:
        frame.label(unit, 0, (y - frame.y) / frame.h, 1, .075, 11, color=frame.muted)
    if footer:
        frame.label(footer, 0, .92, 1, .08, 10, color=frame.muted)
    width = w * .72 if notes else w
    body = ExhibitCanvas(document, slide, (x, y + h * upper, width, h * (1 - lower - upper)),
                         highlight_band=details.highlight_band if details else None)
    DRAW[type(visual)](body, visual)
    if notes:
        rail = ExhibitCanvas(document, slide, (x, body.y, w, body.h))
        step = min(.29, .98 / len(notes))
        for i, (value, target) in enumerate(notes):
            top = i * step + .012
            rail.shape(.773, top, .227, step * .91, rail.surface, border=rail.rule)
            rail.label(value, .788, top + .009, .197, step * .84, 13, emphasis=details.emphasis if details else ())
            if target in body.marks:
                mx, my = body.marks[target]
                rail.line((.773, top + step * .46), (.72 * mx, my), color=rail.muted, arrow=True)
