"""NPC growth: age-aware start, profiles, probabilities, amounts, caps, once per year, modifiers, life-experience hooks."""
import statistics

import pytest

from app.content import STATS, ContentError
from app.npc import Npc, NpcProgress, NpcSpec
from app.power import get_power_config
from app.progression import (
    GrowthConfig, ProgressionError, add_experience, apply_life_event, catch_up, get_growth_config, initial_development,
    load_growth_config, remove_experience, stat_cap,
)
from tests.progress_helpers import CT, FACTORY, growth_config, person

PCFG = get_power_config()


def growing(age=10, seed=1, **kw):
    """A persistent human at `age`, with progress stamped as fully developed for it (nothing owed yet)."""
    return person(age=age, seed=seed, **kw)


def tick(npc, years, cfg):
    reports = []
    for _ in range(years):
        npc.age += 1
        reports += catch_up(npc, CT, cfg, PCFG)
    return reports


# ---------- the shipped configuration ----------

def test_shipped_config_loads_and_covers_every_life_stage():
    cfg = get_growth_config()
    assert all(cfg.profile_for(stage) is not None for stage in CT.stage_ids)
    assert {"childhood", "adolescence", "adulthood", "old_age"} <= set(cfg.profiles)


def test_stats_are_not_all_given_one_rate():
    cfg = get_growth_config()
    assert len({m.chance for m in cfg.stat_rates.values()}) > 1


@pytest.mark.parametrize("patch, fragment", [
    ({"profiles": {"x": {"stages": ["nope"], "stat_chance": 0.1, "breakthrough": {"chance": 0, "percent": [0.1, 0.1], "max_amount": 1, "max_per_stat": 0}}}}, "not a life stage"),
    ({"species": {"gnome": {}}}, "unknown id 'gnome'"),
    ({"traits": {"nope": {}}}, "unknown id 'nope'"),
    ({"experiences": {"X Bad": {}}}, "must be lowercase"),
    ({"experiences": {"fencer": {"trains": ["juggling"]}}}, "unknown specialised stat"),
    ({"caps": {"default": 80, "stats": {"luck": 90}}}, "unknown stat 'luck'"),
])
def test_invalid_config_is_reported(tmp_path, patch, fragment):
    import yaml
    from app.progression import GROWTH_FILE
    raw = yaml.safe_load(GROWTH_FILE.read_text())
    raw.update(patch)
    path = tmp_path / "npc_growth.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ContentError) as e:
        load_growth_config(path=path)
    assert fragment in str(e.value)


def test_a_stage_with_no_profile_is_rejected(tmp_path):
    import yaml
    from app.progression import GROWTH_FILE
    raw = yaml.safe_load(GROWTH_FILE.read_text())
    raw["profiles"]["adulthood"]["stages"] = ["young_adult"]
    path = tmp_path / "g.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ContentError, match="'adulthood' has no growth profile"):
        load_growth_config(path=path)


# ---------- age-aware starting development ----------

def test_adults_start_more_developed_than_newborns_on_average():
    def mean_total(age):
        npcs = [person(age=age, seed=s) for s in range(60)]
        return statistics.mean(sum(n.stats.values()) for n in npcs)
    newborn, adult = mean_total(0), mean_total(35)
    assert adult > newborn + 5


def test_a_newborn_is_not_developed_at_all():
    n = person(age=0, seed=4)
    flat = FACTORY.generate(NpcSpec(category="persistent", species="human", age_range=(0, 0), develop=False), seed=4)
    assert n.stats == flat.stats and n.progress.developed_age == 0


def test_initial_development_is_modest_not_decades_of_instant_growth():
    n = person(age=60, seed=2)
    flat = FACTORY.generate(NpcSpec(category="persistent", species="human", age_range=(60, 60), develop=False), seed=2)
    gains = [n.stats[s] - flat.stats[s] for s in STATS]
    assert max(gains) <= 40 and all(v <= get_growth_config().caps.default for v in n.stats.values())


def test_initial_development_is_recorded_so_it_is_never_applied_twice():
    n = person(age=30, seed=3)
    before = dict(n.stats)
    assert n.progress.developed_age == 30
    assert catch_up(n, CT, get_growth_config(), PCFG) == [] and n.stats == before


def test_exact_stats_are_final_and_birth_stats_are_developed():
    exact = {s: 33 for s in STATS}
    a = FACTORY.generate(NpcSpec(category="persistent", species="human", age_range=(40, 40), stats=exact), seed=5)
    assert a.stats == exact
    b = FACTORY.generate(NpcSpec(category="persistent", species="human", age_range=(40, 40), birth_stats=exact), seed=5)
    assert b.stats != exact and sum(b.stats.values()) >= sum(exact.values())


