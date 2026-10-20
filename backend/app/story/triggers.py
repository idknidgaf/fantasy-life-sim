"""Trigger types: decide whether an event should be offered when the game reports something happened.

An event's `trigger` is {"type": ..., **params}. Whoever drives the game calls
service.scan_triggers(channel, payload) when something happens ("age" after a birthday, "action" after
an action, "state" whenever state may have changed); events whose trigger type matches that channel
and whose trigger parameters match the payload are then checked for eligibility and offered.
"""
from __future__ import annotations

from pydantic import Field, model_validator

from .context import StoryContext
from .registry import Registry, StrictParams

triggers = Registry("trigger")


class NoParams(StrictParams):
    pass


class AgeTriggerParams(StrictParams):
    min_age: int = Field(ge=0)
    max_age: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _ordered(self):
        if self.max_age is not None and self.max_age < self.min_age:
            raise ValueError("max_age must be >= min_age")
        return self


class ActionTriggerParams(StrictParams):
    action: str = Field(min_length=1)


@triggers.register("manual", NoParams)
def _manual(p: NoParams, ctx: StoryContext, payload: dict) -> bool:
    """Never fires by itself; only started explicitly (API call or a start_event effect)."""
    return False


@triggers.register("age", AgeTriggerParams)
def _age(p: AgeTriggerParams, ctx: StoryContext, payload: dict) -> bool:
    """Fires on an 'age' scan once the character's age is inside [min_age, max_age]."""
    return ctx.record.age >= p.min_age and (p.max_age is None or ctx.record.age <= p.max_age)


@triggers.register("action", ActionTriggerParams)
def _action(p: ActionTriggerParams, ctx: StoryContext, payload: dict) -> bool:
    """Fires on an 'action' scan whose payload names this action: {"action": "work"}."""
    return payload.get("action") == p.action


@triggers.register("state", NoParams)
def _state(p: NoParams, ctx: StoryContext, payload: dict) -> bool:
    """Fires on a 'state' scan whenever the event's eligibility conditions hold."""
    return True


def parse_trigger(raw) -> dict:
    return triggers.normalize(raw)
