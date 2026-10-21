"""Saving and loading NPCs. Like battle_store, nothing here commits: the caller owns the transaction.

What is kept, and what is not:

  persistent / named   saved by `save_npc`. Every section is settled first (generated and stored), so a recurring
                       character can never change when the generation rules or species data are tuned later.
  generic (temporary)  NOT saved automatically. If one must survive a reload or another gameplay boundary (an
                       unresolved encounter), `keep_temporary_npc` stores it as it is, which for a light NPC is
                       little more than its identity and seed. `discard_temporary` clears them afterwards, and
                       `NpcFactory.promote` plus `save_npc` turns one into a recurring character, keeping its id.

A saved NPC holds no Power: it is always calculated from the saved skills and traits (npc.npc_power).
Relationships live in relationships.py; deleting an NPC here removes its links too.
"""
from __future__ import annotations

from typing import Iterable

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from .models import CharacterRelationship, NpcRecord
from .npc import CharacterCategory, LifecycleStatus, Npc, ensure_all
from .relationships import remove_all_for

GENERIC = CharacterCategory.GENERIC.value


class TemporaryNpcError(ValueError):
    """Generic NPCs are not saved by save_npc."""


class DemotionError(ValueError):
    """A kept character cannot go back to being temporary."""


def _fill_columns(rec: NpcRecord, npc: Npc) -> None:
    rec.category, rec.status = npc.category.value, npc.status.value
    rec.species_id, rec.name, rec.age = npc.species_id, npc.name, npc.age
    rec.data = npc.model_dump(mode="json")                 # a fresh object, so SQLAlchemy sees the change


def _find(session: Session, campaign_id: int, npc_id: str) -> NpcRecord | None:
    return session.exec(select(NpcRecord).where(NpcRecord.campaign_id == campaign_id,
                                                NpcRecord.npc_id == npc_id)).first()


def _upsert(session: Session, campaign_id: int, npc: Npc) -> NpcRecord:
    """Insert, or update the row with the same npc_id. Safe to repeat, and safe if two writers race: the loser
    hits the unique constraint inside a savepoint and falls back to updating the winner's row."""
    rec = _find(session, campaign_id, npc.id)
    if rec is not None and rec.category != GENERIC and npc.category == CharacterCategory.GENERIC:
        raise DemotionError(f"{rec.name} is a {rec.category} character and cannot become temporary again")
    if rec is None:
        rec = NpcRecord(campaign_id=campaign_id, npc_id=npc.id, category="", status="", species_id="", name="")
        _fill_columns(rec, npc)
        try:
            with session.begin_nested():
                session.add(rec)
                session.flush()
            return rec
        except IntegrityError:
            if rec in session:                      # rolling the savepoint back normally detaches it already
                session.expunge(rec)
            rec = _find(session, campaign_id, npc.id)
            if rec is None:                                                       # pragma: no cover
                raise
    _fill_columns(rec, npc)
    session.add(rec)
    session.flush()
    return rec


def save_npc(session: Session, campaign_id: int, npc: Npc) -> NpcRecord:
    """Keep a persistent or named character. Its generated sections are settled first, so what is saved is
    complete and fixed. Saving the same character again updates it; the id never changes."""
    if npc.category == CharacterCategory.GENERIC:
        raise TemporaryNpcError(f"{npc.name} is a generic NPC; promote it to persistent to keep it, "
                                "or use keep_temporary_npc if it only has to survive an encounter")
    ensure_all(npc)
    return _upsert(session, campaign_id, npc)


def keep_temporary_npc(session: Session, campaign_id: int, npc: Npc) -> NpcRecord:
    """Store a generic NPC across a reload or other boundary. Nothing extra is generated: a light NPC stays light
    and is rebuilt from its seed. Clear these with discard_temporary when the encounter is over."""
    if npc.category != CharacterCategory.GENERIC:
        raise ValueError(f"{npc.name} is {npc.category.value}; use save_npc")
    return _upsert(session, campaign_id, npc)


def load_npc(session: Session, campaign_id: int, npc_id: str) -> Npc | None:
    rec = _find(session, campaign_id, npc_id)
    return Npc.model_validate(rec.data) if rec else None


def load_npcs(session: Session, campaign_id: int, npc_ids: Iterable[str]) -> list[Npc]:
    """Many NPCs in one query, in the order asked. Ids that are missing (or not NPCs) are skipped."""
    ids = list(dict.fromkeys(npc_ids))
    if not ids:
        return []
    rows = {r.npc_id: r for r in session.exec(select(NpcRecord).where(
        NpcRecord.campaign_id == campaign_id, col(NpcRecord.npc_id).in_(ids)))}
    return [Npc.model_validate(rows[i].data) for i in ids if i in rows]


def list_npcs(session: Session, campaign_id: int, *, category: CharacterCategory | None = None,
              status: LifecycleStatus | None = None, species_id: str | None = None,
              include_temporary: bool = False) -> list[Npc]:
    """Saved NPCs, oldest first. Temporary ones are left out unless asked for (or `category` is generic)."""
    query = select(NpcRecord).where(NpcRecord.campaign_id == campaign_id).order_by(NpcRecord.id)
    if category is not None:
        query = query.where(NpcRecord.category == category.value)
    elif not include_temporary:
        query = query.where(NpcRecord.category != GENERIC)
    if status is not None:
        query = query.where(NpcRecord.status == status.value)
    if species_id is not None:
        query = query.where(NpcRecord.species_id == species_id)
    return [Npc.model_validate(r.data) for r in session.exec(query)]


def list_temporary(session: Session, campaign_id: int) -> list[Npc]:
    return list_npcs(session, campaign_id, category=CharacterCategory.GENERIC)


def delete_npc(session: Session, campaign_id: int, npc_id: str) -> bool:
    """Remove a saved NPC and every relationship it is part of."""
    rec = _find(session, campaign_id, npc_id)
    if rec is None:
        return False
    remove_all_for(session, campaign_id, npc_id)
    session.delete(rec)
    session.flush()
    return True


def discard_temporary(session: Session, campaign_id: int, npc_ids: Iterable[str] | None = None) -> int:
    """Delete kept temporary NPCs (all of them, or just these ids). Never touches persistent or named characters."""
    query = delete(NpcRecord).where(NpcRecord.campaign_id == campaign_id, NpcRecord.category == GENERIC)
    if npc_ids is not None:
        query = query.where(col(NpcRecord.npc_id).in_(list(npc_ids)))
    removed = session.exec(query).rowcount or 0
    session.flush()
    return removed


def delete_campaign_npcs(session: Session, campaign_id: int) -> None:
    """Everything NPC-related that belongs to a campaign: its characters and their relationships."""
    for table in (CharacterRelationship, NpcRecord):
        session.exec(delete(table).where(table.campaign_id == campaign_id))
    session.flush()
