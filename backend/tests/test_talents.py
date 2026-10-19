import random

import pytest

from app import engine as sim
from app.content import get_content
from tests.helpers import make, play_life

CT = get_content()


def with_talent(species, talent, seed_limit=100_000, **overrides):
    """A newborn that really rolled `talent` at birth (found by searching seeds)."""
    for seed in range(seed_limit):
        c = sim.new_character("Test", species, seed)
        if c.hidden_talent == talent:
            for k, v in overrides.items():
                setattr(c, k, v)
            return c, seed
    raise AssertionError(f"no seed gives {species} {talent}")


# ---------- the birth roll ----------

def test_about_one_percent_are_born_with_a_talent():
    n = 20_000
    count = sum(1 for s in range(n) if sim.new_character("x", "human", s).hidden_talent)
    assert 140 <= count <= 270, count               # 1% of 20,000 = 200


def test_birth_roll_is_deterministic_and_saved():
    a = sim.new_character("x", "human", 46)
    b = sim.new_character("y", "human", 46)
    assert a.hidden_talent == b.hidden_talent == "master_of_none"
    assert a.talent_state == "dormant"
    assert sim.new_character("x", "human", 47).talent_state == "none"


def test_talent_pool_respects_eligibility():
    seen = {sp: set() for sp in ("human", "wolf", "slime")}
    for seed in range(25_000):
        for sp in seen:
            t = sim.new_character("x", sp, seed).hidden_talent
            if t:
                seen[sp].add(t)
    assert "primordial_origin" in seen["slime"] and "divine_favor" not in seen["slime"]
    assert "divine_favor" in seen["human"] and "primordial_origin" not in seen["human"]
    assert not {"divine_favor", "primordial_origin"} & seen["wolf"]


def test_talent_is_never_rerolled_after_birth(monkeypatch):
    """The birth roll lives in new_character only. Nothing during a life may call it again."""
    c, seed = with_talent("human", "worlds_adversary")
    original = c.hidden_talent

    def forbidden(*args, **kwargs):
        raise AssertionError("hidden talent was rerolled!")
    monkeypatch.setattr(sim, "roll_hidden_talent", forbidden)

    pick = random.Random(0)
    for _ in range(400):
        if not c.alive:
            break
        if c.pending_event:
            opts = [o for o in sim.event_choice_state(c, CT, c.pending_event) if o["available"]]
            sim.resolve_event(c, seed, c.pending_event, pick.choice(opts)["id"])
        else:
            sim.advance_turn(c, seed)
    assert c.hidden_talent == original


def test_forced_talent_chance():
    c = sim.new_character("x", "human", 1, talent_chance=1.0)
    assert c.hidden_talent and c.talent_state == "dormant"
    assert sim.new_character("x", "human", 1, talent_chance=0).talent_state == "none"


# ---------- awakening ----------

def ready(talent, **overrides):
    c = make("human", age=40)
    c.hidden_talent, c.talent_state = talent, "dormant"
    for k, v in overrides.items():
        setattr(c, k, v)
    return c


def test_master_of_none_awakens_for_generalists():
    skills = {s: 4.0 for s in list(CT.skills)[:8]}
    c = ready("master_of_none", skills=skills)
    assert sim.check_awakening(c, 1)
    assert c.talent_state == "awakened" and "generalist" in c.traits
    assert c.pending_event == "master_of_none_path"
    assert c.history[-1]["kind"] == "awakening"


def test_master_of_none_needs_breadth_and_no_specialism():
    assert not sim.check_awakening(ready("master_of_none", skills={s: 4.0 for s in list(CT.skills)[:5]}), 1)
    specialist = {s: 4.0 for s in list(CT.skills)[:8]}
    specialist["archery"] = 50.0
    assert not sim.check_awakening(ready("master_of_none", skills=specialist), 1)


def test_divine_favor_awakens_after_selfless_deeds():
    c = ready("divine_favor", counters={"selfless_acts": 3}, axes={"good_evil": 30, "law_chaos": 0})
    assert sim.check_awakening(c, 1) and "blessed" in c.traits
    kind = ready("divine_favor", counters={"selfless_acts": 3}, axes={"good_evil": 5})
    assert not sim.check_awakening(kind, 1)                   # not good enough


