import io
import re
from pathlib import PurePosixPath
from urllib.parse import urlsplit
from zipfile import ZipFile

from defusedxml import ElementTree
from pptx import Presentation
from pptx.enum.shapes import PP_PLACEHOLDER as Placeholder
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.oxml.ns import qn

from app.domain import BrandLayout, BrandSlot, BrandTemplate, Chart, Image, Skill, Slide, Table, Text, Theme


PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
SLOT_TYPES = {
    Placeholder.TITLE: "title", Placeholder.CENTER_TITLE: "title", Placeholder.SUBTITLE: "subtitle",
    Placeholder.BODY: "body", Placeholder.OBJECT: "body", Placeholder.CHART: "chart",
    Placeholder.TABLE: "table", Placeholder.PICTURE: "picture",
}
ASPECTS = {"16:9": 16 / 9, "4:3": 4 / 3, "1:1": 1}


def validate_package(raw: bytes):
    with ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        names = {entry.filename.casefold() for entry in entries}
        if (len(entries) > 1500 or len(names) != len(entries)
                or sum(entry.file_size for entry in entries) > 55_000_000):
            raise ValueError("The PowerPoint template exceeds package limits.")
        for entry in entries:
            name = entry.filename.casefold()
            if (name.startswith("/") or "\\" in name or ".." in PurePosixPath(name).parts
                    or "/activex/" in name or "/embeddings/" in name and not name.endswith(".xlsx")
                    or "vbaproject" in name):
                raise ValueError("Use a PowerPoint template without macros or embedded objects other than chart workbooks.")
            if name.endswith((".xml", ".rels")):
                root = ElementTree.fromstring(archive.read(entry))
                if name.endswith(".rels"):
                    for node in root:
                        mode = node.get("TargetMode", "Internal").casefold()
                        if mode == "external" and (node.get("Type") != RT.HYPERLINK or
                                urlsplit(node.get("Target", "")).scheme not in {"https", "http", "mailto"}):
                            raise ValueError("Use a PowerPoint template with all images and media embedded.")
                elif any(node.get("action", "").casefold().startswith(("ppaction://macro", "ppaction://program", "ppaction://ole"))
                         for node in root.iter()):
                    raise ValueError("Remove macro, program and embedded object actions from the template.")
                if name == "[content_types].xml" and any(
                    "macroenabled" in node.get("ContentType", "").casefold() for node in root
                ):
                    raise ValueError("Macro-enabled templates are unsupported.")


def native_layouts(presentation):
    return [layout for master in presentation.slide_masters for layout in master.slide_layouts]


def theme_values(master):
    related = next((rel for rel in master.part.rels.values() if rel.reltype == RT.THEME), None)
    if not related:
        return {}, {}
    root = ElementTree.fromstring(related.target_part.blob)
    palette = {}
    scheme = root.find(".//" + qn("a:clrScheme"))
    for entry in scheme if scheme is not None else ():
        if len(entry):
            value = entry[0].get("lastClr") or entry[0].get("val", "")
            if re.fullmatch(r"[0-9a-fA-F]{6}", value):
                palette[entry.tag.split("}")[-1]] = "#" + value.upper()
    aliases = master._element.find(qn("p:clrMap"))
    if aliases is not None:
        palette.update({key: palette[value] for key, value in aliases.items() if value in palette})
    fonts = {}
    for kind, token in [("majorFont", "+mj-lt"), ("minorFont", "+mn-lt")]:
        node = root.find(f".//{qn('a:' + kind)}/{qn('a:latin')}")
        if node is not None and node.get("typeface"):
            fonts[token] = node.get("typeface")[:60]
    return palette, fonts


def drawing_color(parent, palette, default):
    if parent is not None and len(parent):
        node = parent[0]
        value = node.get("val", "")
        if node.tag == qn("a:srgbClr") and re.fullmatch(r"[0-9a-fA-F]{6}", value):
            return "#" + value.upper()
        if node.tag == qn("a:schemeClr"):
            return palette.get(value, default)
    return default


