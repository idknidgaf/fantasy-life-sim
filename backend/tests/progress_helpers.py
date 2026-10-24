"""Shared builders for the NPC progression, family, surname and API tests."""
from sqlmodel import Session, create_engine
from sqlmodel.pool import StaticPool

from app.content import get_content
from app.db import init_db
from app.models import Campaign
from app.npc import NpcFactory, NpcSpec
from app.progression import GrowthConfig

CT = get_content()
FACTORY = NpcFactory()


def new_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(engine)
    with Session(engine) as s:
        s.add_all([Campaign(name="one", seed=1), Campaign(name="two", seed=2)])
        s.commit()
    return engine


def growth_config(*, stat_chance=1.0, small=(1, 1), breakthrough_chance=0.0, percent=(0.5, 0.5), max_amount=10,
                  max_per_stat=2, specialised_chance=0.0, decline=None, cap=85, taper_from=0.7, **extra) -> GrowthConfig:
    """A growth config with ONE profile for every stage and fixed dice-free numbers, for deterministic tests."""
    profile = {"stages": CT.stage_ids, "stat_chance": stat_chance, "small": list(small),
               "breakthrough": {"chance": breakthrough_chance, "percent": list(percent), "max_amount": max_amount,
                                "max_per_stat": max_per_stat},
               "specialised": {"chance": specialised_chance, "gain": [1.0, 1.0]}}
    if decline:
        profile["decline"] = decline
    return GrowthConfig.model_validate({"profiles": {"all": profile}, "caps": {"default": cap, "taper_from": taper_from},
                                        "trait_acquisition": {"enabled": False}, **extra})


def person(species="human", age=30, seed=1, **kw):
    spec = NpcSpec(category="persistent", species=species, age_range=(age, age), **kw)
    return FACTORY.generate(spec, seed=seed)
