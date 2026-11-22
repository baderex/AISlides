from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from app.design import blend, readable
from app.domain import ConnectorAnchor, Contract, Identifier, Line, Shape


class DiagramNode(Contract):
    label: Annotated[str, Field(min_length=1, max_length=46)]
    detail: str = Field(default="", max_length=90)
    role: Literal["actor", "service", "store", "decision", "step"] = "step"
    contains: str = Field(default="", max_length=30, description="Name of a component inside this node, if needed.")
    incoming: str = Field(default="", max_length=24, description="Optional short label for the link entering this node.")
    two_way: bool = Field(default=False, description="The incoming link carries traffic in both directions.")

    @field_validator("incoming")
    @classmethod
    def omit_source_echo(cls, value):
        return "" if value.casefold().startswith("from ") else value


class DiagramLane(Contract):
    title: str = Field(default="", max_length=40)
    kind: Literal["flow", "branch"] = "flow"
    nodes: list[DiagramNode] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def adequate_paths(self):
        if self.kind == "branch" and len(self.nodes) < 3:
            raise ValueError("A branch needs a starting node and at least two outcomes")
        return self


class Diagram(Contract):
    lanes: list[DiagramLane] = Field(min_length=1, max_length=2)
    recap: str = Field(default="", max_length=160)


class SwimlaneNode(Contract):
    id: Identifier
    label: Annotated[str, Field(min_length=1, max_length=36)]
    lane: int = Field(ge=0, le=2, description="Zero-based index into the lane names.")
    column: int = Field(ge=0, le=3, description="Zero-based horizontal position within the lane.")
    decision: bool = False


class SwimlaneEdge(Contract):
    source: Identifier
    target: Identifier
    label: str = Field(default="", max_length=32)
    emphasis: bool = False


class Swimlane(Contract):
    lanes: list[Annotated[str, Field(min_length=1, max_length=18)]] = Field(min_length=2, max_length=3)
    nodes: list[SwimlaneNode] = Field(min_length=2, max_length=12)
    edges: list[SwimlaneEdge] = Field(min_length=1, max_length=14)

    @model_validator(mode="after")
    def routes_fit(self):
        nodes = {node.id: node for node in self.nodes}
        cells = {(node.lane, node.column) for node in self.nodes}
        if len(nodes) != len(self.nodes) or len(cells) != len(nodes):
            raise ValueError("Assign unique node IDs and use each lane/column cell at most once")
        names = {lane.strip().casefold() for lane in self.lanes}
        if len(names) != len(self.lanes) or "" in names:
            raise ValueError("Give every lane a distinct name")
        if any(lane >= len(self.lanes) for lane, _ in cells):
            raise ValueError("Each node must use an existing lane")
        seen = set()
        for edge in self.edges:
            pair = frozenset((edge.source, edge.target))
            if len(pair) != 2 or not pair <= nodes.keys() or pair in seen:
                raise ValueError("Each connector must join two distinct nodes and use a distinct route")
            seen.add(pair)
            start, end = nodes[edge.source], nodes[edge.target]
            first_col, last_col = sorted((start.column, end.column))
            first_lane, last_lane = sorted((start.lane, end.lane))
            if start.lane == end.lane:
                blocked = any((start.lane, col) in cells for col in range(first_col + 1, last_col))
            elif start.column == end.column:
                blocked = any((lane, start.column) in cells for lane in range(first_lane + 1, last_lane))
            else:
                blocked = last_col - first_col > 1
            if blocked:
                raise ValueError("Keep same-row/column routes clear and use adjacent columns for diagonal handoffs")
        return self