def layout_metadata(layout, index, width, height, palette, fonts):
    bg = layout._element.find(f".//{qn('p:bg')}")
    if bg is None:
        bg = layout.slide_master._element.find(f".//{qn('p:bg')}")
    background = palette.get("bg1", "#FFFFFF")
    if bg is not None:
        background = drawing_color(bg.find(f".//{qn('a:solidFill')}"), palette, background)
    slots = []
    for placeholder in layout.placeholders:
        kind = SLOT_TYPES.get(placeholder.placeholder_format.type)
        left, top, w, h = [getattr(placeholder, attr) for attr in ("left", "top", "width", "height")]
        if not kind or None in (left, top, w, h):
            continue
        x, y = max(0, left / width), max(0, top / height)
        w, h = min(1, (left + w) / width) - x, min(1, (top + h) / height) - y
        if w < .04 or h < .035:
            continue
        title = kind == "title"
        style_tag = "titleStyle" if title else "bodyStyle"
        master_style = layout.slide_master._element.find(f".//{qn('p:' + style_tag)}/{qn('a:lvl1pPr')}")
        body = placeholder._element.find(qn("p:txBody"))
        styles = [node for node in (
            master_style,
            body.find(f"{qn('a:lstStyle')}/{qn('a:lvl1pPr')}") if body is not None else None,
            body.find(f"{qn('a:p')}/{qn('a:pPr')}") if body is not None else None,
        ) if node is not None]
        size, ink, align, bold = (36 if title else 20), palette.get("tx1", "#181D3B"), "left", title
        if placeholder.placeholder_format.type == Placeholder.CENTER_TITLE:
            align = "center"
        font = fonts.get("+mj-lt" if title else "+mn-lt", "Arial")
        for style in styles:
            align = {"l": "left", "ctr": "center", "r": "right"}.get(style.get("algn"), align)
            run = style.find(qn("a:defRPr"))
            if run is not None:
                if run.get("sz", "").isdigit():
                    size = max(8, min(100, int(run.get("sz")) / 100))
                if "b" in run.attrib:
                    bold = run.get("b") in {"1", "true"}
                ink = drawing_color(run.find(qn("a:solidFill")), palette, ink)
                latin = run.find(qn("a:latin"))
                if latin is not None and (face := latin.get("typeface")):
                    font = fonts.get(face, font if face.startswith("+") else face[:60])
        slots.append(BrandSlot(idx=placeholder.placeholder_format.idx, kind=kind, x=x, y=y, w=w, h=h,
                               size=size, color=ink, font=font, align=align, bold=bold))
    name = (layout.name or f"Layout {index + 1}")[:220]
    native_type = layout._element.get("type", "")
    return BrandLayout(index=index, name=name, background=background, slots=slots[:32],
                       cover=native_type == "title" or "cover" in name.casefold(),
                       section=native_type == "secHead" or "section" in name.casefold())


def inspect_template(raw: bytes, asset_id: str, name: str) -> Skill:
    validate_package(raw)
    presentation = Presentation(io.BytesIO(raw))
    width, height = presentation.slide_width, presentation.slide_height
    if not width or not height or max(width, height) > 51 * 914400:
        raise ValueError("The PowerPoint template has invalid slide dimensions.")
    aspect = min(ASPECTS, key=lambda ratio: abs(ASPECTS[ratio] - width / height))
    if abs(ASPECTS[aspect] - width / height) > .02:
        raise ValueError("Branded templates must use 16:9, 4:3 or square slides.")
    layouts = native_layouts(presentation)
    if not layouts or len(layouts) > 100:
        raise ValueError("Use a PowerPoint template with 1 to 100 slide layouts.")
    themes = {master.part.partname: theme_values(master) for master in presentation.slide_masters}
    metadata = [layout_metadata(layout, index, width, height, *themes[layout.slide_master.part.partname])
                for index, layout in enumerate(layouts)]
    if not any(any(slot.kind in {"title", "body"} for slot in layout.slots) for layout in metadata):
        raise ValueError("Add a title or content placeholder in PowerPoint's Slide Master view and try again.")
    body = choose_layout(metadata, "summary")
    palette, fonts = themes[layouts[body.index].slide_master.part.partname]
    ink = palette.get("tx1", "#181D3B")
    theme = Theme(background=body.background, ink=ink, accent=palette.get("accent1", "#4C52D9"),
                  muted=ink, surface=palette.get("bg2", "#E7E9FF"), font=fonts.get("+mn-lt", "Arial"))
    return Skill(name=name, category="Branded templates", description="PowerPoint masters, layouts and placeholders are reused on export.",
                 formats=[aspect], recipes=["cover", "summary", "closing"], theme=theme,
                 brand=BrandTemplate(asset_id=asset_id, layouts=metadata))


