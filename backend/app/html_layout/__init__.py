"""Compose a slide in Chromium and keep the measured objects as its editable source."""
from functools import lru_cache
from pathlib import Path
import base64
import io

from playwright.sync_api import Error as BrowserError, sync_playwright
from PIL import Image

from app.config import settings
from app.db import uid
from app.domain import Slide
from app.html_layout.markup import parse_markup
from app.providers import ProviderError
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


def compose(markup, title, theme, aspect, available_pictures=()):
    page_data = {"title":title, "body":parse_markup(markup)}
    pictures = picture_previews(page_data["body"], available_pictures)
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
                page.evaluate(script("draw.js"), {"theme":theme, "pages":[page_data], "width":width, "pictures":pictures})
                if pictures:
                    page.evaluate("Promise.all(Array.from(document.images, image => image.decode()))")
                raw = page.evaluate(script("measure.js"), uid())
            except BrowserError as error:
                if page.is_closed() or not browser.is_connected():
                    raise ProviderError("Chromium closed while measuring the slide. Retry the saved checkpoint.") from error
                raise ValueError(str(error).split("\n")[0][:600]) from error
            slide = Slide.model_validate(raw)
            return slide, page.screenshot(type="png", timeout=15000)
        finally:
            browser.close()
