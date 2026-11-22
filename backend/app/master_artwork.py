"""Render the inherited PowerPoint artwork without flattening editable slide content."""

import io
from contextlib import closing
from functools import lru_cache
from threading import Lock

import pypdfium2 as pdfium

from app.domain import BrandLayout, BrandTemplate, Document, Slide
from app.export_review import ReviewUnavailable, render_native_pdf
from app.powerpoint import to_pptx
from app.storage import Storage


artwork_lock = Lock()


def master_artwork(executable, asset_id, layout, background, aspect):
    # Recheck the cache under the lock. Several canvases may request the same layout at once.
    with artwork_lock:
        return render_artwork(executable, asset_id, layout.model_dump_json(), background, aspect)


@lru_cache(maxsize=40)
def render_artwork(executable, asset_id, layout_json, background, aspect):
    layout = BrandLayout.model_validate_json(layout_json)
    brand = BrandTemplate(asset_id=asset_id, layouts=[layout])
    slide = Slide(title=layout.name, background=background, brand_layout_index=layout.index)
    document = Document(title=layout.name, aspect_ratio=aspect, slides=[slide])
    raw = to_pptx(document, Storage().get, brand)
    try:
        with pdfium.PdfDocument(render_native_pdf(executable, raw)) as pdf:
            if len(pdf) != 1:
                raise ReviewUnavailable("The PowerPoint layout did not render as a single page.")
            with closing(pdf[0]) as page, closing(page.render(scale=1200 / max(page.get_size()))) as bitmap:
                output = io.BytesIO()
                bitmap.to_pil().save(output, format="PNG")
                return output.getvalue()
    except pdfium.PdfiumError as error:
        raise ReviewUnavailable("The PowerPoint layout could not be rendered.") from error
