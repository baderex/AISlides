"""Shared deck direction, projected into the units used by each compositor."""
from dataclasses import dataclass

from app.domain import Contract, DesignRules, MIN_LABEL_PT
from app.palette import categorical_palette, semantic_palette
from app.render_geometry import available_family, fallback_family


@dataclass(frozen=True)
class Family:
    heading: str
    body: str
    spacing: str
    cover: str
    grid: str
    imagery: str


FAMILIES = {
    "corporate": Family("Calibri", "Arial", "regular", "panel", "Align titles and evidence to a common left edge.",
                        "Use a single contextual photograph or a directly labeled diagram."),
    "consulting": Family("Arial", "Arial", "compact", "rules", "Use an assertion headline over a full-width exhibit.",
                         "Prefer labeled comparisons and restrained rules to decorative imagery."),
    "editorial": Family("Source Serif 4", "Manrope", "spacious", "rules", "Use asymmetric columns and a generous outer margin.",
                        "Crop one documentary photograph around its subject and keep the caption close."),
    "technology": Family("Manrope", "Manrope", "regular", "panel", "Align modules on a grid with clear flow direction.",
                         "Use consistent line icons, orthogonal connectors and direct labels."),
    "financial": Family("Cambria", "Arial", "compact", "rules", "Right-align numbers and reserve one accent for the finding.",
                        "Use comparable axes and compact tables on quiet backgrounds."),
    "product": Family("Arial Black", "Calibri", "spacious", "panel", "Pair a short headline with one dominant product view.",
                      "Crop product photography around the feature and keep labels outside the subject."),
}


def design_family(direction, mode, skill=None):
    text = direction.casefold()
    if skill:
        text += " " + skill.category.casefold() + " " + skill.name.casefold()
    for family, words in (("consulting", ("consulting",)), ("financial", ("financ", "reporting", "data driven", "kpi")),
                          ("editorial", ("editorial", "luxury")), ("technology", ("techn",)),
                          ("product", ("product", "launch", "creative", "pitch"))):
        if any(word in text for word in words):
            return family
    return "product" if mode == "creative" else "corporate"


def baseline_rules(theme, family):
    preset = FAMILIES[family]
    series = categorical_palette(theme, theme.background)
    return DesignRules(family=family, audience="General audience", purpose="Communicate the brief", industry="General",
                       mood=family, brand_constraints="", visual_references=preset.grid, imagery=preset.imagery,
                       heading_font=preset.heading, spacing=preset.spacing, card_shape="rect", cover_style=preset.cover,
                       cover_background=theme.background, dense_surface=theme.surface, dense_ink=theme.ink,
                       chart_colors=series if len(series) >= 2 else series * 2)


class TypeRole(Contract):
    family: str
    target_pt: int
    minimum_pt: int
    bold: bool
    leading: float


class DeckDesignSpec(Contract):
    family: str
    typography: dict[str, TypeRole]
    palette: dict[str, str]
    margin_px: int
    gap_px: int
    grid: str
    imagery: str
    cover_style: str
    card_shape: str

    @classmethod
    def from_theme(cls, theme, aspect, layout=None):
        rules = theme.design or baseline_rules(theme, "consulting" if theme.profile == "consulting" else "corporate")
        preset = FAMILIES[rules.family]
        margin, gap = {"compact": (32, 12), "regular": (42, 18), "spacious": (54, 24)}[rules.spacing]
        dense = layout in {"research", "metrics", "chart"} or rules.spacing == "compact"
        compact = dense or aspect == "1:1"
        roles = {"cover": (rules.heading_font, 44 if compact else 52, 36, True, 1.08),
                 "headline": (rules.heading_font, 30 if compact else 36, 26, True, 1.12),
                 "body": (theme.font, 18 if compact else 21, 16 if compact else 18, False, 1.2),
                 "label": (theme.font, 14 if compact else 16, MIN_LABEL_PT, False, 1.15),
                 "table": (theme.font, 13 if dense else 15, 11 if compact else 13, False, 1.18),
                 "footnote": (theme.font, 10 if compact else 11, 10, False, 1.15),
                 "code": (theme.font if theme.brand_kit and "font" in theme.brand_kit.protected else "IBM Plex Mono",
                          14 if compact else 16, 12, False, 1.2)}
        typography = {name: TypeRole(family=f, target_pt=t, minimum_pt=m, bold=b, leading=l)
                      for name, (f, t, m, b, l) in roles.items()}
        bg = rules.cover_background if layout in {"cover", "closing", "section", "quote"} else theme.background
        return cls(family=rules.family, typography=typography, palette=semantic_palette(theme, bg),
                   margin_px=round(margin * (.8 if aspect == "1:1" else 1)), gap_px=gap,
                   grid=preset.grid, imagery=rules.imagery, cover_style=rules.cover_style, card_shape=rules.card_shape)


def resolve_fonts(theme):
    """Keep explicitly requested and protected faces, and resolve inferred faces on this worker."""
    constraints, kit = theme.constraints, theme.brand_kit
    for model, field, pinned in ((theme, "font", constraints and constraints.body_font),
                                 (theme.design, "heading_font", constraints and constraints.heading_font)):
        requested = getattr(model, field)
        if not pinned and not (kit and field in kit.protected):
            setattr(model, field, available_family(requested) or fallback_family(requested))
