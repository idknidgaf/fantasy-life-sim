import random

import pytest

from app.engine import (
    EVENT_CHANCE, STATS, InvalidChoice, NotAllowed, advance_turn, load_events,
    load_skills, new_character, offer_actions, perform_action, resolve_event,
    summarize,
)


def fresh(seed=1, age=0):
    c = new_character("Aren", random.Random(seed))
    c.age = age
    return c


def live_to_death(seed):
    """Play a whole life, always taking the first option offered."""
    c = fresh(seed)
    for _ in range(5000):
        if not c.alive:
            return c
        if c.pending_event:
            ev = load_events()[c.pending_event]
            resolve_event(c, seed, ev["id"], ev["choices"][0]["id"])
        else:
            actions = offer_actions(c, seed)
            if actions:
                perform_action(c, seed, actions[0]["id"])
            advance_turn(c, seed)
    raise AssertionError("character never died")


# ---------- whole lives ----------

def test_character_eventually_dies_with_a_cause():
    c = live_to_death(42)
    assert not c.alive and c.cause_of_death and c.age > 0
    assert c.history[-1]["kind"] == "death"


def test_same_seed_gives_same_life():
    a, b = live_to_death(7), live_to_death(7)
    assert (a.age, a.cause_of_death, a.stats, a.skills, a.history) == \
           (b.age, b.cause_of_death, b.stats, b.skills, b.history)


def test_different_seeds_differ():
    assert live_to_death(1).history != live_to_death(2).history


def test_stats_stay_in_range():
    c = live_to_death(3)
    assert all(1 <= c.stats[s] <= 100 for s in STATS)


# ---------- data integrity ----------

def test_skills_loaded_from_json():
    skills = load_skills()
    assert skills["necromancy"] == "magic" and len(skills) == 28


def test_event_data_is_valid():
    skills = load_skills()
    events = load_events()
    assert len(events) >= 5
    for e in events.values():
        assert e["min_age"] <= e["max_age"], e["id"]
        assert len(e["choices"]) >= 2, e["id"]
        ids = [ch["id"] for ch in e["choices"]]
        assert len(ids) == len(set(ids)), f"duplicate choice id in {e['id']}"
        for ch in e["choices"]:
            assert ch["label"] and ch["result"], e["id"]
            for eff in ch.get("effects", []):
                assert eff["type"] in {"stat", "skill", "death"}, e["id"]
                if eff["type"] == "stat":
                    assert eff["target"] in STATS, (e["id"], eff)
                if eff["type"] == "skill":
                    assert eff["target"] in skills, (e["id"], eff)
                if "chance" in eff:
                    assert 0 < eff["chance"] <= 1


# ---------- actions ----------

def test_actions_depend_on_age():
    assert [a["id"] for a in offer_actions(fresh(age=2), 1)] == ["play"]
    ids = [a["id"] for a in offer_actions(fresh(age=20), 1)]
    assert len(ids) == 4 and "work" in ids and "rest" in ids
    assert offer_actions(fresh(age=20), 1) == offer_actions(fresh(age=20), 1)


def test_training_raises_skill_and_uses_the_action():
    c = fresh(age=20)
    action = next(a["id"] for a in offer_actions(c, 1) if a["id"].startswith("train:"))
    perform_action(c, 1, action)
    assert c.skills[action.split(":")[1]] > 0
    assert c.action_taken == action
    assert offer_actions(c, 1) == []
    with pytest.raises(NotAllowed):
        perform_action(c, 1, "rest")          # one action per year


def test_unavailable_action_rejected():
    with pytest.raises(InvalidChoice):
        perform_action(fresh(age=20), 1, "train:not_a_skill")
    with pytest.raises(InvalidChoice):
        perform_action(fresh(age=2), 1, "work")   # too young


# ---------- turns ----------

def test_advance_ages_and_resets_action():
    c = fresh(age=20)
    perform_action(c, 1, "rest")
    advance_turn(c, 1)
    assert c.age == 21 and c.action_taken is None


def test_dead_character_cannot_act():
    c = live_to_death(5)
    with pytest.raises(NotAllowed):
        advance_turn(c, 5)
    with pytest.raises(NotAllowed):
        perform_action(c, 5, "rest")


# ---------- events ----------

def test_events_trigger_over_a_life():
    """With a 35% yearly chance, a normal life must meet some events."""
    assert EVENT_CHANCE > 0
    c = live_to_death(10)
    assert any(h["kind"] == "event" for h in c.history)


def test_pending_event_blocks_everything_until_resolved():
    c = fresh(age=20)
    c.pending_event = "wandering_merchant"
    assert offer_actions(c, 1) == []
    with pytest.raises(NotAllowed):
        perform_action(c, 1, "rest")
    with pytest.raises(NotAllowed):
        advance_turn(c, 1)
    resolve_event(c, 1, "wandering_merchant", "listen")
    assert c.pending_event is None
    assert c.history[-1]["kind"] == "event" and c.history[-1]["event_id"] == "wandering_merchant"
    assert offer_actions(c, 1)                 # free to act again


def test_event_choice_applies_effects():
    c = fresh(age=20)
    before = c.stats["wisdom"]
    c.pending_event = "wandering_merchant"
    resolve_event(c, 1, "wandering_merchant", "listen")      # +1 wisdom
    assert c.stats["wisdom"] == min(100, before + 1)


def test_event_errors():
    c = fresh(age=20)
    with pytest.raises(NotAllowed):
        resolve_event(c, 1, "wandering_merchant", "listen")   # nothing pending
    c.pending_event = "wandering_merchant"
    with pytest.raises(NotAllowed):
        resolve_event(c, 1, "stray_dog", "befriend")          # wrong event
    with pytest.raises(InvalidChoice):
        resolve_event(c, 1, "wandering_merchant", "nope")


def test_event_can_kill():
    c = fresh(age=30)
    for seed in range(2000):                  # find a seed where the wolf fight is fatal
        c = fresh(age=30)
        c.pending_event = "wolves_in_the_woods"
        resolve_event(c, seed, "wolves_in_the_woods", "fight")
        if not c.alive:
            break
    assert not c.alive and c.cause_of_death == "wolves"
    assert c.history[-1]["kind"] == "death"


def test_once_only_events_do_not_repeat():
    c = live_to_death(21)
    once = [e for e in load_events().values() if e.get("once")]
    for e in once:
        assert sum(1 for h in c.history if h.get("event_id") == e["id"]) <= 1


def test_summary():
    c = live_to_death(11)
    s = summarize(c)
    assert s["cause_of_death"] and s["age"] == c.age and s["events_faced"] >= 0
