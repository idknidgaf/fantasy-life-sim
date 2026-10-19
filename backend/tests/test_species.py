import statistics

import pytest

from app import engine as sim
from app.content import STATS, get_content
from app.rules import stage_at, trainable_skills
from tests.helpers import make, play_life

CT = get_content()


def stage_name(species, age):
    return stage_at(CT.species[species], age, CT).id


# ---------- life stages scale with the species ----------

def test_human_stages():
    expected = {0: "infancy", 2: "infancy", 3: "early_childhood", 5: "early_childhood", 6: "childhood",
                11: "childhood", 12: "adolescence", 17: "adolescence", 18: "young_adult", 29: "young_adult",
                30: "adulthood", 49: "adulthood", 50: "middle_age", 64: "middle_age", 65: "elder", 90: "elder"}
    for age, stage in expected.items():
        assert stage_name("human", age) == stage, age


def test_stages_scale_to_lifespan():
    assert stage_name("elf", 17) == "childhood" and stage_name("elf", 18) == "adolescence"
    assert stage_name("wolf", 0) == "infancy" and stage_name("wolf", 2) == "childhood"
    assert stage_name("wolf", 3) == "adolescence" and stage_name("wolf", 12) == "elder"
    assert stage_name("slime", 6) == "adolescence"
    # A 3-year-old human is a toddler; a 3-year-old wolf is a teenager.
    assert stage_name("human", 3) != stage_name("wolf", 3)


# ---------- racial modifiers ----------

def test_species_modifiers_shape_birth_stats():
    def mean(species, stat):
        return statistics.mean(sim.new_character("x", species, s).stats[stat] for s in range(300))
    assert mean("dwarf", "constitution") > mean("elf", "constitution") + 10
    assert mean("elf", "dexterity") > mean("dwarf", "dexterity") + 8
    assert mean("wolf", "charisma") < mean("human", "charisma") - 10
    assert mean("slime", "constitution") > mean("human", "constitution") + 10


def test_innate_traits():
    assert "keen_sense" in make("elf").traits
    assert "iron_constitution" in make("dwarf").traits
    assert "fleet_footwork" in make("wolf").traits
    assert "regenerative" in make("slime").traits
    assert "draconic_scales" in make("dragonkin").traits


def test_supernatural_stat_cap():
    assert CT.species["dragonkin"].stat_cap == 120 and CT.species["human"].stat_cap == 100
    assert CT.species["wolf"].stat_cap == 100
    def mean(species):
        return statistics.mean(sim.new_character("x", species, s).stats["strength"] for s in range(300))
    assert mean("dragonkin") > mean("human") + 8


def test_birth_location_comes_from_species_habitats():
    for sp in CT.species.values():
        for seed in range(30):
            assert sim.new_character("x", sp.id, seed).location in sp.habitats


def test_unknown_species_rejected():
    with pytest.raises(sim.InvalidChoice):
        sim.new_character("x", "unicorn", 1)


# ---------- non-humanoid gameplay ----------

def test_wolves_have_species_flavoured_actions():
    wolf = make("wolf", age=4)                                    # young adult wolf
    labels = {a["id"]: a["label"] for a in sim.offer_actions(wolf, 1)}
    assert labels["work"] == "Hunt and forage" and labels["rest"] == "Curl up and rest"
    human = make("human", age=25)
    assert {a["id"]: a["label"] for a in sim.offer_actions(human, 1)}["work"] == "Take on hard work"


def test_wolves_can_only_train_wolf_skills():
    allowed = set(CT.species["wolf"].allowed_skills)
    for seed in range(20):
        wolf = make("wolf", seed=seed, age=5)
        assert set(trainable_skills(wolf, CT)) <= allowed
        for a in sim.offer_actions(wolf, seed):
            if a["id"].startswith("train:"):
                assert a["id"].split(":")[1] in allowed


def test_supernatural_skills_are_locked_until_unlocked():
    human = make("human", age=25)
    assert not any(CT.skills[s] == "supernatural" for s in trainable_skills(human, CT))
    assert "draconic_mastery" in trainable_skills(make("dragonkin", age=40), CT)      # innate
    assert "beast_instinct" in trainable_skills(make("wolf", age=5), CT)
    human.skills["beast_instinct"] = 2.0                                           # e.g. from befriending a dog
    assert "beast_instinct" in trainable_skills(human, CT)
    human = make("human", age=25, traits=["blessed"])                             # trait unlocks divine_power
    assert "divine_power" in trainable_skills(human, CT)


# ---------- different species, different lives ----------

def test_lifespans_differ_by_species():
    def mean_age(species, n=25):
        return statistics.mean(play_life(s, species, talent_chance=0).age for s in range(n))
    wolf, human, elf = mean_age("wolf"), mean_age("human"), mean_age("elf")
    assert wolf < 20 < human < elf
    assert wolf < human * 0.4 and elf > human * 1.2


def event_ids(species, n=40):
    seen = set()
    for seed in range(n):
        seen |= {h["event_id"] for h in play_life(seed, species, talent_chance=0).history if h.get("event_id")}
    return seen


def test_species_experience_different_stories():
    human, wolf, slime, drake = event_ids("human"), event_ids("wolf"), event_ids("slime"), event_ids("dragonkin")
    # Only pack animals see these; humans never do.
    assert {"den_days", "first_hunt", "rival_pack"} <= wolf
    assert not {"den_days", "first_hunt", "rival_pack", "harsh_winter"} & human
    # Wolves cannot read, haggle, or join guilds.
    assert not {"strange_book", "wandering_merchant", "guild_recruiter", "tournament"} & wolf
    # Species-specific story hooks.
    assert "formless_days" in slime and "absorb_the_fallen" in slime
    assert "clutch_warmth" in drake and "dragon_hoard_urge" in drake
    assert "stray_dog" in human and "stray_dog" not in wolf
