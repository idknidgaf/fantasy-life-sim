import random

import pytest

from app.content import get_content
from app.rules import (
    alignment_label, apply_effect, apply_effects, conditions_met, describe_requirement,
    effective_stat, requirement_met, skill_aptitude, unmet_requirements_text,
)
from tests.helpers import make

CT = get_content()


class FixedRng:
    """A 'random' source returning a fixed value, to test chance-gated effects."""
    def __init__(self, value):
        self.value = value

    def random(self):
        return self.value

    def choice(self, seq):
        return seq[0]


# ---------- requirements ----------

def test_stat_requirement_uses_trait_bonuses():
    c = make(stats={"constitution": 30, "dexterity": 30, "wisdom": 30, "strength": 30, "charisma": 30}, traits=[])
    assert not requirement_met({"stat": "constitution", "min": 36}, c, CT)
    c.traits = ["iron_constitution"]                              # +6 constitution
    assert effective_stat(c, CT, "constitution") == 36
    assert requirement_met({"stat": "constitution", "min": 36}, c, CT)
    assert requirement_met({"stat": "constitution", "max": 36}, c, CT)
    assert not requirement_met({"stat": "constitution", "max": 35}, c, CT)


def test_skill_requirements():
    c = make(skills={"stealth": 6.0, "archery": 4.0, "tactics": 3.0})
    assert requirement_met({"skill": "stealth", "min_level": 6}, c, CT)
    assert not requirement_met({"skill": "stealth", "min_level": 6.5}, c, CT)
    assert requirement_met({"skill": "cooking", "max_level": 0}, c, CT)
    assert requirement_met({"skill_count": 3, "min_level": 3}, c, CT)
    assert not requirement_met({"skill_count": 3, "min_level": 4}, c, CT)
    assert requirement_met({"max_top_skill": 6}, c, CT)
    assert not requirement_met({"max_top_skill": 5}, c, CT)


def test_trait_flag_counter_item_axis():
    c = make(traits=["keen_sense"], flags=["guild_member"], counters={"selfless_acts": 2},
             items={"silver_locket": 1}, axes={"bravery": 15})
    assert requirement_met({"trait": "keen_sense"}, c, CT)
    assert requirement_met({"flag": "guild_member"}, c, CT)
    assert not requirement_met({"flag": "other"}, c, CT)
    assert requirement_met({"counter": "selfless_acts", "min": 2}, c, CT)
    assert not requirement_met({"counter": "selfless_acts", "min": 3}, c, CT)
    assert requirement_met({"item": "silver_locket"}, c, CT)
    assert requirement_met({"item_tag": "valuable_offering"}, c, CT)
    assert not requirement_met({"item_tag": "medicine"}, c, CT)
    assert requirement_met({"axis": "bravery", "min": 10}, c, CT)
    assert not requirement_met({"axis": "bravery", "min": 20}, c, CT)


def test_species_and_location_requirements():
    wolf = make("wolf")
    assert requirement_met({"species": "wolf"}, wolf, CT)
    assert requirement_met({"species": ["human", "wolf"]}, wolf, CT)
    assert requirement_met({"species_category": "beast"}, wolf, CT)
    assert requirement_met({"species_tag": "predator"}, wolf, CT)
    assert requirement_met({"species_tag": ["speaker", "predator"]}, wolf, CT)       # any-of
    assert not requirement_met({"species_tag": "speaker"}, wolf, CT)
    wolf.location = "tundra"
    assert requirement_met({"location_tag": ["tundra", "city"]}, wolf, CT)


def test_combinators():
    c = make(flags=["a"])
    t, f = {"flag": "a"}, {"flag": "b"}
    assert requirement_met({"any": [f, t]}, c, CT)
    assert not requirement_met({"all": [f, t]}, c, CT)
    assert requirement_met({"all": [t, {"not": f}]}, c, CT)
    assert not requirement_met({"not": t}, c, CT)


def test_describe_requirement_is_readable():
    assert describe_requirement({"stat": "wisdom", "min": 20}, CT) == "Wisdom 20+"
    assert describe_requirement({"trait": "keen_sense"}, CT) == "Trait: Keen Sense"
    assert describe_requirement({"item_tag": "valuable_offering"}, CT) == "An item (valuable offering)"
    assert "Order" in describe_requirement({"axis": "law_chaos", "min": 10}, CT)
    c = make(stats={"constitution": 30, "dexterity": 30, "wisdom": 10, "strength": 30, "charisma": 30})
    assert unmet_requirements_text([{"stat": "wisdom", "min": 20}], c, CT) == "Requires Wisdom 20+"
    assert unmet_requirements_text([{"stat": "wisdom", "min": 5}], c, CT) is None


# ---------- conditions ----------

def test_conditions_by_stage_and_species():
    ev = CT.events["fever_outbreak"].conditions         # young_adult+, humanoid
    adult = make("human", age=25)
    child = make("human", age=3)
    wolf = make("wolf", age=5)
    assert conditions_met(ev, adult, CT)
    assert not conditions_met(ev, child, CT)
    assert not conditions_met(ev, wolf, CT)