def draw_swimlane(elements, diagram, body_top, theme, background, label):
    ink = readable(theme.ink, background)
    accent = readable(theme.accent, background, ink)
    muted = readable(theme.muted, background, ink)
    rail = blend(background, ink, .19)
    left, right, gutter, bottom = .07, .93, .233, .925
    row = (bottom - body_top) / len(diagram.lanes)
    column = (right - gutter) / (max(node.column for node in diagram.nodes) + 1)

    def centered_label(value, x, center_y, w, max_h, preferred, minimum, color, **options):
        if value.strip():
            label(value, x, center_y - max_h/2, w, max_h, preferred, minimum, color, **options)
            text = elements[-1]
            text.y = center_y - text.h/2

    for i, name in enumerate(diagram.lanes):
        top = body_top + i * row
        header = blend(background, theme.accent, .11 if i == 0 else .06)
        elements.extend((
            Shape(x=left, y=top, w=gutter-left, h=row, fill=header, z=-6),
            Shape(x=gutter, y=top, w=right-gutter, h=row, fill=blend(background, ink, .018 * i), z=-6),
            Shape(x=left, y=top, w=right-left, h=.002, fill=rail, z=-5),
        ))
        centered_label(name, left + .016, top + row / 2, gutter-left-.032, .078, 17, 10,
                       readable(ink, header), bold=True, keep_words=True)
    elements.append(Shape(x=left, y=bottom, w=right-left, h=.002, fill=rail, z=-5))

    boxes = {}
    lane_by_node = {}
    for node in diagram.nodes:
        w, h = min(.238, column * .59), min(row * .54, .153 if node.decision else .125)
        cx, cy = gutter + (node.column + .5) * column, body_top + (node.lane + .5) * row
        box = Shape(x=cx - w/2, y=cy - h/2, w=w, h=h,
                    shape="diamond" if node.decision else "rect",
                    fill=blend(background, theme.accent, .10) if node.decision else background,
                    border=accent if node.decision else blend(background, ink, .43), z=-2)
        boxes[node.id] = box
        lane_by_node[node.id] = node.lane
        elements.append(box)
        inset = .22 if node.decision else .08
        centered_label(node.label, box.x + w*inset, cy, w*(1-2*inset), min(.093, h*.65),
                       15, 9, readable(ink, box.fill), bold=True, keep_words=True, align="center")

    for edge in diagram.edges:
        start, end = boxes[edge.source], boxes[edge.target]
        x1, x2 = start.x + start.w/2, end.x + end.w/2
        y1, y2 = start.y + start.h/2, end.y + end.h/2
        shade = accent if edge.emphasis else muted
        elbow = False
        if abs(x1-x2) < .00001:
            down = y1 < y2
            y1, y2 = (start.y + start.h, end.y) if down else (start.y, end.y + end.h)
            direction = "down" if down else "up"
            ports = ("bottom", "top") if down else ("top", "bottom")
            x, y, w, h = x1 - .006, min(y1, y2), .012, abs(y2-y1)
            divider = body_top + (min(lane_by_node[edge.source], lane_by_node[edge.target]) + 1) * row
            text_h = min(.065, divider - y - .009)
            if right - x1 >= .15:
                label_box = (x1 + .013, divider - text_h - .006, min(.24, right-x1-.014), text_h)
                align = "left"
            else:
                label_box = (x1 - .214, divider - text_h - .006, .20, text_h)
                align = "right"
        else:
            forward = x1 < x2
            x1, x2 = (start.x + start.w, end.x) if forward else (start.x, end.x + end.w)
            direction = "right" if forward else "left"
            ports = ("right", "left") if forward else ("left", "right")
            x, w = min(x1, x2), abs(x2-x1)
            elbow = abs(y1-y2) > .00001
            if elbow:
                y, h = min(y1, y2), abs(y2-y1)
                direction = "diagonal"
            else:
                y, h = y1 - .006, .012
            label_box = (x + .003, y1 - .063, w - .006, .054)
            align = "center"
        elements.append(Line(x=x, y=y, w=w, h=h, direction=direction, route="elbow" if elbow else "straight",
                             start_anchor=ConnectorAnchor(shape_id=start.id, side=ports[0]),
                             end_anchor=ConnectorAnchor(shape_id=end.id, side=ports[1]),
                             arrows="end", color=shade, weight=1.75 if edge.emphasis else 1.2, z=-3))
        label(edge.label, *label_box, 12, 8, shade, bold=edge.emphasis, align=align)