def test_a_light_npc_is_developed_the_same_when_generated_later():
    light = FACTORY.generate(NpcSpec(species="human", age_range=(30, 30)), seed=8)       # generic: nothing generated yet
    assert light.stats is None
    from app.npc import ensure_all, to_character
    seen = to_character(light).stats
    ensure_all(light)
    assert light.stats == seen and light.progress.developed_age == 30


def test_development_is_species_scaled():
    wolf = person("wolf", age=6, seed=1)             # a wolf at 6 is middle-aged: its stages follow its lifespan
    assert wolf.progress.developed_age == 6 and all(1 <= v <= CT.species["wolf"].stat_cap for v in wolf.stats.values())


# ---------- probabilities, amounts, caps ----------

def test_chance_one_changes_every_stat_by_the_small_amount():
    cfg = growth_config(stat_chance=1.0, small=(2, 2))
    n = growing(10)
    before = dict(n.stats)
    tick(n, 1, cfg)
    assert all(n.stats[s] == before[s] + 2 for s in STATS)


def test_chance_zero_changes_nothing():
    cfg = growth_config(stat_chance=0.0)
    n = growing(10)
    before = dict(n.stats)
    tick(n, 5, cfg)
    assert n.stats == before


def test_breakthrough_is_a_percentage_bounded_by_max_amount():
    cfg = growth_config(stat_chance=1.0, breakthrough_chance=1.0, percent=(0.5, 0.5), max_amount=7, max_per_stat=5)
    n = growing(10)
    before = dict(n.stats)
    tick(n, 1, cfg)
    for s in STATS:
        assert n.stats[s] - before[s] == min(7, max(1, round(before[s] * 0.5)))


def test_breakthroughs_are_limited_per_stat_for_a_lifetime():
    cfg = growth_config(stat_chance=1.0, breakthrough_chance=1.0, max_per_stat=2, small=(1, 1), max_amount=3, cap=200)
    n = growing(10)
    tick(n, 6, cfg)
    assert all(v == 2 for v in n.progress.breakthroughs.values())


def test_stats_never_pass_the_cap_and_growth_tapers_towards_it():
    cfg = growth_config(stat_chance=1.0, small=(5, 5), breakthrough_chance=1.0, max_amount=20, cap=60, taper_from=0.5)
    n = growing(10)
    before = dict(n.stats)
    tick(n, 40, cfg)
    assert all(n.stats[s] <= max(60, before[s]) for s in STATS)      # a cap stops growth; it never shrinks a stat
    assert any(n.stats[s] > before[s] for s in STATS)                  # and something did grow before reaching it


def test_the_species_hard_cap_always_wins():
    cfg = growth_config(stat_chance=1.0, small=(9, 9), cap=500)
    n = growing(10)
    tick(n, 50, cfg)
    assert all(v <= CT.species["human"].stat_cap for v in n.stats.values())


def test_no_exponential_inflation_over_a_long_life():
    n = growing(0, seed=7)
    tick(n, 400, get_growth_config())
    assert all(v <= get_growth_config().caps.default + 20 for v in n.stats.values())


def test_species_and_stat_caps_are_configurable():
    cfg = get_growth_config()
    drake, human = person("dragonkin", 45, 1), person("human", 30, 1)
    assert stat_cap(drake, "strength", cfg, CT) > stat_cap(human, "strength", cfg, CT)
    assert stat_cap(human, "strength", cfg, CT) <= CT.species["human"].stat_cap


def test_old_age_declines_the_configured_stats_only():
    cfg = growth_config(stat_chance=0.0, decline={"chance": 1.0, "amount": [2, 2], "stats": ["constitution"]})
    n = growing(70)
    before = dict(n.stats)
    tick(n, 1, cfg)
    assert n.stats["constitution"] == max(1, before["constitution"] - 2)
    assert all(n.stats[s] == before[s] for s in STATS if s != "constitution")


def test_a_decline_never_takes_a_stat_below_one():
    cfg = growth_config(stat_chance=0.0, decline={"chance": 1.0, "amount": [9, 9], "stats": ["constitution"]})
    n = growing(70)
    tick(n, 30, cfg)
    assert n.stats["constitution"] == 1


def test_age_ranges_use_the_life_stage_profiles():
    two = GrowthConfig.model_validate({
        "profiles": {
            "young": {"stages": ["infancy", "early_childhood", "childhood", "adolescence"], "stat_chance": 1.0,
                      "breakthrough": {"chance": 0, "percent": [0.1, 0.1], "max_amount": 1, "max_per_stat": 0}},
            "rest": {"stages": ["young_adult", "adulthood", "middle_age", "elder"], "stat_chance": 0.0,
                     "breakthrough": {"chance": 0, "percent": [0.1, 0.1], "max_amount": 1, "max_per_stat": 0}}},
        "trait_acquisition": {"enabled": False}})
    kid, adult = growing(5, seed=3), growing(40, seed=3)
    k0, a0 = dict(kid.stats), dict(adult.stats)
    tick(kid, 1, two)
    tick(adult, 1, two)
    assert kid.stats != k0 and adult.stats == a0


