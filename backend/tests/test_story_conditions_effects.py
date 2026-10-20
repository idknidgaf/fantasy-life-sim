"""Condition evaluation, composition, effect execution and the registries."""
import pytest
from pydantic import Field
from sqlmodel import Session

from app.models import StoryChoiceLog, StoryEventInstance
from app.story import store
from app.story.conditions import conditions, evaluate, parse_condition
from app.story.effects import apply_effect, effects
from app.story.errors import StoryEffectError
from app.story.registry import StrictParams

from .story_helpers import ctx_for, ev, free_trait, library, make_life, memory_engine


@pytest.fixture
def env():
    with Session(memory_engine()) as session:
        rec = make_life(session, age=10)
        yield session, rec, ctx_for(session, rec)


def check(cond, ctx):
    return evaluate(parse_condition(cond), ctx)


def test_age_conditions(env):
    _, _, ctx = env
    assert check({"type": "age_at_least", "value": 10}, ctx)
    assert not check({"type": "age_at_least", "value": 11}, ctx)
    assert check({"type": "age_at_most", "value": 10}, ctx)
    assert not check({"type": "age_at_most", "value": 9}, ctx)


def test_trait_conditions(env):
    session, rec, ctx = env
    t = free_trait(rec)
    assert check({"type": "trait_absent", "trait": t}, ctx) and not check({"type": "trait_owned", "trait": t}, ctx)
    rec.traits = [*rec.traits, t]
    assert check({"type": "trait_owned", "trait": t}, ctx) and not check({"type": "trait_absent", "trait": t}, ctx)


def test_flag_conditions(env):
    _, _, ctx = env
    assert not check({"type": "flag_equals", "flag": "f", "value": "x"}, ctx)        # unset
    assert check({"type": "flag_at_least", "flag": "f", "value": 0}, ctx)           # unset counts as 0
    assert not check({"type": "flag_at_least", "flag": "f", "value": 1}, ctx)
    store.set_flag(ctx, "f", "x")
    assert check({"type": "flag_equals", "flag": "f", "value": "x"}, ctx)
    assert not check({"type": "flag_at_least", "flag": "f", "value": 0}, ctx)        # strings are not numbers
    store.set_flag(ctx, "f", True)
    assert check({"type": "flag_equals", "flag": "f", "value": True}, ctx)
    assert not check({"type": "flag_equals", "flag": "f", "value": 1}, ctx)          # true is not 1
    assert not check({"type": "flag_at_least", "flag": "f", "value": 1}, ctx)        # nor a number
    store.set_flag(ctx, "f", 3)
    assert check({"type": "flag_at_least", "flag": "f", "value": 3}, ctx)
    assert not check({"type": "flag_equals", "flag": "f", "value": True}, ctx)


def test_event_and_choice_history_conditions(env):
    session, rec, ctx = env
    assert not check({"type": "event_completed", "event": "e"}, ctx)
    inst = StoryEventInstance(campaign_id=rec.campaign_id, character_id=rec.id, event_id="e", status="resolved",
                              selected_choice_id="c")
    session.add(inst)
    session.flush()
    session.add(StoryChoiceLog(instance_id=inst.id, campaign_id=rec.campaign_id, character_id=rec.id,
                               event_id="e", choice_id="c"))
    session.flush()
    assert check({"type": "event_completed", "event": "e"}, ctx)
    assert check({"type": "choice_was_made", "event": "e", "choice": "c"}, ctx)
    assert not check({"type": "choice_was_made", "event": "e", "choice": "other"}, ctx)
    assert not check({"type": "event_completed", "event": "other"}, ctx)


def test_composition(env):
    _, _, ctx = env
    yes, no = {"type": "age_at_least", "value": 1}, {"type": "age_at_least", "value": 99}
    assert check({"all": [yes, yes]}, ctx) and not check({"all": [yes, no]}, ctx)
    assert check({"any": [no, yes]}, ctx) and not check({"any": [no, no]}, ctx)
    assert check({"not": no}, ctx) and not check({"not": yes}, ctx)
    assert check({"all": [yes, {"any": [no, {"not": no}]}]}, ctx)           # nesting
    assert evaluate(None, ctx) is True                                         # no condition: eligible


def test_flag_effects_persist_and_report(env):
    _, _, ctx = env
    apply_effect({"type": "set_flag", "flag": "a", "value": 2}, ctx)
    apply_effect({"type": "increment_flag", "flag": "a", "amount": 3}, ctx)
    apply_effect({"type": "increment_flag", "flag": "fresh"}, ctx)               # starts from 0, default +1
    assert store.all_flags(ctx) == {"a": 5, "fresh": 1}
    assert [r["type"] for r in ctx.effect_log] == ["set_flag", "increment_flag", "increment_flag"]


def test_increment_non_numeric_flag_fails(env):
    _, _, ctx = env
    apply_effect({"type": "set_flag", "flag": "a", "value": "text"}, ctx)
    with pytest.raises(StoryEffectError):
        apply_effect({"type": "increment_flag", "flag": "a"}, ctx)


def test_grant_trait_uses_game_rules(env):
    session, rec, ctx = env
    t = free_trait(rec)
    assert apply_effect({"type": "grant_trait", "trait": t}, ctx)["granted"] is True
    session.refresh(rec)
    assert t in rec.traits
    assert apply_effect({"type": "grant_trait", "trait": t}, ctx)["granted"] is False     # already owned
    assert rec.traits.count(t) == 1
    with pytest.raises(StoryEffectError):
        apply_effect({"type": "grant_trait", "trait": "no_such_trait"}, ctx)


def test_start_and_complete_event_effects(env):
    session, rec, _ = env
    lib = library(ev("a"), ev("b"))
    ctx = ctx_for(session, rec, lib)
    assert apply_effect({"type": "start_event", "event": "a"}, ctx)["started"] is True
    assert store.open_instance(ctx, "a") is not None
    assert apply_effect({"type": "start_event", "event": "a"}, ctx)["started"] is False   # already open
    assert apply_effect({"type": "complete_event", "event": "a"}, ctx)["changed"] is True
    assert store.event_completed(ctx, "a") and store.open_instance(ctx, "a") is None
    assert apply_effect({"type": "complete_event", "event": "a"}, ctx)["changed"] is False  # idempotent
    assert apply_effect({"type": "complete_event", "event": "b"}, ctx)["changed"] is True   # never started
    assert store.event_completed(ctx, "b")


def test_unknown_types_are_rejected_by_registries():
    with pytest.raises(ValueError, match="unknown effect type"):
        effects.normalize({"type": "exec", "code": "1"})
    with pytest.raises(ValueError, match="unknown condition type"):
        parse_condition({"type": "exec"})


def test_new_types_register_without_touching_event_definitions(env):
    """The extensibility promise: one decorated function adds a condition type."""
    session, rec, ctx = env

    class StatParams(StrictParams):
        stat: str
        minimum: int = Field(ge=0)

    @conditions.register("test_stat_at_least", StatParams)
    def _stat(p, c):
        return c.record.stats.get(p.stat, 0) >= p.minimum

    try:
        cond = {"type": "test_stat_at_least", "stat": "strength", "minimum": 1}
        assert check(cond, ctx)
        assert not check({"type": "test_stat_at_least", "stat": "strength", "minimum": 10**6}, ctx)
        with pytest.raises(ValueError):
            parse_condition({"type": "test_stat_at_least", "stat": "strength"})       # params validated
    finally:
        del conditions._specs["test_stat_at_least"]