def draw_diagram(elements, diagram, body_top, theme, background, label, *, compact, vertical_flow=False):
    """Compose the diagram from independently editable shapes, text and connectors."""
    shades = [*(theme.design.chart_colors if theme.design else []), theme.ink, theme.accent, theme.muted][:3]
    role_colors = {"actor": theme.accent, "service": shades[1], "store": shades[2],
                   "decision": shades[0], "step": theme.accent}
    ink = readable(theme.ink, background)
    muted = readable(theme.muted, background, ink)
    connector = blend(background, ink, .72)

    def draw_node(node, x, y, w, h, condition="", *, wide=False):
        color = role_colors[node.role]
        panel = blend(background, color, .10)
        box = Shape(x=x, y=y, w=w, h=h, fill=panel, border=blend(panel, color, .52), z=-2)
        elements.append(box)
        elements.append(Shape(x=x, y=y, w=.006, h=h, fill=color, z=-1))
        text_x, text_w = x + .016, w - .032
        top = y + .014
        node_ink = readable(theme.ink, panel)
        if wide:
            label(node.label, text_x, top, .315, h - .024, 17, 10, node_ink, bold=True, keep_words=True)
            detail = "\n".join(part for part in (node.detail, node.contains) if part)
            label(detail, x + .355, top, w - .373, h - .024, 14, 10, readable(theme.muted, panel, node_ink))
            return box
        if condition:
            top = label(condition, text_x, top, text_w, h * .26, 11, 9,
                        readable(color, panel, node_ink), bold=True) + .004
        end = label(node.label, text_x, top, text_w, h * (.36 if node.detail or node.contains else .67),
                    23 if h >= .24 else 18, 10, node_ink, bold=True, keep_words=True)
        if node.contains:
            inner_y = y + h * (.69 if node.detail else .52)
            inner = blend(panel, role_colors["store"], .26)
            inner_h = y + h - .009 - inner_y
            elements.append(Shape(x=text_x, y=inner_y, w=text_w, h=inner_h, fill=inner, z=-1))
            label(node.contains, text_x + .008, inner_y + .005, text_w - .016, inner_h - .008, 15, 9,
                  readable(theme.ink, inner), bold=True)
        if node.detail:
            detail_end = inner_y - .004 if node.contains else y + h - .012
            if detail_end > end + .008:
                label(node.detail, text_x, end + .005, text_w, detail_end - end - .005, 15, 11,
                      readable(theme.muted, panel, node_ink))
        return box

    def horizontal(x, y, width, arrows="none", start=None, end=None):
        elements.append(Line(x=x, y=y - .012, w=width, h=.024, direction="right",
                             arrows=arrows, color=connector, weight=1.6, z=-3,
                             start_anchor=ConnectorAnchor(shape_id=start.id, side="right") if start else None,
                             end_anchor=ConnectorAnchor(shape_id=end.id, side="left") if end else None))

    def vertical(x, y, height, arrows="none", start=None, end=None):
        elements.append(Line(x=x - .012, y=y, w=.024, h=height, direction="down",
                             arrows=arrows, color=connector, weight=1.6, z=-3,
                             start_anchor=ConnectorAnchor(shape_id=start.id, side="bottom") if start else None,
                             end_anchor=ConnectorAnchor(shape_id=end.id, side="top") if end else None))

    lanes = diagram.lanes
    body_end = .82 if diagram.recap else .91
    weights = [1.24 if lane.kind == "branch" else 1 for lane in lanes]
    gap = .034
    height = (body_end - body_top - gap * (len(lanes) - 1)) / sum(weights)
    y = body_top
    for lane, weight in zip(lanes, weights):
        end_y = y + height * weight
        top = y
        if lane.title:
            top = label(lane.title, .07, y, .86, .056, 16, 12, ink, bold=True) + .011
        previous = None
        if lane.kind == "flow" and len(lanes) == 1 and (vertical_flow or (compact and len(lane.nodes) == 4)):
            gap_y = .038
            row_h = (end_y - top - gap_y * (len(lane.nodes) - 1)) / len(lane.nodes)
            for i, node in enumerate(lane.nodes):
                node_y = top + i * (row_h + gap_y)
                box = draw_node(node, .07, node_y, .86, row_h, wide=True)
                if i:
                    vertical(.5, node_y - gap_y, gap_y, "both" if node.two_way else "end", previous, box)
                    if node.incoming:
                        label(node.incoming, .53, node_y - gap_y + .004, .38, gap_y - .007, 10, 8, muted)
                previous = box
        elif lane.kind == "flow":
            nodes = lane.nodes
            space = .19 if len(nodes) == 2 else .076 if len(nodes) == 3 else .048
            w = (.86 - space * (len(nodes) - 1)) / len(nodes)
            link_band = .048 if len(nodes) >= 3 and any(node.incoming for node in nodes[1:]) else 0
            top += link_band
            h = min(.38, end_y - top)
            node_y = top + (end_y - top - h) / 2
            for i, node in enumerate(nodes):
                x = .07 + i * (w + space)
                box = draw_node(node, x, node_y, w, h)
                if i:
                    mid_y = node_y + h * .5
                    horizontal(x - space, mid_y, space, "both" if node.two_way else "end", previous, box)
                    if node.incoming:
                        width = .148 if link_band else space - .012
                        label(node.incoming, x - (space + width) / 2,
                              node_y - link_band if link_band else mid_y - min(.055, h * .43),
                              width, link_band or min(.049, h * .41), 11, 9, muted, align="center")
                previous = box
        else:
            origin, *outcomes = lane.nodes
            available = end_y - top
            origin_h = min(.11, available * .35)
            origin_box = draw_node(origin, .30, top, .40, origin_h)
            first_link = min(.025, available * .12)
            last_link = min(.036, available * .16)
            spine_y = top + origin_h + first_link
            outcomes_y = spine_y + last_link
            vertical(.5, top + origin_h, first_link, start=origin_box)
            space = .018
            w = (.86 - space * (len(outcomes) - 1)) / len(outcomes)
            centers = [.07 + i * (w + space) + w / 2 for i in range(len(outcomes))]
            horizontal(centers[0], spine_y, centers[-1] - centers[0])
            for x, node in zip(centers, outcomes):
                box = draw_node(node, x - w / 2, outcomes_y, w, end_y - outcomes_y, condition=node.incoming)
                vertical(x, spine_y, outcomes_y - spine_y, "end", end=box)
        y = end_y + gap

    if diagram.recap:
        panel = blend(background, theme.accent, .12)
        elements.append(Shape(x=.07, y=.847, w=.86, h=.105, fill=panel, z=-1))
        label(diagram.recap, .088, .859, .824, .081, 15, 11, readable(ink, panel))