# ---------- exactly once per year ----------

def test_growth_happens_once_per_age_however_often_it_is_called():
    cfg = growth_config(stat_chance=1.0, small=(1, 1))
    n = growing(10)
    n.age = 11
    first = catch_up(n, CT, cfg, PCFG)
    after_first = dict(n.stats)
    again = [catch_up(n, CT, cfg, PCFG) for _ in range(3)]
    assert len(first) == 1 and all(a == [] for a in again) and n.stats == after_first
    assert n.progress.developed_age == 11


def test_catching_up_several_years_applies_each_exactly_once():
    cfg = growth_config(stat_chance=1.0, small=(1, 1))
    n = growing(10)
    before = dict(n.stats)
    n.age = 14
    reports = catch_up(n, CT, cfg, PCFG)
    assert [r.age for r in reports] == [11, 12, 13, 14]
    assert all(n.stats[s] == before[s] + 4 for s in STATS)


def test_replaying_the_same_saved_state_gives_the_same_result():
    cfg = get_growth_config()
    a, b = growing(5, seed=11), growing(5, seed=11)
    saved = a.model_dump(mode="json")
    tick(a, 30, cfg)
    c = Npc.model_validate(saved)
    tick(c, 30, cfg)
    tick(b, 30, cfg)
    assert a.stats == b.stats == c.stats and a.progress == c.progress


def test_a_reloaded_save_does_not_grow_again():
    cfg = growth_config(stat_chance=1.0)
    n = growing(10)
    n.age = 11
    catch_up(n, CT, cfg, PCFG)
    reloaded = Npc.model_validate(n.model_dump(mode="json"))
    assert catch_up(reloaded, CT, cfg, PCFG) == [] and reloaded.stats == n.stats


def test_the_dead_and_seedless_do_not_grow():
    cfg = growth_config(stat_chance=1.0)
    n = growing(10)
    n.mark_deceased("illness")
    n.age = 12
    assert catch_up(n, CT, cfg, PCFG) == []


# ---------- modifiers: species, traits, talents, experiences ----------

def _chance_cfg(**extra):
    return growth_config(stat_chance=0.5, small=(1, 1), **extra)


def _grown_total(npc, cfg, years=80):
    before = sum(npc.stats.values())
    tick(npc, years, cfg)
    return sum(npc.stats.values()) - before


def test_a_species_modifier_changes_growth_for_that_species_only():
    cfg = _chance_cfg(species={"elf": {"chance": 0.0}})
    elf, human = person("elf", 10, 2), person("human", 10, 2)
    assert _grown_total(elf, cfg, 20) == 0 and _grown_total(human, cfg, 20) > 0


def test_per_stat_modifiers_target_single_stats():
    cfg = growth_config(stat_chance=1.0, species={"human": {"stats": {"wisdom": {"chance": 0.0}}}})
    n = person("human", 10, 2)
    before = dict(n.stats)
    tick(n, 1, cfg)
    assert n.stats["wisdom"] == before["wisdom"] and n.stats["strength"] == before["strength"] + 1


def test_a_trait_can_speed_up_or_stop_learning():
    cfg = growth_config(stat_chance=1.0, traits={"generalist": {"chance": 0.0}})
    n = person("human", 10, 2, traits=["generalist"])
    before = dict(n.stats)
    tick(n, 3, cfg)
    assert n.stats == before


def test_a_talent_modifier_applies_when_the_npc_holds_that_talent():
    cfg = growth_config(stat_chance=1.0, talents={"master_of_none": {"chance": 0.0}})
    n = person("human", 10, 2)
    n.talent = "master_of_none"
    before = dict(n.stats)
    tick(n, 2, cfg)
    assert n.stats == before


def test_an_experience_modifier_applies_only_once_the_npc_has_it():
    cfg = growth_config(stat_chance=0.0, experiences={"education": {"chance": 1.0}})
    n = growing(10)
    before = dict(n.stats)
    tick(n, 2, cfg)
    assert n.stats == before                                    # chance 0 x 1.0 stays 0
    cfg2 = growth_config(stat_chance=0.5, small=(1, 1), experiences={"education": {"chance": 0.0}})
    a, b = growing(10, seed=4), growing(10, seed=4)
    add_experience(b, "education", cfg2)
    assert _grown_total(a, cfg2, 20) > 0 and _grown_total(b, cfg2, 20) == 0