def choose_layout(layouts, role):
    def rank(layout):
        kinds = [slot.kind for slot in layout.slots]
        title, body = "title" in kinds, kinds.count("body")
        if role in {"cover", "closing"}:
            return (layout.cover and title, title, "subtitle" in kinds, not layout.section, body)
        if role == "section":
            return (layout.section and title, title, not layout.cover, "subtitle" in kinds, body)
        preferred = {"chart": "chart", "research": "table", "showcase": "picture", "editorial": "picture"}.get(role)
        return (title and (body > 0 or preferred in kinds), not layout.cover and not layout.section,
                preferred in kinds, body >= 2 if role == "comparison" else body == 1, title, body)

    return max(layouts, key=rank)


def compose_branded(item, content, document, image_ids, brand):
    layout = choose_layout(brand.layouts, item.layout)
    sources = {source.id for source in document.sources}
    slide = Slide(title=item.title, layout=item.layout, background=layout.background, notes=content.notes,
                  source_ids=list(dict.fromkeys(key for key in content.source_ids if key in sources)))
    theme = document.theme
    title = next((slot for slot in layout.slots if slot.kind == "title"), None)
    subtitle = next((slot for slot in layout.slots if slot.kind == "subtitle"), None)
    bodies = sorted((slot for slot in layout.slots if slot.kind == "body"), key=lambda slot: (slot.x, slot.y))

    def geometry(slot):
        return {"x": slot.x, "y": slot.y, "w": slot.w, "h": slot.h}

    def text(value, slot, fallback):
        if not value.strip():
            return
        if slot:
            slide.elements.append(Text(**geometry(slot), text=value, size=slot.size, color=slot.color, font=slot.font,
                                       bold=slot.bold, align=slot.align, placeholder_idx=slot.idx))
        else:
            slide.elements.append(Text(**fallback, text=value, font=theme.font, color=theme.ink))

    text(item.title, title, {"x": .06, "y": .06, "w": .88, "h": .15, "size": 32, "bold": True})
    points = [" — ".join(filter(None, (point.title, point.value if slide.source_ids else "", point.body)))
              for point in content.points]
    if subtitle:
        text(content.subtitle, subtitle, {})
    elif content.subtitle:
        points.insert(0, content.subtitle)
    if item.layout == "cover" and not bodies:
        return slide

    top = max([.27, *(el.y + el.h + .025 for el in slide.elements)])
    fallback = {"x": .07, "y": min(top, .84), "w": .86, "h": .94 - min(top, .84)}
    exhibit_kind = None
    if content.chart and slide.source_ids and item.layout == "chart":
        exhibit_kind = "chart"
    elif content.table and item.layout in {"comparison", "research"} and all(
            len(row) == len(content.table[0]) for row in content.table) and 1 <= len(content.table[0]) <= 8:
        exhibit_kind = "table"
    elif content.image_asset_id in image_ids and item.layout in {"showcase", "editorial", "research"}:
        exhibit_kind = "picture"
    if exhibit_kind:
        slot = next((slot for slot in layout.slots if slot.kind == exhibit_kind), None)
        exhibit = geometry(slot or bodies[-1]) if slot or bodies else fallback
        if slot:
            exhibit["placeholder_idx"] = slot.idx
        try:
            if exhibit_kind == "chart":
                chart = content.chart
                slide.elements.append(Chart(**exhibit, chart=chart.chart, categories=chart.categories,
                                            series=[{"name": chart.label, "values": chart.values}], color=theme.accent))
            elif exhibit_kind == "table":
                slide.elements.append(Table(**exhibit, rows=[[cell[:180] for cell in row] for row in content.table],
                                            color=theme.ink, fill=theme.surface, header_fill=theme.accent, header_color="#FFFFFF"))
            else:
                slide.elements.append(Image(**exhibit, asset_id=content.image_asset_id, alt=item.title))
        except ValueError:
            slide.warnings.append("The exhibit could not be rendered in the template.")
        else:
            if not slot and bodies:
                bodies.pop()
            if not bodies:
                slide.notes = "\n\n".join(filter(None, (slide.notes, "\n".join(points))))[:7000]
                return slide
    if not bodies:
        text("\n\n".join(points), None, {**fallback, "size": 20})
    for index, slot in enumerate(bodies):
        start = index * len(points) // len(bodies)
        stop = (index + 1) * len(points) // len(bodies)
        text("\n\n".join(points[start:stop]), slot, {})
    return slide
