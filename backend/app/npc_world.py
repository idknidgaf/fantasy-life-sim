"""The world's yearly tick for saved NPCs: ageing, and the growth that goes with each year.

One rule keeps it safe to repeat: an NPC remembers the world year its age is current for (`progress.aged_year`) and
growth is recorded per age (`progress.developed_age`). `advance_world(campaign, world_year)` therefore brings every
persistent NPC UP TO that year and no further. Calling it again for the same year, retrying a request or reloading a
save changes nothing; calling it for a later year applies exactly the years in between, once each.

Called from the playable character's Age Up (main._mutate), in the same transaction, right after the campaign's
`world_year` moves. It never touches the playable character, and the playable character's own age-up never calls
progression: the two only share the clock. Named characters are not handled here: they age by their lifecycle rule
(named.advance_all) and their authored stats do not grow. Read endpoints never call this.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, select

from .content import Content, get_content
from .models import NpcRecord
from .npc import CharacterCategory, LifecycleStatus, Npc, NpcProgress
from .npc_store import update_record
from .power import PowerConfig, get_power_config
from .progression import GrowthConfig, catch_up, get_growth_config


@dataclass(frozen=True)
class WorldSync:
    npc_id: str
    age_before: int
    age_after: int
    died: bool


def sync_npc_to_year(npc: Npc, world_year: int, ct: Content, gcfg: GrowthConfig, pcfg: PowerConfig) -> bool:
    """Bring one NPC's age and growth up to `world_year`. Returns whether anything changed. Pure (saves nothing)."""
    if npc.progress is None:                               # an older save: current as of now, nothing back-filled
        npc.progress = NpcProgress(developed_age=npc.age, aged_year=world_year)
        return True
    if npc.progress.aged_year is None:
        npc.progress.aged_year = world_year
        return True
    years = world_year - npc.progress.aged_year
    if years <= 0 or not npc.alive:
        return False
    lifespan = ct.species[npc.species_id].lifespan if npc.species_id in ct.species else None
    for _ in range(years):
        npc.age += 1
        npc.progress.aged_year += 1
        catch_up(npc, ct, gcfg, pcfg)
        if gcfg.aging.die_of_old_age and lifespan is not None and npc.age > lifespan:
            npc.mark_deceased("old age")
            break
    return True


def advance_world(session: Session, campaign_id: int, world_year: int, *, ct: Content | None = None,
                  gcfg: GrowthConfig | None = None, pcfg: PowerConfig | None = None) -> list[WorldSync]:
    """Age and grow every living persistent NPC of the campaign up to `world_year`, once per year. One query to read
    them, and a write only for the NPCs that changed. Returns who aged."""
    ct, gcfg, pcfg = ct or get_content(), gcfg or get_growth_config(), pcfg or get_power_config()
    rows = session.exec(select(NpcRecord).where(
        NpcRecord.campaign_id == campaign_id, NpcRecord.category == CharacterCategory.PERSISTENT.value,
        NpcRecord.status == LifecycleStatus.ACTIVE.value))
    out = []
    for rec in list(rows):
        npc = Npc.model_validate(rec.data)
        before = npc.age
        if sync_npc_to_year(npc, world_year, ct, gcfg, pcfg):
            update_record(rec, npc)
            session.add(rec)
            if npc.age != before:
                out.append(WorldSync(npc.id, before, npc.age, not npc.alive))
    session.flush()
    return out
