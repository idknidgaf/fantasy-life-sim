"""Saving, reading and dismissing battle summaries. No commits happen here: the caller owns the transaction, so a
fight's consequences and its record are saved together or not at all."""
from __future__ import annotations

from sqlalchemy import update
from sqlmodel import Session, select

from .battle import BattleSummary
from .models import CharacterRecord, CombatRecord


def store_battle(session: Session, rec: CharacterRecord, summary: BattleSummary, *, choice_id: str,
                 instance_id: int | None = None) -> CombatRecord:
    """Persist a resolved fight and give the summary its id. Needs rec.id (the character must be saved)."""
    row = CombatRecord(
        campaign_id=rec.campaign_id, character_id=rec.id, source=summary.source, event_id=summary.event_id,
        instance_id=instance_id, choice_id=choice_id, age=rec.age, outcome=summary.outcome,
        flee_attempted=summary.flee is not None, flee_succeeded=summary.flee.succeeded if summary.flee else None,
        summary={})
    session.add(row)
    session.flush()
    summary.id = row.id
    row.summary = summary.model_dump(mode="json")
    session.add(row)
    session.flush()
    return row


def pending_battle(session: Session, rec: CharacterRecord) -> BattleSummary | None:
    """The newest summary the player has not dismissed yet, if any."""
    row = session.exec(
        select(CombatRecord)
        .where(CombatRecord.character_id == rec.id, CombatRecord.acknowledged == False)  # noqa: E712
        .order_by(CombatRecord.id.desc())
    ).first()
    return BattleSummary.model_validate(row.summary) if row else None


def acknowledge_battle(session: Session, rec: CharacterRecord, battle_id: int) -> bool:
    """Dismiss one summary. Idempotent. False if there is no such record for this character."""
    row = session.get(CombatRecord, battle_id)
    if row is None or row.character_id != rec.id:
        return False
    if not row.acknowledged:
        row.acknowledged = True
        session.add(row)
    return True


def acknowledge_all(session: Session, rec: CharacterRecord) -> None:
    """Dismiss every open summary. Moving on (acting, aging, deciding another event) means the player has moved
    past the last fight, so a stale summary never reappears later."""
    session.execute(
        update(CombatRecord)
        .where(CombatRecord.character_id == rec.id, CombatRecord.acknowledged == False)  # noqa: E712
        .values(acknowledged=True)
    )
