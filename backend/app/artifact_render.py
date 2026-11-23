"""Rendering and conversion adapters shared by inline downloads and artifact workers."""
from app.branding import PPTX, layout_for_slide
from app.domain import BrandTemplate, Slide, read_document
from app.export_review import add_pdf_evidence, render_native_pdf, renderer, review_powerpoint
from app.master_artwork import master_artwork
from app.powerpoint import to_pptx
from app.previews import render_preview
from app.rendering import fit_slide_text, to_pdf
from app.storage import Storage, decode_upload

MIME = {"pptx": PPTX, "pdf": "application/pdf"}


def build(kind, params):
    storage = Storage()
    if kind == "conversion":
        media, raw, extracted = decode_upload(params["name"], storage.get(params["input_key"]), params["declared"])
        return raw, media, {"name": params["name"], "extracted": extracted}
    brand = BrandTemplate.model_validate(params["brand"]) if params.get("brand") else None
    if kind == "artwork":
        slide = Slide.model_validate(params["slide"])
        raw = master_artwork(params["renderer"], brand.asset_id, layout_for_slide(brand, slide),
                             slide.background, params["aspect"])
        return raw, "image/png", {}
    document = read_document(params["document"])
    if kind == "preview":
        slide = document.slides[0]
        artwork = None
        if brand and params.get("renderer"):
            artwork = master_artwork(params["renderer"], brand.asset_id, layout_for_slide(brand, slide),
                                     slide.background, document.aspect_ratio)
        return render_preview(document, slide, artwork), "image/png", {}
    for slide in document.slides:
        fit_slide_text(document, slide)
    format = params["format"]
    result = {"revision": params["revision"], "title": document.title}
    if format == "pptx":
        raw = to_pptx(document, storage.get, brand)
        if kind == "review":
            result["slides"] = review_powerpoint(raw, document)
    elif brand and (executable := renderer()):
        raw = add_pdf_evidence(render_native_pdf(executable, to_pptx(document, storage.get, brand)), document)
    else:
        raw = to_pdf(document, storage.get)
    return raw, MIME[format], result
