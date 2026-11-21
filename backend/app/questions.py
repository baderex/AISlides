from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.db import uid
from app.domain import Contract, Identifier, ShortText


class Option(Contract):
    id: Identifier
    label: ShortText
    description: str = Field(default="", max_length=240)
    swatch: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class QuestionBase(Contract):
    id: Identifier = Field(default_factory=uid)
    title: ShortText
    description: str = Field(default="", max_length=400)
    required: bool = True
    allow_custom_answer: bool = True


class Choice(QuestionBase):
    type: Literal["single_select", "visual_style", "theme", "audience", "yes_no", "objective", "detail_level", "output_format"]
    options: list[Option] = Field(min_length=2, max_length=8)
    recommended_option_id: Identifier | None = None

    @model_validator(mode="after")
    def options_valid(self):
        ids = {item.id for item in self.options}
        if len(ids) != len(self.options) or (self.recommended_option_id and self.recommended_option_id not in ids):
            raise ValueError("Question options or recommendation are invalid")
        return self


class MultiChoice(Choice):
    type: Literal["multi_select"] = "multi_select"


class FreeText(QuestionBase):
    type: Literal["free_text"] = "free_text"
    recommended_text: str | None = Field(default=None, max_length=1000)


class SlideCount(QuestionBase):
    type: Literal["slide_count"] = "slide_count"
    recommended_count: int = Field(default=6, ge=1, le=30)


Question = Annotated[Choice | MultiChoice | FreeText | SlideCount, Field(discriminator="type")]


class Clarification(Contract):
    questions: list[Question] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def unique_questions(self):
        if len({item.id for item in self.questions}) != len(self.questions):
            raise ValueError("Question IDs must be unique")
        return self


def standard_options(batch: Clarification, intent) -> Clarification:
    audience = Option(id="executive_board", label="Executive / Board", description="Strategic and decision-oriented")
    editorial = Option(id="editorial_report", label="Editorial report", description="Expressive typography and a restrained palette",
                       swatch="#EFE2D4")
    options = {"audience": audience, "visual_style": editorial, "theme": editorial}
    questions = []
    asked = set()
    for question in batch.questions:
        key = "visual_style" if question.type == "theme" else question.type
        if key in {"audience", "visual_style", "slide_count"}:
            value = {"audience": intent.audience, "visual_style": intent.visual_direction, "slide_count": intent.slide_count}[key]
            if value or key in asked:
                continue
            asked.add(key)
        if isinstance(question, Choice) and (option := options.get(question.type)):
            if not any(entry.label.lower() == option.label.lower() for entry in question.options):
                if option.id in {entry.id for entry in question.options}:
                    option = option.model_copy(update={"id": uid()})
                retained = question.options if len(question.options) < 8 else [
                    entry for index, entry in enumerate(question.options)
                    if index < 7 or entry.id == question.recommended_option_id]
                if len(retained) == 8:
                    retained.pop(6)
                question = question.model_copy(update={"options": [*retained, option]})
        questions.append(question)
    return Clarification(questions=questions)


class AnswerInput(Contract):
    question_id: Identifier
    option_ids: list[Identifier] = Field(default_factory=list, max_length=8)
    text: str = Field(default="", max_length=1000)
    use_default: bool = False


class Answers(Contract):
    answers: list[AnswerInput] = Field(min_length=1, max_length=3)


def validate_answers(batch: Clarification, submission: Answers) -> list[dict]:
    incoming = {answer.question_id: answer for answer in submission.answers}
    if set(incoming) != {question.id for question in batch.questions} or len(incoming) != len(submission.answers):
        raise ValueError("Submit one answer for every question")
    resolved = []
    for question in batch.questions:
        answer = incoming[question.id]
        text = answer.text.strip()
        ids = answer.option_ids
        if isinstance(question, Choice):
            options = {value.id: value.label for value in question.options}
            if answer.use_default and question.recommended_option_id:
                ids = [question.recommended_option_id]
            limit = len(options) if isinstance(question, MultiChoice) else 1
            if len(set(ids)) != len(ids) or len(ids) > limit or not set(ids) <= options.keys():
                raise ValueError(f"Invalid options for {question.title}")
            if text and not question.allow_custom_answer:
                raise ValueError(f"Custom text is unavailable for {question.title}")
            values = [options[value] for value in ids]
            if text:
                values.append(text)
        else:
            if ids:
                raise ValueError(f"Invalid answer for {question.title}")
            if isinstance(question, SlideCount):
                text = str(question.recommended_count) if answer.use_default else text
                if text and (not text.isdecimal() or not 1 <= int(text) <= 30):
                    raise ValueError("Slide count must be between 1 and 30")
            elif answer.use_default:
                text = question.recommended_text or text
            values = [text] if text else []
        if not values and question.required and not answer.use_default:
            raise ValueError(f"An answer is required for {question.title}")
        resolved.append({"question_id": question.id, "title": question.title, "option_ids": ids,
                         "text": text, "value": "; ".join(values) or "Use a recommended default"})
    return resolved
