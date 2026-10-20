"""Effects: declarative state changes applied when a choice is selected.

Wire format: {"type": "set_flag", "flag": "met_the_baker", "value": true}

Handlers run inside the resolution transaction and never commit. Raising StoryEffectError aborts
the resolution and rolls everything back. Each handler returns a short dict describing what it did.
"""
from __future__ import annotations

from pydantic import Field

from ..rules import grant_trait
from . import store
from .context import StoryContext
from .errors import StoryEffectError
from .registry import Registry, StrictParams
from .scalars import Scalar, is_number

effects = Registry("effect")


class SetFlagParams(StrictParams):
    flag: str = Field(min_length=1, max_length=64)
    value: Scalar


class IncrementFlagParams(StrictParams):
    flag: str = Field(min_length=1, max_length=64)
    amount: int | float = 1


class GrantTraitParams(StrictParams):
    trait: str = Field(min_length=1)


class EventRefParams(StrictParams):
    event: str = Field(min_length=1)


@effects.register("set_flag", SetFlagParams)
def _set_flag(p: SetFlagParams, ctx: StoryContext) -> dict:
    """Set a story flag to a bool, number or string."""
    store.set_flag(ctx, p.flag, p.value)
    return {"type": "set_flag", "flag": p.flag, "value": p.value}


@effects.register("increment_flag", IncrementFlagParams)
def _increment_flag(p: IncrementFlagParams, ctx: StoryContext) -> dict:
    """Add amount (default 1) to a numeric flag. An unset flag starts at 0; a non-numeric flag is an error."""
    current = store.get_flag(ctx, p.flag)
    if current is None:
        current = 0
    if not is_number(current):
        raise StoryEffectError(f"cannot increment flag '{p.flag}': it holds the non-numeric value {current!r}")
    store.set_flag(ctx, p.flag, current + p.amount)
    return {"type": "increment_flag", "flag": p.flag, "value": current + p.amount}


@effects.register("grant_trait", GrantTraitParams)
def _grant_trait(p: GrantTraitParams, ctx: StoryContext) -> dict:
    """Give the character a trait. Reuses the game's trait rules: already owned or unmet prerequisites is a no-op."""
    if p.trait not in ctx.content.traits:
        raise StoryEffectError(f"unknown trait '{p.trait}'")
    character = ctx.record.to_character()
    granted = grant_trait(character, ctx.content, p.trait)
    if granted:
        ctx.record.traits = list(character.traits)          # fresh list so SQLAlchemy sees the change
        ctx.session.add(ctx.record)
        ctx.session.flush()
    return {"type": "grant_trait", "trait": p.trait, "granted": granted}


@effects.register("start_event", EventRefParams)
def _start_event(p: EventRefParams, ctx: StoryContext) -> dict:
    """Create a pending instance of another event. Its eligibility and lifecycle policy still apply."""
    from . import service                                  # service imports this module
    inst, reason = service.try_start(ctx, p.event, origin=f"start_event:{ctx.current_event_id}",
                                     parent_instance_id=ctx.current_instance_id)
    return {"type": "start_event", "event": p.event, "started": inst is not None, "skipped_reason": reason}


@effects.register("complete_event", EventRefParams)
def _complete_event(p: EventRefParams, ctx: StoryContext) -> dict:
    """Mark an event as completed without a choice (closes an open instance, or records a finished one)."""
    from . import service
    changed = service.complete_without_choice(ctx, p.event)
    return {"type": "complete_event", "event": p.event, "changed": changed}


def parse_effect(raw) -> dict:
    return effects.normalize(raw)


def apply_effect(effect: dict, ctx: StoryContext) -> dict:
    spec, params = effects.parse(effect)
    result = spec.handler(params, ctx)
    ctx.effect_log.append(result)
    return result
