"""Conditions: composable, side-effect-free yes/no questions about one character life.

Wire format (JSON):
    leaf:   {"type": "age_at_least", "value": 5}
    all:    {"all": [ <condition>, ... ]}       every child holds (at least one child)
    any:    {"any": [ <condition>, ... ]}       at least one child holds (at least one child)
    not:    {"not": <condition>}

To add a type, register it below (or anywhere that is imported); event files never change.
"""
from __future__ import annotations

from pydantic import Field

from . import store
from .context import StoryContext
from .registry import Registry, StrictParams
from .scalars import Scalar, is_number, same_value

MAX_DEPTH = 12
COMPOSITES = ("all", "any", "not")

conditions = Registry("condition")


# ---------------------------------------------------------------- parameter models

class AgeParams(StrictParams):
    value: int = Field(ge=0)


class TraitParams(StrictParams):
    trait: str = Field(min_length=1)


class FlagEqualsParams(StrictParams):
    flag: str = Field(min_length=1)
    value: Scalar


class FlagAtLeastParams(StrictParams):
    flag: str = Field(min_length=1)
    value: int | float


class EventParams(StrictParams):
    event: str = Field(min_length=1)


class ChoiceMadeParams(StrictParams):
    event: str = Field(min_length=1)
    choice: str = Field(min_length=1)


# ---------------------------------------------------------------- built-in condition types

@conditions.register("age_at_least", AgeParams)
def _age_at_least(p: AgeParams, ctx: StoryContext) -> bool:
    """The character's age is >= value."""
    return ctx.record.age >= p.value


@conditions.register("age_at_most", AgeParams)
def _age_at_most(p: AgeParams, ctx: StoryContext) -> bool:
    """The character's age is <= value."""
    return ctx.record.age <= p.value


@conditions.register("trait_owned", TraitParams)
def _trait_owned(p: TraitParams, ctx: StoryContext) -> bool:
    """The character has the trait."""
    return p.trait in (ctx.record.traits or [])


@conditions.register("trait_absent", TraitParams)
def _trait_absent(p: TraitParams, ctx: StoryContext) -> bool:
    """The character does not have the trait."""
    return p.trait not in (ctx.record.traits or [])


@conditions.register("flag_equals", FlagEqualsParams)
def _flag_equals(p: FlagEqualsParams, ctx: StoryContext) -> bool:
    """The story flag is set and equals value (type-aware: true is not 1). An unset flag never equals anything."""
    current = store.get_flag(ctx, p.flag)
    return current is not None and same_value(current, p.value)


@conditions.register("flag_at_least", FlagAtLeastParams)
def _flag_at_least(p: FlagAtLeastParams, ctx: StoryContext) -> bool:
    """The numeric story flag is >= value. An unset flag counts as 0; a non-numeric flag is never enough."""
    current = store.get_flag(ctx, p.flag)
    if current is None:
        current = 0
    return is_number(current) and current >= p.value


@conditions.register("event_completed", EventParams)
def _event_completed(p: EventParams, ctx: StoryContext) -> bool:
    """An instance of the event has been resolved in this life."""
    return store.event_completed(ctx, p.event)


@conditions.register("choice_was_made", ChoiceMadeParams)
def _choice_was_made(p: ChoiceMadeParams, ctx: StoryContext) -> bool:
    """The given choice of the given event was selected at some point in this life."""
    return store.choice_made(ctx, p.event, p.choice)


# ---------------------------------------------------------------- parsing and evaluation

def parse_condition(raw, depth: int = 0) -> dict:
    """Validate a condition tree and return it normalized. Raises ValueError describing the problem."""
    if depth > MAX_DEPTH:
        raise ValueError(f"conditions are nested more than {MAX_DEPTH} levels deep")
    if not isinstance(raw, dict):
        raise ValueError(f"a condition must be an object, got {type(raw).__name__}")
    composite = [k for k in COMPOSITES if k in raw]
    if composite:
        if len(raw) != 1:
            raise ValueError(f"a '{composite[0]}' condition must have no other keys, got {sorted(raw)}")
        key = composite[0]
        if key == "not":
            return {"not": parse_condition(raw["not"], depth + 1)}
        children = raw[key]
        if not isinstance(children, list) or not children:
            raise ValueError(f"'{key}' needs a non-empty list of conditions")
        return {key: [parse_condition(c, depth + 1) for c in children]}
    return conditions.normalize(raw)


def evaluate(cond: dict | None, ctx: StoryContext) -> bool:
    """Evaluate a condition already validated by parse_condition. None means 'no condition': true."""
    if cond is None:
        return True
    if "all" in cond:
        return all(evaluate(c, ctx) for c in cond["all"])
    if "any" in cond:
        return any(evaluate(c, ctx) for c in cond["any"])
    if "not" in cond:
        return not evaluate(cond["not"], ctx)
    spec, params = conditions.parse(cond)
    return bool(spec.handler(params, ctx))


def walk(cond: dict | None):
    """Yield every leaf of a condition tree (used for cross-reference checks)."""
    if cond is None:
        return
    for key in ("all", "any"):
        if key in cond:
            for c in cond[key]:
                yield from walk(c)
            return
    if "not" in cond:
        yield from walk(cond["not"])
        return
    yield cond
