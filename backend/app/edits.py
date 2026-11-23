import re
from typing import Annotated, Literal

from pydantic import Field

from app.design import ArtDirection, apply_brand_kit, enforce_constraints, explicit_constraints, style_chart, style_table
from app.db import uid
from app.domain import Chart, Contract, Group, Identifier, Slide, Table, Text, Theme
from app.providers import Intelligence, ProviderError
from app.slide_design import recompose_slide


class ChangeText(Contract):
    op: Literal["change_text"]
    slide_id: Identifier
    element_id: Identifier
    text: str = Field(min_length=1, max_length=5000)


class ReplaceSlide(Contract):
    op: Literal["replace_slide"]
    slide_id: Identifier
    slide: Slide


class InsertSlide(Contract):
    op: Literal["insert_slide"]
    position: int | None = Field(default=None, ge=1, le=40, description="One-based position for the new slide.")
    slide: Slide


class Restyle(Contract):
    op: Literal["restyle"]
    theme: Theme


Operation = Annotated[ChangeText | ReplaceSlide | InsertSlide | Restyle, Field(discriminator="op")]


class EditProposal(Contract):
    summary: str = Field(min_length=1, max_length=600)
    operations: list[Operation] = Field(min_length=1, max_length=15)


class ChatRequest(Contract):
    instruction: str = Field(min_length=3, max_length=2000)
    slide_id: Identifier | None = None


class ApplyEdit(Contract):
    revision: int = Field(ge=0)
    proposal: EditProposal


class RestyleDirection(ArtDirection):
    summary: str = Field(min_length=1, max_length=600)


STYLE_EDIT = re.compile(
    r"\b(?:restyle|redesign|retheme|typography|palette|luxur(?:y|ious)|premium|"
    r"visual refresh|(?:more|less) (?:restrained|minimal|spacious|editorial|colorful|formal|playful))\b", re.I
)
CONTENT_EDIT = re.compile(
    r"\b(?:add|rewrite|remove|replace|change|update|revise)\b[^,.;]{0,40}"
    r"\b(?:wording|titles?|text|copy|facts?|chart data|numbers?|claims?|figures?)\b", re.I
)
SINGLE_SLIDE_REQUEST = re.compile(
    r"^\s*(?:(?:can|could|would) you\s+)?(?:please\s+)?(?:add|insert|append|create|generate|make)\s+"
    r"(?:(?:a|an|one|1|another|new|additional|more|extra)\s+)*slide\b"
    r"(?=\s*(?:$|[.?!,:]|(?:about|for|on|with|that|covering|showing|explaining|before|after|at|to the deck)\b))", re.I
)


def propose(document, request):
    ai = Intelligence()
    slides = [s for s in document.slides if not request.slide_id or s.id == request.slide_id]
    if request.slide_id and not slides:
        raise ValueError("No slide matches the selected scope.")
    default_position = document.slides.index(slides[0]) + 2 if request.slide_id else len(document.slides) + 1
    context = {
        "instruction": request.instruction, "current_theme": document.theme.model_dump(),
        "deck_title": document.title, "aspect_ratio": document.aspect_ratio,
        "default_insertion_position": default_position,
        "slide_titles_in_order": [s.title for s in document.slides],
        "slides": [s.model_dump(mode="json", exclude={"spec"}) for s in slides[:12]],
        "sources": [s.model_dump(mode="json") for s in document.sources],
    }
    if SINGLE_SLIDE_REQUEST.match(request.instruction):
        if len(document.slides) >= 40:
            raise ValueError("The presentation already contains the maximum of 40 slides.")
        insertion = ai.structured(InsertSlide,
            "Create one complete new slide with visible editable elements matching the deck's theme. "
            "Ground its content in the supplied material. Omit position unless the user specifies a location.",
            context, "edit")
        proposal = EditProposal(summary=f"Add a slide titled “{insertion.slide.title}”.", operations=[insertion])
        return prepare_insertions(proposal, default_position)
    constraints = explicit_constraints(request.instruction)
    if not request.slide_id and STYLE_EDIT.search(request.instruction) and not CONTENT_EDIT.search(request.instruction):
        direction = ai.structured(RestyleDirection,
            "Redesign the deck to match the user's visual request. Specify a complete coordinated theme and design rules. "
            "Choose a mood, spacing and cover treatment that distinguish this design from the current one. "
            "The studio recomposes from the same facts and automatically preserves manual edits. "
            "Describe only the visual changes in the summary.", {
                "instruction": request.instruction, "current_theme": document.theme.model_dump(mode="json"),
                "aspect_ratio": document.aspect_ratio,
            }, "design")
        theme = Theme.model_validate({**direction.model_dump(exclude={"summary"}), "profile": document.theme.profile})
        theme.constraints = constraints
        return EditProposal(summary=direction.summary, operations=[Restyle(op="restyle", theme=theme)])
    proposal = ai.structured(EditProposal, "Propose structured slide edits. Limit changes to the requested scope. Reuse IDs for existing content. "
        "Use insert_slide whenever the user asks for additional slides. Give each new slide editable elements. "
        "Omit position unless the user specifies a location. Adding slides alone must preserve every existing slide. "
        "Use change_text for targeted changes, replace_slide only when restructuring or regenerating a slide, and restyle only for a deck-level redesign. "
        "For a deck-level redesign supply complete design rules and choose a mood, spacing and cover treatment that "
        "reflect the requested style. The studio rebuilds eligible slide layouts from the original content. "
        "Preserve edited elements when replacing a slide. Keep existing source and asset references grounded in this deck.", context, "edit")
    if request.slide_id and any(isinstance(op, Restyle) or
            isinstance(op, (ChangeText, ReplaceSlide)) and op.slide_id != request.slide_id for op in proposal.operations):
        raise ProviderError("The model proposed changes outside the selected slide. Try a more specific edit.")
    for operation in proposal.operations:
        if isinstance(operation, Restyle):
            operation.theme.constraints = constraints
    return prepare_insertions(proposal, default_position)


