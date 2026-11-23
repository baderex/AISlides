import re

from app.domain import DesignRules, Theme


CONSULTING_DIRECTION = "Strategy consulting"
CONSULTING_ICON_LAYOUTS = frozenset({"summary", "metrics", "showcase", "editorial", "timeline", "process", "closing"})
FIRM = r"(?:bcg|boston consulting group)"
STYLE = r"(?:style|inspired|format|look|aesthetic|visuals?|layout|template|design|like)"


def requested_consulting(prompt: str) -> bool:
    return bool(re.search(rf"\b{FIRM}(?:['’]s)?[\s-]+{STYLE}\b|\b(?:{STYLE})\s+(?:of\s+)?{FIRM}\b",
                          prompt, re.IGNORECASE))


def consulting_theme() -> Theme:
    theme = Theme(profile="consulting", background="#FFFFFF", ink="#16251E", accent="#006841",
                  muted="#4D6056", surface="#EDF4EF", font="Arial")
    theme.design = consulting_rules(theme)
    return theme


def consulting_rules(theme: Theme) -> DesignRules:
    return DesignRules(family="consulting", audience="Decision makers", purpose="Present evidence and a recommendation",
                       industry="General", mood="Strategy consulting", brand_constraints="", visual_references="",
                       imagery="Use imagery where it supports the evidence.", heading_font=theme.font, spacing="compact",
                       card_shape="rect", cover_style="rules", cover_background=theme.background,
                       dense_surface=theme.surface, dense_ink=theme.ink, chart_colors=[theme.accent, theme.muted])


PLAN_GUIDANCE = (
    "Use a strategy consulting storyline. After the cover, write each headline as a concise conclusion or recommendation "
    "rather than a subject label. Begin with an executive answer, compare the actual markets or alternatives in an exhibit, "
    "and finish with a decision and next steps. Use comparison or research layouts for matrices. "
    "For a country landscape name the countries and compare them on common dimensions. "
    "Use neutral, unbranded copy and distinguish qualitative hypotheses from evidence."
)
SLIDE_GUIDANCE = (
    "For comparison or research, prefer an editable table with a short header row and up to six concise data rows. "
    "Use comparable dimensions and identify the actual markets or alternatives. For a qualitative exhibit, clearly label "
    "hypotheses and leave unsupported metrics out. Use the subtitle for the context or scope. "
    "For a closing slide, express the actions and how to validate or sequence them."
)
