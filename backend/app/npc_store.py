"""Saving and loading NPCs. Like battle_store, nothing here commits: the caller owns the transaction.

Only persistent and named characters are saved. A generic NPC is temporary: it is rebuilt from its seed whenever
it is needed, so saving it would only add clutter (promote it first with NpcFactory.promote if it should stay).
"""
from __future__ import annotations

from sqlmodel import Session, select

from .models import NpcRecord
from .npc import CharacterCategory, LifecycleStatus, Npc


class TemporaryNpcError(ValueError):
    """Generic NPCs are not saved."""


def _fill_columns(rec: NpcRecord, npc: Npc) -> None:
    rec.category, rec.status = npc.category.value, npc.status.value
    rec.species_id, rec.name, rec.age = npc.species_id, npc.name, npc.age
    rec.data = npc.model_dump(mode="json")                 # a fresh object, so SQLAlchemy sees the change


def save_npc(session: Session, campaign_id: int, npc: Npc) -> NpcRecord:
    """Insert the NPC, or update it if this campaign already has one with the same id."""
    if npc.category == CharacterCategory.GENERIC:
        raise TemporaryNpcError(f"{npc.name} is a generic NPC; promote it to persistent to keep it")
    rec = _find(session, campaign_id, npc.id) or NpcRecord(campaign_id=campaign_id, npc_id=npc.id,
                                                          category="", status="", species_id="", name="")
    _fill_columns(rec, npc)
    session.add(rec)
    session.flush()
    return rec


def _find(session: Session, campaign_id: int, npc_id: str) -> NpcRecord | None:
    return session.exec(select(NpcRecord).where(NpcRecord.campaign_id == campaign_id,
                                                NpcRecord.npc_id == npc_id)).first()


def load_npc(session: Session, campaign_id: int, npc_id: str) -> Npc | None:
    rec = _find(session, campaign_id, npc_id)
    return Npc.model_validate(rec.data) if rec else None


def list_npcs(session: Session, campaign_id: int, *, category: CharacterCategory | None = None,
              status: LifecycleStatus | None = None, species_id: str | None = None) -> list[Npc]:
    query = select(NpcRecord).where(NpcRecord.campaign_id == campaign_id).order_by(NpcRecord.id)
    if category is not None:
        query = query.where(NpcRecord.category == category.value)
    if status is not None:
        query = query.where(NpcRecord.status == status.value)
    if species_id is not None:
        query = query.where(NpcRecord.species_id == species_id)
    return [Npc.model_validate(r.data) for r in session.exec(query)]


def delete_npc(session: Session, campaign_id: int, npc_id: str) -> bool:
    rec = _find(session, campaign_id, npc_id)
    if rec is None:
        return False
    session.delete(rec)
    session.flush()
    return True
