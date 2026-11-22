from typing import Annotated, Literal

from pydantic import Field

from app.design import style_chart, style_table
from app.domain import Chart, Contract, Group, Identifier, Slide, Table, Text, Theme
from app.providers import Intelligence, ProviderError


class ChangeText(Contract):
    op: Literal["change_text"]
    slide_id: Identifier
    element_id: Identifier
    text: str = Field(min_length=1, max_length=5000)


class ReplaceSlide(Contract):
    op: Literal["replace_slide"]
    slide_id: Identifier
    slide: Slide


class Restyle(Contract):
    op: Literal["restyle"]
    theme: Theme


Operation = Annotated[ChangeText | ReplaceSlide | Restyle, Field(discriminator="op")]


class EditProposal(Contract):
    summary: str = Field(min_length=1, max_length=600)
    operations: list[Operation] = Field(min_length=1, max_length=15)


class ChatRequest(Contract):
    instruction: str = Field(min_length=3, max_length=2000)
    slide_id: Identifier | None = None


class ApplyEdit(Contract):
    revision: int = Field(ge=0)
    proposal: EditProposal


def propose(item, document, request):
    ai = Intelligence()
    if ai.provider == "demo":
        raise ProviderError("Conversational editing requires a configured live AI provider.")
    slides = [s for s in document.slides if not request.slide_id or s.id == request.slide_id]
    if not slides:
        raise ValueError("No slide matches the selected scope.")
    proposal = ai.structured(EditProposal, "Propose structured slide edits. Limit changes to the requested scope. Reuse slide and element IDs. "
        "Use change_text for targeted changes, replace_slide only when restructuring or regenerating a slide, and restyle only for a deck-level redesign. "
        "Preserve edited elements when replacing a slide. Keep existing source and asset references grounded in this deck.", {
        "instruction": request.instruction, "current_theme": document.theme.model_dump(),
        "slides": [s.model_dump(mode="json") for s in slides[:12]], "sources": [s.model_dump(mode="json") for s in document.sources]
    }, "slide")
    if request.slide_id and any(isinstance(op, Restyle) or op.slide_id != request.slide_id for op in proposal.operations):
        raise ProviderError("The model proposed changes outside the selected slide. Try a more specific edit.")
    return proposal


def inherited_elements(elements):
    for element in elements:
        if element.edited:
            continue
        yield element
        if isinstance(element, Group):
            yield from inherited_elements(element.elements)


def apply(document, proposal):
    from app.domain import read_document

    slides = {slide.id: slide for slide in document.slides}
    for operation in proposal.operations:
        if isinstance(operation, Restyle):
            keys = ("background", "ink", "accent", "muted", "surface")
            colors = {getattr(document.theme, key): getattr(operation.theme, key) for key in keys}
            theme = operation.theme
            previous = document.theme.design
            if previous and theme.design is None:
                # Older edit clients submit just the base palette.
                updates = {key: colors.get(getattr(previous, key), getattr(previous, key))
                           for key in ("cover_background", "dense_surface", "dense_ink")}
                if previous.heading_font == document.theme.font:
                    updates["heading_font"] = theme.font
                updates["chart_colors"] = [colors.get(shade, shade) for shade in previous.chart_colors]
                theme = theme.model_copy(update={"design": previous.model_copy(update=updates)})
            rules = theme.design
            for slide in slides.values():
                if previous and rules and slide.layout in {"cover", "closing", "section", "quote"}:
                    slide.background = rules.cover_background
                else:
                    slide.background = colors.get(slide.background, slide.background)
                for element in inherited_elements(slide.elements):
                    for attr in ("fill", "color", "border", "header_fill", "header_color", "label_color"):
                        value = getattr(element, attr, None)
                        if value in colors:
                            setattr(element, attr, colors[value])
                    if isinstance(element, Text):
                        element.font = rules.heading_font if rules and element.text == slide.title else theme.font
                    elif rules and isinstance(element, Chart):
                        style_chart(element, theme, slide.background)
                    elif rules and isinstance(element, Table):
                        style_table(element, theme)
            document.theme = theme
            continue
        slide = slides.get(operation.slide_id)
        if not slide:
            raise ValueError("The proposal references an unknown slide.")
        if isinstance(operation, ChangeText):
            element = next((el for el in slide.elements if el.id == operation.element_id and isinstance(el, Text)), None)
            if not element:
                raise ValueError("The proposal references an unknown text box.")
            element.text = operation.text
        else:
            replacement = operation.slide.model_copy(deep=True)
            replacement.id = slide.id
            edited = {el.id: el for el in slide.elements if el.edited}
            replacement.elements = [el for el in replacement.elements if el.id not in edited] + list(edited.values())
            slides[slide.id] = replacement
    document.slides = list(slides.values())
    return read_document(document.model_dump(mode="json"))
