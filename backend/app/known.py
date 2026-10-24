"""Who the player's family has met. A persistent record (models.KnownNpc), never "every NPC the game generated".

An NPC is KNOWN to a campaign when either
  * it has a KnownNpc row: `record_known` was called when it was met (a story introduced it, an encounter was
    resolved, a child was born into the family), or
  * it is linked by any relationship to the current playable character (family, mentor, friend...).
Generating or saving an NPC does not make it known, and a temporary (generic) NPC never is.

Known-ness belongs to the campaign, not to one playable life, so an heir still knows who their parent knew. Nothing here
commits: like npc_store, the caller owns the transaction. Recording twice is harmless.
"""
from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from .models import KnownNpc
from .models import CharacterRelationship
from .npc import CharacterCategory
from .npc_store import world_year_of
from .relationships import PLAYER_PREFIX

SOURCES = ("encounter", "story", "birth", "manual")


def record_known(session: Session, campaign_id: int, npc_id: str, source: str = "encounter") -> KnownNpc:
    """Remember that the family has met this NPC. Idempotent: the first source and year are kept."""
    if source not in SOURCES:
        raise ValueError(f"unknown source '{source}' (use {list(SOURCES)})")
    if npc_id.startswith(PLAYER_PREFIX):
        raise ValueError("the playable character is not an NPC")
    existing = _find(session, campaign_id, npc_id)
    if existing is not None:
        return existing
    row = KnownNpc(campaign_id=campaign_id, npc_id=npc_id, source=source, first_met_year=world_year_of(session, campaign_id))
    try:
        with session.begin_nested():                 # a lost race must not undo the caller's work
            session.add(row)
            session.flush()
    except IntegrityError:
        return _find(session, campaign_id, npc_id) or row           # pragma: no cover
    return row


def _find(session: Session, campaign_id: int, npc_id: str) -> KnownNpc | None:
    return session.exec(select(KnownNpc).where(KnownNpc.campaign_id == campaign_id, KnownNpc.npc_id == npc_id)).first()


def is_recorded(session: Session, campaign_id: int, npc_id: str) -> bool:
    """Has this NPC been recorded as met (a KnownNpc row)? Relationship links are not looked at."""
    return _find(session, campaign_id, npc_id) is not None


def is_known(session: Session, campaign_id: int, player: str, npc_id: str) -> bool:
    if _find(session, campaign_id, npc_id) is not None:
        return True
    return session.exec(select(CharacterRelationship.id).where(
        CharacterRelationship.campaign_id == campaign_id,
        or_(CharacterRelationship.source_id == player, CharacterRelationship.target_id == player),
        or_(CharacterRelationship.source_id == npc_id, CharacterRelationship.target_id == npc_id))).first() is not None


def known_ids(session: Session, campaign_id: int, player: str) -> list[str]:
    """Every id the family knows (not yet checked against existing characters), in a stable order."""
    recorded = list(session.exec(select(KnownNpc.npc_id).where(KnownNpc.campaign_id == campaign_id).order_by(KnownNpc.id)))
    ends = session.exec(select(CharacterRelationship.source_id, CharacterRelationship.target_id).where(
        CharacterRelationship.campaign_id == campaign_id,
        or_(CharacterRelationship.source_id == player, CharacterRelationship.target_id == player)).order_by(CharacterRelationship.id))
    linked = [e for pair in ends for e in pair if e != player]
    return list(dict.fromkeys([*recorded, *linked]))


def met_sources(session: Session, campaign_id: int, npc_ids: list[str]) -> dict[str, KnownNpc]:
    if not npc_ids:
        return {}
    rows = session.exec(select(KnownNpc).where(KnownNpc.campaign_id == campaign_id, col(KnownNpc.npc_id).in_(npc_ids)))
    return {r.npc_id: r for r in rows}


GENERIC = CharacterCategory.GENERIC.value
