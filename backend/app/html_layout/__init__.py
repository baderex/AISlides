"""Compose a slide in Chromium and keep the measured objects as its editable source."""
from functools import lru_cache
from pathlib import Path
import base64
import io

from playwright.sync_api import Error as BrowserError, sync_playwright
from PIL import Image

from app.config import settings
from app.db import uid
from app.domain import Document, ExhibitRegion, Slide, Theme
from app.html_layout.exhibits import attach_region
from app.html_layout.markup import parse_markup
from app.html_layout.review import readability_warnings
from app.providers import ProviderError
from app.rendering import preview
from app.storage import Storage

WIDTH = {"16:9":1280, "4:3":960, "1:1":720}


@lru_cache(maxsize=2)
def script(name):
    return Path(__file__).with_name(name).read_text(encoding="utf-8")


def launch(chromium):
    executable = settings().html_layout_browser
    candidates = [{"executable_path": executable}] if executable else [{}, {"channel":"msedge"}, {"channel":"chrome"}]
    for options in candidates:
        try:
            return chromium.launch(timeout=15000, **options)
        except BrowserError as error:
            failure = error
    raise ProviderError("The worker could not launch Chromium. Run python -m playwright install chromium "
                        "in the backend environment or set HTML_LAYOUT_BROWSER to its executable.") from failure


def picture_previews(root, allowed):
    pictures = {}
    nodes = [root]
    while nodes:
        node = nodes.pop()
        nodes.extend(node["children"])
        if node["tag"] != "picture":
            continue
        key = node["a"].get("asset")
        if key not in allowed:
            raise ValueError("Use a picture ID from available_pictures.")
        if key not in pictures:
            with Image.open(io.BytesIO(Storage().get(key))) as source:
                width, height = source.size
                source.thumbnail((1280, 720))
                preview = io.BytesIO()
                source.convert("RGBA").save(preview, "PNG")
            pictures[key] = {"width":width, "height":height,
                             "src":"data:image/png;base64," + base64.b64encode(preview.getvalue()).decode("ascii")}
    return pictures


def apply_brand_roles(root, theme, document, layout):
    kit = document.theme.brand_kit
    if not kit:
        return
    protected = set(kit.protected)
    theme["lockFont"] = "font" in protected
    rules = document.theme.design
    if layout in {"cover", "closing", "section"} and rules and "cover_background" in protected:
        root["a"]["bg"] = rules.cover_background
    elif layout in {"research", "chart", "metrics"} and rules and "dense_surface" in protected:
        root["a"]["bg"] = rules.dense_surface
    elif "background" in protected:
        root["a"]["bg"] = "background"
    # Map literal paint overrides back to brand roles. Preserve explicit contrast inks.
    palette = {theme[k].lower() for k in ("ink", "accent", "muted", "surface", "background")}
    if rules:
        palette.update(c.lower() for c in [rules.cover_background, rules.dense_surface, *rules.chart_colors])
    nodes = [root]
    while nodes:
        node = nodes.pop()
        nodes.extend(node["children"])
        for field, fallback in (("color", "ink"), ("bg", "surface")):
            literal = node["a"].get(field, "").lower()
            role = "accent" if node["tag"] in {"box","bar","flow","poly","arrow","rule"} else fallback
            if literal in theme.get("roleAliases", {}):
                node["a"][field] = theme["roleAliases"][literal]
            elif role in protected and literal.startswith("#") and literal not in palette | {"#ffffff","#000000"}:
                node["a"][field] = role


def compose(markup, title, theme, aspect, available_pictures=(), document=None, plan=None, candidate=None,
            observations=(), required_picture=None):
    page_data = {"title":title, "body":parse_markup(markup)}
    pictures = picture_previews(page_data["body"], available_pictures)
    if required_picture and required_picture not in pictures:
        raise ValueError("Include the assigned picture using its exact asset ID.")
    document = document or Document(title=title, aspect_ratio=aspect, theme=Theme(**{
        k: v for k, v in theme.items() if k in Theme.model_fields}))
    theme = dict(theme)
    apply_brand_roles(page_data["body"], theme, document, plan.layout if plan else None)
    logo = document.theme.brand_kit.logo_asset_id if document.theme.brand_kit else None
    if logo:
        if logo in pictures:
            raise ValueError("The brand logo is placed automatically. Remove its picture tag.")
        pictures.update(picture_previews({"tag":"picture","a":{"asset":logo},"children":[]}, {logo}))
    width = WIDTH[aspect]
    with sync_playwright() as playwright:
        browser = launch(playwright.chromium)
        try:
            page = browser.new_page(viewport={"width":width, "height":720}, offline=True)
            page.set_content(f"""<meta charset="utf-8"><meta http-equiv="Content-Security-Policy"
              content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'">
              <style>*{{box-sizing:border-box}}body{{margin:0}}
              .slide{{width:{width}px;height:720px;position:relative;overflow:hidden}}</style><main></main>""")
            # Only bundled code is evaluated. Untrusted markup crosses this boundary as parsed data.
            try:
                page.evaluate(script("draw.js"), {"theme":theme, "pages":[page_data], "width":width, "pictures":pictures,
                                                  "logo":logo, "hasExhibit":bool(plan and plan.exhibit)})
                if pictures:
                    page.evaluate("Promise.all(Array.from(document.images, image => image.decode()))")
                raw = page.evaluate(script("measure.js"), uid())
            except BrowserError as error:
                if page.is_closed() or not browser.is_connected():
                    raise ProviderError("Chromium closed while measuring the slide. Retry the saved checkpoint.") from error
                raise ValueError(str(error).split("\n")[0][:600]) from error
            regions = [node for node in raw["elements"] if node["type"] == "exhibit"]
            if len(regions) != int(bool(plan and plan.exhibit)):
                raise ValueError("Use exactly one exhibit frame for a planned exhibit, and none otherwise.")
            raw["elements"] = [node for node in raw["elements"] if node["type"] != "exhibit"]
            slide = Slide.model_validate(raw)
            if candidate:
                slide.notes, slide.source_ids, slide.layout = candidate.notes, candidate.source_ids, plan.layout
            if regions:
                region = ExhibitRegion.model_validate({k:v for k,v in regions[0].items() if k != "type"})
                slide = attach_region(document, slide, region, candidate, plan, observations)
            slide.warnings = list(dict.fromkeys([*slide.warnings, *readability_warnings(document, slide)]))[:30]
            if regions:
                return slide, preview(document, slide, Storage().get, width)
            return slide, page.screenshot(type="png", timeout=15000)
        finally:
            browser.close()
