"""Database reads and writes for story state. No commits happen here: the caller owns the transaction."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, or_
from sqlmodel import select

from ..models import OPEN_STATUSES, StoryChoiceLog, StoryEventInstance, StoryFlag
from .context import StoryContext
from .scalars import Scalar


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- flags

def get_flag(ctx: StoryContext, key: str) -> Scalar | None:
    row = ctx.session.exec(
        select(StoryFlag).where(StoryFlag.character_id == ctx.character_id, StoryFlag.key == key)
    ).first()
    return row.value if row else None


def set_flag(ctx: StoryContext, key: str, value: Scalar) -> None:
    row = ctx.session.exec(
        select(StoryFlag).where(StoryFlag.character_id == ctx.character_id, StoryFlag.key == key)
    ).first()
    if row is None:
        row = StoryFlag(campaign_id=ctx.campaign_id, character_id=ctx.character_id, key=key, value=value)
    row.value = value
    row.updated_year = ctx.world_year
    ctx.session.add(row)
    ctx.session.flush()


def all_flags(ctx: StoryContext) -> dict[str, Scalar]:
    rows = ctx.session.exec(select(StoryFlag).where(StoryFlag.character_id == ctx.character_id)).all()
    return {r.key: r.value for r in sorted(rows, key=lambda r: r.key)}


# ---------------------------------------------------------------- history queries

def event_completed(ctx: StoryContext, event_id: str) -> bool:
    return ctx.session.exec(
        select(StoryEventInstance.id).where(
            StoryEventInstance.character_id == ctx.character_id,
            StoryEventInstance.event_id == event_id,
            StoryEventInstance.status == "resolved",
        )
    ).first() is not None


def choice_made(ctx: StoryContext, event_id: str, choice_id: str) -> bool:
    return ctx.session.exec(
        select(StoryChoiceLog.id).where(
            StoryChoiceLog.character_id == ctx.character_id,
            StoryChoiceLog.event_id == event_id,
            StoryChoiceLog.choice_id == choice_id,
        )
    ).first() is not None


def open_instance(ctx: StoryContext, event_id: str) -> StoryEventInstance | None:
    return ctx.session.exec(
        select(StoryEventInstance).where(
            StoryEventInstance.character_id == ctx.character_id,
            StoryEventInstance.event_id == event_id,
            StoryEventInstance.status.in_(OPEN_STATUSES),
        )
    ).first()


def _counting(stmt):
    """Instances that count as occurrences: everything except ones dropped by a content update."""
    return stmt.where(or_(StoryEventInstance.status != "expired",
                          StoryEventInstance.end_reason != "version_changed"))


def occurrences(ctx: StoryContext, event_id: str, *, whole_campaign: bool = False) -> int:
    stmt = select(func.count()).select_from(StoryEventInstance).where(StoryEventInstance.event_id == event_id)
    stmt = stmt.where(StoryEventInstance.campaign_id == ctx.campaign_id) if whole_campaign \
        else stmt.where(StoryEventInstance.character_id == ctx.character_id)
    return ctx.session.exec(_counting(stmt)).one()


def last_occurrence_year(ctx: StoryContext, event_id: str) -> int | None:
    stmt = select(func.max(StoryEventInstance.created_year)).where(
        StoryEventInstance.character_id == ctx.character_id, StoryEventInstance.event_id == event_id)
    return ctx.session.exec(_counting(stmt)).one()


# ---------------------------------------------------------------- instances

def create_instance(ctx: StoryContext, definition, *, origin: str,
                    parent_instance_id: int | None = None) -> StoryEventInstance:
    expires = definition.lifecycle.expires_after_years
    inst = StoryEventInstance(
        campaign_id=ctx.campaign_id, character_id=ctx.character_id,
        event_id=definition.id, event_version=definition.version, status="pending", origin=origin,
        parent_instance_id=parent_instance_id, created_year=ctx.world_year,
        expires_year=None if expires is None else ctx.world_year + expires,
    )
    ctx.session.add(inst)
    ctx.session.flush()
    return inst


def list_open(ctx: StoryContext) -> list[StoryEventInstance]:
    return list(ctx.session.exec(
        select(StoryEventInstance)
        .where(StoryEventInstance.character_id == ctx.character_id,
               StoryEventInstance.status.in_(OPEN_STATUSES))
        .order_by(StoryEventInstance.id)
    ).all())


def mark_presented(ctx: StoryContext, inst: StoryEventInstance) -> None:
    if inst.status == "pending":
        inst.status = "presented"
        inst.presented_year = ctx.world_year
        inst.presented_at = _now()
        ctx.session.add(inst)
        ctx.session.flush()


def list_instances(ctx: StoryContext, limit: int = 50) -> list[StoryEventInstance]:
    return list(ctx.session.exec(
        select(StoryEventInstance).where(StoryEventInstance.character_id == ctx.character_id)
        .order_by(StoryEventInstance.id.desc()).limit(limit)
    ).all())