def test_worlds_adversary_awakens_after_surviving_overwhelming_foes():
    assert not sim.check_awakening(ready("worlds_adversary", counters={"overwhelming_foe": 2}), 1)
    c = ready("worlds_adversary", counters={"overwhelming_foe": 3})
    assert sim.check_awakening(c, 1) and "adversary_instinct" in c.traits


def test_unbroken_will_awakens_after_repeated_trials():
    c = ready("unbroken_will", counters={"mental_trials": 3}, skills={"willpower": 9.0})
    assert sim.check_awakening(c, 1) and "steel_resolve" in c.traits
    assert not sim.check_awakening(ready("unbroken_will", counters={"mental_trials": 3}, skills={"willpower": 2.0}), 1)


def test_primordial_origin_for_a_monstrous_species():
    c = make("slime", age=10)
    c.hidden_talent, c.talent_state = "primordial_origin", "dormant"
    assert not sim.check_awakening(c, 1)
    c.flags.append("touched_primal_source")
    assert sim.check_awakening(c, 1) and "primal_spark" in c.traits


def test_awakening_waits_for_the_right_life_stage():
    child = make("human", age=8)
    child.hidden_talent, child.talent_state = "worlds_adversary", "dormant"
    child.counters["overwhelming_foe"] = 3
    assert not sim.check_awakening(child, 1)                  # still a child
    child.age = 14
    assert sim.check_awakening(child, 1)


def test_a_talent_awakens_only_once():
    c = ready("worlds_adversary", counters={"overwhelming_foe": 3})
    assert sim.check_awakening(c, 1)
    before = (list(c.traits), len(c.history))
    assert not sim.check_awakening(c, 1)
    assert (list(c.traits), len(c.history)) == before


def test_awakening_during_the_annual_turn_starts_a_story():
    for seed in range(40):
        c = ready("worlds_adversary", counters={"overwhelming_foe": 3}, age=30)
        sim.advance_turn(c, seed)
        if c.alive:
            assert c.talent_state == "awakened" and c.pending_event == "worlds_adversary_path"
            sim.resolve_event(c, seed, "worlds_adversary_path", "hunt")
            assert "monster_hunter" in c.flags
            return
    raise AssertionError("never survived a turn")


def test_awakening_through_an_event_chain_end_to_end():
    """A slime born with Primordial Origin drinks from a mana spring and is changed."""
    c, seed = with_talent("slime", "primordial_origin", age=10, location="cavern")
    c.pending_event = "mana_spring"
    sim.resolve_event(c, seed, "mana_spring", "drink")
    if c.alive:
        assert c.talent_state == "awakened" and "primal_spark" in c.traits
        assert c.pending_event == "primordial_origin_path"           # the story begins immediately
        assert any(h["kind"] == "awakening" for h in c.history)
        sim.resolve_event(c, seed, "primordial_origin_path", "embrace")
        assert "primordial_embraced" in c.flags
    else:
        assert c.cause_of_death == "raw mana"


# ---------- hints and secrecy ----------

def test_dormant_talents_hint_vaguely_and_never_name_themselves():
    hints, names = [], {t.name.lower() for t in CT.talents.values()}
    for seed in range(60):
        c = ready("master_of_none", age=20)                  # requirements unmet: stays dormant
        for _ in range(25):
            if not c.alive:
                break
            if c.pending_event:
                opts = [o for o in sim.event_choice_state(c, CT, c.pending_event) if o["available"]]
                sim.resolve_event(c, seed, c.pending_event, opts[0]["id"])
            if c.alive:
                sim.advance_turn(c, seed)
        hints += [h["text"] for h in c.history if h["kind"] == "hint"]
    assert hints, "dormant talents should drop hints"
    assert set(hints) <= set(CT.talents["master_of_none"].hints)
    assert not any(n in h.lower() for h in hints for n in names)


def test_characters_without_a_talent_get_no_hints():
    for seed in range(15):
        c = play_life(seed, "human", talent_chance=0)
        assert not any(h["kind"] == "hint" for h in c.history)


def test_death_reveals_an_unawakened_talent_only_then():
    c = ready("worlds_adversary")
    assert sim.summarize(c)["unawakened_talent"] == "World's Adversary"      # summary is only shown after death
    c.talent_state = "awakened"
    s = sim.summarize(c)
    assert s["unawakened_talent"] is None and s["awakened_talent"] == "World's Adversary"