def test_experiences_can_be_added_removed_and_are_validated():
    cfg = get_growth_config()
    n = growing(20)
    assert add_experience(n, "education", cfg) is True and add_experience(n, "education", cfg) is False
    assert n.progress.experiences == ["education"]
    assert remove_experience(n, "education") is True and remove_experience(n, "education") is False
    with pytest.raises(ProgressionError):
        add_experience(n, "wizard_school", cfg)


# ---------- specialised stats stay separate ----------

def test_specialised_stats_grow_only_when_already_trained():
    cfg = growth_config(stat_chance=0.0, specialised_chance=1.0)
    plain = person("human", 25, 3, combat={})
    trained = person("human", 25, 3, combat={"swordsmanship": 10.0})
    tick(plain, 3, cfg)
    tick(trained, 3, cfg)
    assert "swordsmanship" not in plain.skills
    assert trained.skills["swordsmanship"] > 10.0
    from app.npc import npc_power
    assert npc_power(trained).total > 0


def test_an_experience_can_train_a_specialised_stat_from_zero():
    cfg = growth_config(stat_chance=0.0, specialised_chance=1.0, experiences={"soldier": {"trains": ["swordsmanship"]}})
    n = person("human", 25, 3, combat={})
    add_experience(n, "soldier", cfg)
    tick(n, 2, cfg)
    assert n.skills["swordsmanship"] == 2.0


def test_a_species_cannot_grow_a_skill_it_cannot_train():
    cfg = growth_config(stat_chance=0.0, specialised_chance=1.0, experiences={"soldier": {"trains": ["swordsmanship"]}})
    wolf = person("wolf", 5, 1)
    add_experience(wolf, "soldier", cfg)
    tick(wolf, 3, cfg)
    assert "swordsmanship" not in wolf.skills


def test_specialised_growth_does_not_touch_base_stats_and_respects_its_cap():
    cfg = growth_config(stat_chance=0.0, specialised_chance=1.0)
    n = person("human", 25, 3, combat={"magic": 59.0})
    base = dict(n.stats)
    tick(n, 5, cfg)
    assert n.stats == base and n.skills["magic"] <= 60.0


def test_power_comes_from_the_existing_power_system_after_growth():
    from app.npc import to_character
    from app.power import calculate_power
    cfg = growth_config(stat_chance=0.0, specialised_chance=1.0)
    n = person("human", 25, 3, combat={"swordsmanship": 10.0})
    tick(n, 2, cfg)
    from app.npc import npc_power
    assert npc_power(n).total == calculate_power(to_character(n), CT, PCFG).total


# ---------- life events ----------

def test_a_life_event_applies_exactly_once():
    n = growing(30, seed=3)
    before = n.stats["strength"]
    r1 = apply_life_event(n, "duel:won_1", stats={"strength": 5}, traits=["iron_constitution"], experience="mentored")
    r2 = apply_life_event(n, "duel:won_1", stats={"strength": 5}, traits=["iron_constitution"], experience="mentored")
    assert r1.applied and not r2.applied
    assert n.stats["strength"] == min(CT.species["human"].stat_cap, before + 5)
    assert n.traits.count("iron_constitution") == 1 and n.progress.experiences == ["mentored"]


def test_a_life_event_survives_a_reload_without_repeating():
    n = growing(30, seed=3)
    apply_life_event(n, "found_relic", stats={"wisdom": 4})
    reloaded = Npc.model_validate(n.model_dump(mode="json"))
    wisdom = reloaded.stats["wisdom"]
    assert apply_life_event(reloaded, "found_relic", stats={"wisdom": 4}).applied is False
    assert reloaded.stats["wisdom"] == wisdom


def test_a_life_event_respects_trait_prerequisites_and_validates_input():
    n = growing(30, seed=3)
    assert apply_life_event(n, "curse", traits=["worldrender"]).traits_gained == []        # needs adversary_instinct
    with pytest.raises(ProgressionError):
        apply_life_event(n, "bad", stats={"luck": 1})
    with pytest.raises(ProgressionError):
        apply_life_event(n, "Bad Key")
    with pytest.raises(ProgressionError):
        apply_life_event(n, "x", traits=["no_such_trait"])


def test_acquired_traits_come_from_the_normal_rules_and_only_once():
    cfg = growth_config(stat_chance=0.0)
    cfg = cfg.model_copy(update={"trait_acquisition": cfg.trait_acquisition.model_copy(update={"enabled": True, "chance_scale": 1.0})})
    n = person("human", 30, 3, traits=[], stats={**{s: 60 for s in STATS}}, combat={})
    n.skills["stealth"] = 10.0
    tick(n, 60, cfg)
    assert n.traits.count("fleet_footwork") <= 1
    assert "fleet_footwork" in n.traits                         # dexterity 60 and stealth 10 meet its rule, 15% a year
