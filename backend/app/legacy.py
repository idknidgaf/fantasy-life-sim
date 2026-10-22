"""Legacy: completing a life, finding the heirs, and carrying on as one of them.

Reuse, not duplication.

  * A completed life is the dead `CharacterRecord` itself. It already holds the identity, final attributes, history and
    cause of death, and the family links keep naming it (`pc_<id>`). A `LifeRecord` only marks it as completed (once)
    and remembers who carried on.
  * Heirs come from the relationship model (`relationships.eligible_descendants`). Nothing about inheritance changes.
  * The playable role is a row (`PlayableRole`) that points at an existing record. Switching is moving that pointer.
    The one thing that cannot be avoided is that the engine plays a `CharacterRecord`, while a descendant is an NPC:
    so the chosen heir is built into a record ONCE from the NPC's own saved data (`npc.to_character`), its family
    links are re-pointed at the new id, and the NPC row is retired, so exactly one record stands for that person.
    Nothing about them is rolled again.

Eligibility: a living descendant of the dead character (a child, grandchild and so on, nearest generation first) who is
a kept NPC with status `active`. Dead and retired characters, temporary NPCs, everyone who is not descended from them,
other playable lives and the dead character themselves are never offered. With no heir the game is over; nobody
unrelated is ever chosen.

Everything runs in the caller's transaction, except `continue_as`, which owns its own and rolls back as a whole.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import delete, update
from sqlmodel import Session, col, select

from .content import Content, get_content
from .models import OPEN_STATUSES, CharacterRecord, LifeRecord, NpcRecord, PlayableRole, StoryEventInstance
from .npc import Npc, npc_power, to_character
from .power import get_power_config
from .relationships import children_of, eligible_descendants, player_ref, rekey_character
from .rules import max_health
from .story import service as story_service


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- errors (each carries its HTTP status)

class LegacyError(Exception):
    status_code = 409


class NotDeadError(LegacyError):
    """The current character is still alive."""


class NoHeirsError(LegacyError):
    """Nobody is eligible: the line ends here."""


class ChoiceRequiredError(LegacyError):
    status_code = 400


class NotEligibleError(LegacyError):
    """That character is not one of the eligible descendants."""


class AlreadyContinuedError(LegacyError):
    """This life already went on as someone else."""


# ---------------------------------------------------------------- the playable role

def player_record(session: Session, campaign_id: int) -> CharacterRecord | None:
    """The character the player controls: the role's holder, or (no role yet) the campaign's newest record."""
    role = session.exec(select(PlayableRole).where(PlayableRole.campaign_id == campaign_id)).first()
    if role is not None:
        rec = session.get(CharacterRecord, role.character_id)
        if rec is not None and rec.campaign_id == campaign_id:
            return rec
    return session.exec(select(CharacterRecord).where(CharacterRecord.campaign_id == campaign_id)
                        .order_by(col(CharacterRecord.id).desc())).first()


def set_player(session: Session, campaign_id: int, character_id: int) -> None:
    role = session.exec(select(PlayableRole).where(PlayableRole.campaign_id == campaign_id)).first()
    if role is None:
        role = PlayableRole(campaign_id=campaign_id, character_id=character_id)
    role.character_id, role.changed_at = character_id, _now()
    session.add(role)
    session.flush()


# ---------------------------------------------------------------- a life is completed

def life_of(session: Session, character_id: int) -> LifeRecord | None:
    return session.exec(select(LifeRecord).where(LifeRecord.character_id == character_id)).first()


def complete_life(session: Session, rec: CharacterRecord) -> LifeRecord:
    """Record that this dead character's life is over. Safe to call again: the second call finds the first's record.

    Also closes the life's open story encounters (they belong to this life, which has ended), so nothing is left
    pending against a character who can no longer act. The history, combat records and choice log stay as they are."""
    if rec.alive:
        raise NotDeadError(f"{rec.name} is still alive")
    life = life_of(session, rec.id)
    if life is not None:
        return life
    session.exec(update(StoryEventInstance)
                 .where(StoryEventInstance.character_id == rec.id, col(StoryEventInstance.status).in_(OPEN_STATUSES))
                 .values(status="expired", end_reason="death", resolved_year=rec.age, resolved_at=_now()))
    life = LifeRecord(campaign_id=rec.campaign_id, character_id=rec.id)
    session.add(life)
    session.flush()
    return life


# ---------------------------------------------------------------- who can carry on

def eligible_heirs(session: Session, rec: CharacterRecord) -> list[Npc]:
    """The living, kept descendants of a dead character, nearest generation first. Empty while they are alive."""
    if rec.alive:
        return []
    return eligible_descendants(session, rec.campaign_id, player_ref(rec))


class HeirRead(BaseModel):
    npc_id: str
    name: str
    species_id: str
    species_name: str
    age: int
    relation: Literal["child", "descendant"]
    power: float
    traits: list[str]


class PastLifeRead(BaseModel):
    character_id: int
    name: str
    species_name: str
    age: int
    cause_of_death: str | None
    successor_name: str | None          # who carried on, None while undecided or if the line ended
    current: bool                       # the life the player is looking at right now


class LegacyRead(BaseModel):
    status: Literal["alive", "awaiting_heir", "game_over"]
    heirs: list[HeirRead]
    lives: list[PastLifeRead]           # completed lives of this campaign, oldest first


def _heir_read(session: Session, rec: CharacterRecord, npc: Npc, children: set[str], ct: Content) -> HeirRead:
    traits = npc.traits if npc.traits is not None else to_character(npc, ct).traits
    return HeirRead(
        npc_id=npc.id, name=npc.name, species_id=npc.species_id,
        species_name=ct.species[npc.species_id].name if npc.species_id in ct.species else npc.species_id,
        age=npc.age, relation="child" if npc.id in children else "descendant", power=npc_power(npc).total,
        traits=[ct.traits[t].name for t in traits if t in ct.traits])


def legacy_state(session: Session, rec: CharacterRecord) -> LegacyRead:
    """Where the line stands. For a dead character this also makes sure their life is recorded (a save from before
    legacy existed may hold a dead character nobody has processed), so the caller should commit."""
    ct = get_content()
    if not rec.alive:
        complete_life(session, rec)
    heirs = eligible_heirs(session, rec)
    children = set(children_of(session, rec.campaign_id, player_ref(rec))) if not rec.alive else set()

    lives = list(session.exec(select(LifeRecord).where(LifeRecord.campaign_id == rec.campaign_id)
                              .order_by(LifeRecord.id)))
    ids = {x for life in lives for x in (life.character_id, life.successor_character_id) if x is not None}
    people = {r.id: r for r in session.exec(select(CharacterRecord).where(col(CharacterRecord.id).in_(ids)))} if ids else {}
    past = []
    for life in lives:
        dead = people.get(life.character_id)
        if dead is None:
            continue
        successor = people.get(life.successor_character_id) if life.successor_character_id else None
        past.append(PastLifeRead(
            character_id=dead.id, name=dead.name,
            species_name=ct.species[dead.species_id].name if dead.species_id in ct.species else dead.species_id,
            age=dead.age, cause_of_death=dead.cause_of_death,
            successor_name=successor.name if successor else None, current=dead.id == rec.id))

    status = "alive" if rec.alive else ("awaiting_heir" if heirs else "game_over")
    return LegacyRead(status=status, heirs=[_heir_read(session, rec, h, children, ct) for h in heirs], lives=past)


# ---------------------------------------------------------------- carrying on as an heir

def _record_from_npc(npc: Npc, dead: CharacterRecord, ct: Content) -> CharacterRecord:
    """The playable record for an heir, from the heir's own saved data. Identity, species, age, base stats, skills,
    traits and talent are copied exactly as stored; only what an NPC has no field for is filled in, in a fixed way:
    personality axes start at the species' bias (no roll), the home is picked as a newborn's is (same seeded stream),
    health starts full, and the history starts with how this life began."""
    c = to_character(npc, ct, get_power_config())
    sp = ct.species[c.species_id]
    c.alive, c.cause_of_death = True, None
    c.axes = {a: max(-100, min(100, sp.axis_bias.get(a, 0))) for a in ct.axes}
    c.location = (random.Random(f"{npc.seed}:birth:location").choice(sp.habitats)
                  if npc.seed is not None else sp.habitats[0])
    c.health = max_health(c, ct)
    c.history = [{"age": c.age, "kind": "legacy", "event_id": f"legacy:{dead.id}",
                  "text": f"{c.name} carries on the family's story after the death of {dead.name}."}]
    return CharacterRecord.from_character(dead.campaign_id, c)


def continue_as(session: Session, rec: CharacterRecord, npc_id: str | None = None) -> CharacterRecord:
    """Carry on as an eligible descendant of the dead character `rec` (the current playable one). Returns the record
    now playable. `npc_id` may be left out when exactly one heir is eligible.

    One transaction: either the new character is playing and everything is saved, or nothing at all changed.
    Repeating a request that already succeeded returns the same character and changes nothing; asking for someone else
    after a life has gone on is refused."""
    try:
        result = _continue_as(session, rec, npc_id)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


def _continue_as(session: Session, rec: CharacterRecord, npc_id: str | None) -> CharacterRecord:
    if rec.alive:
        done = session.exec(select(LifeRecord).where(LifeRecord.campaign_id == rec.campaign_id,
                                                     LifeRecord.successor_character_id == rec.id)).first()
        if done is None:
            raise NotDeadError(f"{rec.name} is still alive")
        if npc_id is not None and npc_id != done.successor_npc_id:
            raise AlreadyContinuedError(f"This life already went on as {rec.name}")
        return rec                                              # a repeat of what already happened

    ct = get_content()
    life = complete_life(session, rec)
    heirs = eligible_heirs(session, rec)
    if npc_id is None:
        if not heirs:
            raise NoHeirsError(f"{rec.name} has no eligible descendants: the line ends here")
        if len(heirs) > 1:
            raise ChoiceRequiredError("Several descendants are eligible; choose one")
        heir = heirs[0]
    else:
        heir = next((h for h in heirs if h.id == npc_id), None)
        if heir is None:
            raise NotEligibleError(f"'{npc_id}' is not an eligible descendant of {rec.name}")

    # Claim the life atomically: of two requests that race, only one can flip it from open to continued.
    claimed = session.execute(update(LifeRecord)
                              .where(LifeRecord.id == life.id, col(LifeRecord.successor_npc_id).is_(None))
                              .values(successor_npc_id=heir.id, resolved_at=_now()))
    if claimed.rowcount != 1:
        raise AlreadyContinuedError(f"{rec.name}'s life has already gone on")
    session.refresh(life)

    new = _record_from_npc(heir, rec, ct)
    session.add(new)
    session.flush()                                             # the new record's id
    rekey_character(session, rec.campaign_id, heir.id, player_ref(new))     # the family goes with the person
    session.exec(delete(NpcRecord).where(NpcRecord.campaign_id == rec.campaign_id, NpcRecord.npc_id == heir.id))
    life.successor_character_id = new.id
    session.add(life)
    set_player(session, rec.campaign_id, new.id)
    # A new life begins: offer anything already eligible, in this same transaction (as at the start of a campaign).
    story_service.run_scans(story_service.make_context(session, new), [("age", {}), ("state", {})])
    return new
