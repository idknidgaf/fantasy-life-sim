"""Event definition schema and library validation."""
import json

import pytest

from app.content import get_content
from app.story.errors import StoryContentError
from app.story.library import build_library, load_library
from app.story.schema import EventDefinition

from .story_helpers import ev, library


def invalid(raw):
    with pytest.raises(Exception):
        EventDefinition.model_validate(raw)


def test_minimal_event_is_valid_and_defaults_applied():
    d = EventDefinition.model_validate(ev("a"))
    assert d.version == 1 and d.event_type == "event"
    assert d.trigger == {"type": "manual"}
    assert d.lifecycle.mode == "once_per_life"


def test_full_event_is_valid():
    d = EventDefinition.model_validate(ev(
        "full", version=3, compatible_versions=[1, 2],
        trigger={"type": "age", "min_age": 5, "max_age": 9},
        conditions={"all": [{"type": "age_at_least", "value": 5}, {"not": {"type": "trait_owned", "trait": "x"}}]},
        lifecycle={"mode": "repeatable", "max_occurrences": 3, "cooldown_years": 2, "skippable": True, "expires_after_years": 1},
        choices=[{"id": "a", "text": "A", "effects": [{"type": "set_flag", "flag": "f", "value": True}],
                  "next_event": "full", "locked_hint": "needs luck",
                  "conditions": {"any": [{"type": "flag_at_least", "flag": "f", "value": 1}]}}]))
    assert d.choices[0].conditions == {"any": [{"type": "flag_at_least", "flag": "f", "value": 1}]}


@pytest.mark.parametrize("raw", [
    ev("Bad Id"),                                                  # id pattern
    ev("a", choices=[]),                                           # needs a choice
    ev("a", choices=[{"id": "x", "text": "1"}, {"id": "x", "text": "2"}]),   # duplicate choice ids
    ev("a", surprise=1),                                           # unknown field
    ev("a", version=0),
    ev("a", title=""),
    ev("a", lifecycle={"mode": "repeatable"}),                     # repeatable needs a limit
    ev("a", lifecycle={"mode": "once_per_life", "max_occurrences": 2}),
    ev("a", lifecycle={"mode": "nonsense"}),
    ev("a", lifecycle={"expires_after_years": 2}),                 # a blocking event stops time: cannot expire
    ev("a", event_type="node", trigger={"type": "state"}),         # nodes are manual
    ev("a", compatible_versions=[1]),                              # must be earlier than version
    ev("a", choices=[{"id": "x", "text": "t", "next_event": "a"}]),   # self loop on a one-time event
    ev("a", trigger={"type": "teleport"}),
    ev("a", trigger={"type": "age"}),                              # missing params
    ev("a", trigger={"type": "age", "min_age": 9, "max_age": 3}),
])
def test_invalid_definitions_are_rejected(raw):
    invalid(raw)


@pytest.mark.parametrize("cond", [
    {"type": "mystery"},                                           # unknown type
    {"type": "age_at_least"},                                      # missing param
    {"type": "age_at_least", "value": -1},
    {"type": "age_at_least", "value": 3, "extra": 1},              # unknown param
    {"type": "flag_equals", "flag": "f", "value": [1]},            # not a scalar
    {"all": []}, {"any": []},                                      # empty composites
    {"all": [{"type": "age_at_least", "value": 1}], "not": {"type": "age_at_least", "value": 1}},
    {"not": [{"type": "age_at_least", "value": 1}]},
    "age_at_least",
])
def test_invalid_conditions_are_rejected(cond):
    invalid(ev("a", conditions=cond))


@pytest.mark.parametrize("effect", [
    {"type": "run_python", "code": "print(1)"},                    # arbitrary code is not an effect
    {"type": "set_flag", "flag": "f"},
    {"type": "set_flag", "flag": "f", "value": None},
    {"type": "increment_flag", "flag": "f", "amount": "a lot"},
    {"type": "grant_trait"},
    {"type": "start_event", "event": "b", "x": 1},
])
def test_invalid_effects_are_rejected(effect):
    invalid(ev("a", choices=[{"id": "c", "text": "t", "effects": [effect]}]))


def test_conditions_nested_too_deep_are_rejected():
    cond = {"type": "age_at_least", "value": 1}
    for _ in range(20):
        cond = {"not": cond}
    invalid(ev("a", conditions=cond))


def test_library_rejects_duplicate_ids_and_dangling_references_and_reports_everything():
    with pytest.raises(StoryContentError) as e:
        build_library([
            ("f1", ev("a", choices=[{"id": "c", "text": "t", "next_event": "ghost"}])),
            ("f2", ev("a")),                                                           # duplicate
            ("f2", ev("b", conditions={"type": "event_completed", "event": "ghost2"})),
            ("f2", ev("c", choices=[{"id": "c", "text": "t",
                                     "effects": [{"type": "start_event", "event": "ghost3"}]}])),
            ("f2", ev("d", conditions={"type": "choice_was_made", "event": "b", "choice": "nope"})),
            ("f2", ev("e", conditions={"type": "trait_owned", "trait": "no_such_trait"})),
            ("f2", {"id": "broken"}),
        ], get_content())
    text = str(e.value)
    for needle in ("ghost'", "duplicate event id", "ghost2", "ghost3", "no choice 'nope'", "no_such_trait", "broken"):
        assert needle in text


def test_library_accepts_branching_and_convergence():
    lib = library(
        ev("start", choices=[{"id": "l", "text": "L", "next_event": "join"}, {"id": "r", "text": "R", "next_event": "join"}]),
        ev("join", event_type="node"),
    )
    assert len(lib) == 2


def test_load_library_from_directory(tmp_path):
    assert len(load_library(tmp_path / "missing")) == 0              # the empty container is valid
    (tmp_path / "a.json").write_text(json.dumps({"schema_version": 1, "events": [ev("one")]}))
    (tmp_path / "x.schema.json").write_text("not even json")        # schema files are skipped
    assert list(load_library(tmp_path).events) == ["one"]
    (tmp_path / "bad.json").write_text("{oops")
    with pytest.raises(StoryContentError):
        load_library(tmp_path)


def test_load_library_rejects_unsupported_schema_version(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps({"schema_version": 99, "events": []}))
    with pytest.raises(StoryContentError):
        load_library(tmp_path)


def test_shipped_story_content_is_valid_and_empty():
    from app.story.library import get_story_library
    assert len(get_story_library()) == 0
