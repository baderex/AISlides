from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SkillRecord
from app.domain import Skill, Theme

SEEDS = [
    ("strategy", "Executive Strategy", "Boardroom", "Decision-ready narratives for leadership teams.", ["Strategy", "Executive"],
     "#eef1ff", "#191c43", "#4f50d5", "#4b5471", "#dce1ff", ["cover", "summary", "comparison", "closing"]),
    ("pitch", "Startup Pitch", "Fundraising", "Tell a compelling story from problem to traction.", ["Pitch deck", "Investors"],
     "#fff4ed", "#361c30", "#95274e", "#6b4357", "#f6dce5", ["cover", "showcase", "metrics", "closing"]),
    ("research", "Research Brief", "Academic", "Turn complex research into clear, structured insights.", ["Research", "Education"],
     "#e9f8f3", "#12372e", "#087d64", "#3b6054", "#c6eadd", ["cover", "research", "comparison", "summary"]),
    ("product", "Product Launch", "Product", "Introduce a product with a clear value proposition.", ["Product", "Marketing"],
     "#edebff", "#221648", "#6941c6", "#574980", "#dcd1ff", ["cover", "showcase", "process", "closing"]),
    ("data", "Performance Review", "Data and KPI", "Make trends, decisions and owners visible.", ["KPI", "Reporting"],
     "#eaf7ff", "#102b41", "#125c94", "#405767", "#cae8fa", ["cover", "metrics", "chart", "closing"]),
    ("education", "Technical Explainer", "Technical", "Explain unfamiliar systems one concept at a time.", ["Technical", "Education"],
     "#fff8e3", "#432d16", "#8d5509", "#695846", "#f6e3ac", ["cover", "process", "timeline", "closing"]),
]


def seed(db: Session):
    for key, name, category, description, tags, background, ink, accent, muted, surface, recipes in SEEDS:
        if db.get(SkillRecord, key):
            continue
        skill = Skill(name=name, description=description, category=category, tags=tags, preview=key,
                      recipes=recipes, instructions=description,
                      theme=Theme(background=background, ink=ink, accent=accent, muted=muted, surface=surface))
        db.add(SkillRecord(id=key, manifest=skill.model_dump(mode="json")))
    db.commit()


def list_skills(db: Session, owner_id: str) -> list[dict]:
    query = select(SkillRecord).where(
        (SkillRecord.owner_id == owner_id) | SkillRecord.owner_id.is_(None), SkillRecord.deleted.is_(False),
    )
    return [{"id": item.id, **item.manifest, "can_delete": item.owner_id == owner_id} for item in db.scalars(query)]
