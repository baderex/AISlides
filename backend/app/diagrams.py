from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from app.design import blend, readable
from app.domain import Contract, Line, Shape


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


def draw_diagram(elements, diagram, body_top, theme, background, label, *, compact):
    """Compose the diagram from independently editable shapes, text and connectors."""
    shades = (theme.design.chart_colors + [theme.ink, theme.accent])[:3]
    role_colors = {"actor": theme.accent, "service": shades[0], "store": shades[1],
                   "decision": shades[2], "step": theme.accent}
    ink = readable(theme.ink, background)
    muted = readable(theme.muted, background, ink)
    connector = blend(background, ink, .72)

    def draw_node(node, x, y, w, h, condition="", *, wide=False):
        color = role_colors[node.role]
        panel = blend(background, color, .10)
        elements.append(Shape(x=x, y=y, w=w, h=h, fill=panel, border=blend(panel, color, .52), z=-2))
        elements.append(Shape(x=x, y=y, w=.006, h=h, fill=color, z=-1))
        text_x, text_w = x + .016, w - .032
        top = y + .014
        node_ink = readable(theme.ink, panel)
        if wide:
            label(node.label, text_x, top, .315, h - .024, 17, 10, node_ink, bold=True, keep_words=True)
            detail = "\n".join(part for part in (node.detail, node.contains) if part)
            label(detail, x + .355, top, w - .373, h - .024, 14, 10, readable(theme.muted, panel, node_ink))
            return
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

    def horizontal(x, y, width, arrows="none"):
        elements.append(Line(x=x, y=y - .012, w=width, h=.024, direction="right",
                             arrows=arrows, color=connector, weight=1.6, z=-3))

    def vertical(x, y, height, arrows="none"):
        elements.append(Line(x=x - .012, y=y, w=.024, h=height, direction="down",
                             arrows=arrows, color=connector, weight=1.6, z=-3))

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
        if lane.kind == "flow" and compact and len(lanes) == 1 and len(lane.nodes) == 4:
            gap_y = .038
            row_h = (end_y - top - gap_y * 3) / 4
            for i, node in enumerate(lane.nodes):
                node_y = top + i * (row_h + gap_y)
                draw_node(node, .07, node_y, .86, row_h, wide=True)
                if i:
                    vertical(.15, node_y - gap_y, gap_y, "both" if node.two_way else "end")
                    if node.incoming:
                        label(node.incoming, .177, node_y - gap_y + .004, .72, gap_y - .007, 10, 8, muted)
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
                draw_node(node, x, node_y, w, h)
                if i:
                    mid_y = node_y + h * .56
                    horizontal(x - space, mid_y, space, "both" if node.two_way else "end")
                    if node.incoming:
                        width = .148 if link_band else space - .012
                        label(node.incoming, x - (space + width) / 2,
                              node_y - link_band if link_band else mid_y - min(.055, h * .43),
                              width, link_band or min(.049, h * .41), 11, 9, muted, align="center")
        else:
            origin, *outcomes = lane.nodes
            available = end_y - top
            origin_h = min(.11, available * .35)
            draw_node(origin, .30, top, .40, origin_h)
            first_link = min(.025, available * .12)
            last_link = min(.036, available * .16)
            spine_y = top + origin_h + first_link
            outcomes_y = spine_y + last_link
            vertical(.5, top + origin_h, first_link)
            space = .018
            w = (.86 - space * (len(outcomes) - 1)) / len(outcomes)
            centers = [.07 + i * (w + space) + w / 2 for i in range(len(outcomes))]
            horizontal(centers[0], spine_y, centers[-1] - centers[0])
            for x, node in zip(centers, outcomes):
                vertical(x, spine_y, outcomes_y - spine_y, "end")
                draw_node(node, x - w / 2, outcomes_y, w, end_y - outcomes_y, condition=node.incoming)
        y = end_y + gap

    if diagram.recap:
        panel = blend(background, theme.accent, .12)
        elements.append(Shape(x=.07, y=.847, w=.86, h=.105, fill=panel, z=-1))
        label(diagram.recap, .088, .859, .824, .081, 15, 11, readable(ink, panel))