def test_altar_conditions_follow_the_designed_schema():
    cond = CT.events["forest_ancient_altar"].conditions
    elf = make("elf", age=30, location="ancient_forest")           # adolescent elf, has Keen Sense
    assert conditions_met(cond, elf, CT)
    elf.location = "village"
    assert not conditions_met(cond, elf, CT)                       # wrong location
    human = make("human", age=20, location="ancient_forest")
    assert not conditions_met(cond, human, CT)                     # no skill, no keen_sense
    human.skills["exploration"] = 10
    assert conditions_met(cond, human, CT)                         # requires_any satisfied by skill


# ---------- effects ----------

def test_stat_delta_clamps_to_species_cap():
    human = make("human", stats={"strength": 99, "constitution": 50, "dexterity": 50, "wisdom": 50, "charisma": 50})
    apply_effect({"stat_delta": {"strength": 5}}, human, CT, random.Random(1), [])
    assert human.stats["strength"] == 100
    drake = make("dragonkin", stats={"strength": 99, "constitution": 50, "dexterity": 50, "wisdom": 50, "charisma": 50})
    apply_effect({"stat_delta": {"strength": 5}}, drake, CT, random.Random(1), [])
    assert drake.stats["strength"] == 104                          # supernatural cap is 120


def test_skill_xp_is_scaled_by_aptitude():
    human = make("human")                                          # all x1.1
    apply_effect({"skill_xp": {"archery": 10}}, human, CT, random.Random(1), [])
    assert human.skills["archery"] == pytest.approx(11.0)
    elf = make("elf")                                              # magic x1.25
    assert skill_aptitude(elf, CT, "necromancy") == pytest.approx(1.25)
    apply_effect({"skill_xp": {"necromancy": 8}}, elf, CT, random.Random(1), [])
    assert elf.skills["necromancy"] == pytest.approx(10.0)
    slime = make("slime")
    assert skill_aptitude(slime, CT, "alchemy") == pytest.approx(1.4)


def test_skill_xp_by_category_goes_to_best_owned_skill():
    c = make(skills={"necromancy": 5.0, "enchantment": 1.0})
    apply_effect({"skill_xp": {"magic": 1}}, c, CT, random.Random(1), [])
    assert c.skills["necromancy"] > 5.0 and c.skills["enchantment"] == 1.0
    fresh = make(skills={})
    apply_effect({"skill_xp": {"magic": 1}}, fresh, CT, random.Random(3), [])
    assert len(fresh.skills) == 1 and CT.skills[next(iter(fresh.skills))] == "magic"


def test_chance_gates_an_effect():
    c = make(flags=[])
    apply_effect({"flag": "x", "chance": 0.3}, c, CT, FixedRng(0.9), [])
    assert "x" not in c.flags
    apply_effect({"flag": "x", "chance": 0.3}, c, CT, FixedRng(0.1), [])
    assert "x" in c.flags


def test_trait_grant_respects_prerequisites():
    c = make(traits=[])
    apply_effect({"grant_trait": "worldrender"}, c, CT, random.Random(1), [])       # needs adversary_instinct
    assert "worldrender" not in c.traits
    c.traits.append("adversary_instinct")
    notes: list[str] = []
    apply_effect({"grant_trait": "worldrender"}, c, CT, random.Random(1), notes)
    assert "worldrender" in c.traits and notes == ["gained Worldrender"]
    apply_effect({"grant_trait": "worldrender"}, c, CT, random.Random(1), [])
    assert c.traits.count("worldrender") == 1


def test_items_flags_counters_location():
    c = make(items={}, flags=[], counters={})
    rng = random.Random(1)
    apply_effects([{"grant_item": "silver_locket"}, {"grant_item": {"id": "rough_gemstone", "count": 2}},
                   {"counter": {"selfless_acts": 1}}, {"counter": {"selfless_acts": 2}},
                   {"flag": "a"}, {"set_location": "city"}], c, CT, rng, [])
    assert c.items == {"silver_locket": 1, "rough_gemstone": 2}
    assert c.counters["selfless_acts"] == 3 and c.location == "city" and c.flags == ["a"]
    apply_effect({"consume_item": {"tag": "valuable_offering"}}, c, CT, rng, [])
    assert sum(c.items.values()) == 2
    apply_effect({"consume_item": {"id": "silver_locket"}}, c, CT, rng, [])      # already gone: no error
    apply_effect({"clear_flag": "a"}, c, CT, rng, [])
    assert c.flags == []


def test_die_stops_remaining_effects():
    c = make(flags=[])
    apply_effects([{"die": {"cause": "wolves"}}, {"flag": "never"}], c, CT, random.Random(1), [])
    assert not c.alive and c.cause_of_death == "wolves" and "never" not in c.flags


def test_trigger_event_queues_behind_a_pending_one():
    c = make(pending_event="stray_dog")
    apply_effect({"trigger_event": "master_of_none_path"}, c, CT, random.Random(1), [])
    assert c.pending_event == "stray_dog" and c.queued_events == ["master_of_none_path"]


def test_alignment_label():
    assert alignment_label(make(axes={"law_chaos": 0, "good_evil": 0})) == "True Neutral"
    assert alignment_label(make(axes={"law_chaos": 40, "good_evil": 40})) == "Lawful Good"
    assert alignment_label(make(axes={"law_chaos": -40, "good_evil": -40})) == "Chaotic Evil"
    assert alignment_label(make(axes={"law_chaos": 0, "good_evil": 40})) == "Neutral Good"
