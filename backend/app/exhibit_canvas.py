from app.deck_design import DeckDesignSpec
from app.design import blend, readable
from app.palette import semantic_palette
from app.domain import Line, Shape, Text, TextSpan
from app.rendering import longest_word_width, physical, text_height


class ExhibitCanvas:
    """Map exhibit-local geometry into the slide body or a template slot."""

    def __init__(self, document, slide, bounds, *, highlight_band=None, recipe="standard", score_bars=False):
        self.document, self.slide = document, slide
        self.x, self.y, self.w, self.h = bounds
        self.theme = document.theme
        self.ink = readable(self.theme.ink, slide.background)
        self.muted = readable(self.theme.muted, slide.background, self.ink)
        self.colors = semantic_palette(self.theme, slide.background)
        self.accent = self.colors["highlight"]
        self.accent_text = self.colors["accentText"]
        self.surface = blend(slide.background, self.accent, .10)
        self.rule = self.colors["border"]
        self.palette = [v for k, v in self.colors.items() if k.startswith("series")]
        self.typography = DeckDesignSpec.from_theme(self.theme, document.aspect_ratio, slide.layout).typography
        self.ptw, self.pth = physical(document)
        self.marks = {}
        self.highlight_band = highlight_band
        self.recipe = recipe
        self.score_bars = score_bars

    def box(self, x, y, w, h):
        return dict(x=self.x + x * self.w, y=self.y + y * self.h, w=w * self.w, h=h * self.h)

    def shape(self, x, y, w, h, fill=None, *, shape="rect", border=None, opacity=1, z=-2, element_key=None):
        if w <= 0 or h <= 0:
            return None
        element = Shape(**self.box(x, y, w, h), fill=fill or self.surface, shape=shape,
                        border=border, opacity=opacity, z=z, element_key=element_key)
        self.slide.elements.append(element)
        return element

    def label(self, value, x, y, w, h, size=15, *, color=None, bold=False, align="left", font=None, emphasis=(),
              centered=False, element_key=None, role="label"):
        if not value.strip() or w <= 0 or h <= 0:
            return
        bounds = self.box(x, y, w, h)
        font = font or self.theme.font
        width, height = bounds["w"] * self.ptw, bounds["h"] * self.pth
        floor = self.typography[role].minimum_pt
        size = max(size, floor)
        while size > floor and (text_height(value, width, size, bold, font) + 3 > height or
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
        self.slide.elements.append(Text(**bounds, text=value, size=size, role=role, minimum_pt=floor, font=font, color=color or self.ink,
                                        bold=bold, align=align, spans=spans, element_key=element_key))

    def line(self, a, b, *, color=None, arrow=False, weight=1.2, dashed=False, z=-1,
             route="straight", anchors=(None, None), element_key=None):
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
                                        start_anchor=anchors[0], end_anchor=anchors[1], element_key=element_key))

    def mark(self, label, x, y):
        self.marks[label] = (x, y)

    def polygon(self, points, fill, *, element_key):
        xs, ys = zip(*points)
        x, y = min(xs), min(ys)
        w, h = max(xs) - x, max(ys) - y
        if not w or not h:
            return
        self.slide.elements.append(Shape(**self.box(x, y, w, h), fill=fill, z=-4, element_key=element_key,
                                         polygon=[((px - x) / w, (py - y) / h) for px, py in points]))