def prepare_insertions(proposal, default_position):
    for operation in proposal.operations:
        if isinstance(operation, InsertSlide):
            operation.slide.id = uid()
            operation.slide.spec = None
            if operation.position is None:
                operation.position = default_position
            default_position = operation.position + 1
    return proposal


def inherited_elements(elements):
    for element in elements:
        if element.edited:
            continue
        yield element
        if isinstance(element, Group):
            yield from inherited_elements(element.elements)


def apply(document, proposal, *, locked_theme=False):
    from app.domain import read_document

    slides = {slide.id: slide for slide in document.slides}
    for operation in proposal.operations:
        if isinstance(operation, InsertSlide):
            if operation.slide.id in slides:
                raise ValueError("An inserted slide must have a new ID.")
            ordered = list(slides.values())
            position = operation.position or len(ordered) + 1
            if position > len(ordered) + 1:
                raise ValueError("The insertion position is outside the presentation.")
            ordered.insert(position - 1, operation.slide.model_copy(deep=True))
            slides = {slide.id: slide for slide in ordered}
            continue
        if isinstance(operation, Restyle):
            if locked_theme:
                raise ValueError("The PowerPoint master controls this deck's theme.")
            keys = ("background", "ink", "accent", "muted", "surface")
            old_theme = document.theme
            theme = enforce_constraints(operation.theme, operation.theme.constraints or old_theme.constraints)
            colors = {getattr(old_theme, key): getattr(theme, key) for key in keys}
            previous = old_theme.design
            if previous and theme.design is None:
                # Older edit clients submit just the base palette.
                updates = {key: colors.get(getattr(previous, key), getattr(previous, key))
                           for key in ("cover_background", "dense_surface", "dense_ink")}
                if previous.heading_font == document.theme.font:
                    updates["heading_font"] = theme.font
                updates["chart_colors"] = [colors.get(shade, shade) for shade in previous.chart_colors]
                theme = theme.model_copy(update={"design": previous.model_copy(update=updates)})
            theme = enforce_constraints(theme, theme.constraints)
            theme = apply_brand_kit(theme, old_theme)
            rules = theme.design
            style_roles = {"mood", "spacing", "cover_style", "card_shape"}
            keep_recipe = bool(previous and rules and
                               previous.model_dump(include=style_roles) == rules.model_dump(include=style_roles))
            document.theme = theme
            for index, slide in enumerate(list(slides.values())):
                recomposed = recompose_slide(slide, document, index, keep_recipe=keep_recipe)
                if recomposed is not None:
                    slides[slide.id] = recomposed
                    continue
                if previous and rules and slide.layout in {"cover", "closing", "section", "quote"}:
                    slide.background = rules.cover_background
                else:
                    slide.background = colors.get(slide.background, slide.background)
                if slide.spec and (region := slide.spec.html_region) and region.background:
                    region.background = colors.get(region.background, region.background)
                for element in inherited_elements(slide.elements):
                    for attr in ("fill", "color", "border", "header_fill", "header_color", "label_color"):
                        value = getattr(element, attr, None)
                        if value in colors:
                            setattr(element, attr, colors[value])
                    if isinstance(element, Text):
                        element.font = rules.heading_font if rules and element.text == slide.title else theme.font
                    elif rules and isinstance(element, Chart):
                        style_chart(element, theme, slide.background)
                    elif isinstance(element, Table) and (rules or element.row_rule):
                        style_table(element, theme, slide.background,
                                    preserve_palette=theme.profile == "consulting" or element.placeholder_idx is not None)
            continue
        slide = slides.get(operation.slide_id)
        if not slide:
            raise ValueError("The proposal references an unknown slide.")
        if isinstance(operation, ChangeText):
            element = next((el for el in slide.elements if el.id == operation.element_id and isinstance(el, Text)), None)
            if not element:
                raise ValueError("The proposal references an unknown text box.")
            element.text = operation.text
            element.spans = []
            element.edited = True
        else:
            replacement = operation.slide.model_copy(deep=True)
            replacement.id = slide.id
            replacement.spec = None
            edited = {el.id: el for el in slide.elements if el.edited}
            replacement.elements = [el for el in replacement.elements if el.id not in edited] + list(edited.values())
            slides[slide.id] = replacement
    document.slides = list(slides.values())
    return read_document(document.model_dump(mode="json"))
