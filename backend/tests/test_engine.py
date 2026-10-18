import random

import pytest

from app.engine import (
    advance_year, load_skills, new_character, offer_choices, summarize, year_rng,
)


def live_to_death(seed, choice=None):
    rng = random.Random(seed)
    c = new_character("Aren", rng)
    while c.alive:
        advance_year(c, year_rng(seed, c.age), choice)
    return c


def test_character_eventually_dies():
    c = live_to_death(42, "train:swordsmanship")
    assert c.age > 0 and c.cause_of_death
    assert len(c.history) == c.age
    assert c.skills["swordsmanship"] > 0


def test_same_seed_gives_same_life():
    a, b = live_to_death(7), live_to_death(7)
    assert (a.age, a.cause_of_death, a.stats) == (b.age, b.cause_of_death, b.stats)


def test_skills_are_loaded_from_json():
    skills = load_skills()
    assert skills["necromancy"] == "magic"
    assert len(skills) == 28


def test_choices_match_age():
    c = new_character("Aren", random.Random(1))
    assert [ch["id"] for ch in offer_choices(c, 1)] == ["play"]
    c.age = 20
    ids = [ch["id"] for ch in offer_choices(c, 1)]
    assert "work" in ids and "rest" in ids and len(ids) == 4
    assert offer_choices(c, 1) == offer_choices(c, 1)   # deterministic


def test_stats_stay_in_range():
    c = live_to_death(3, "rest")
    assert all(1 <= v <= 100 for v in c.stats.values())


def test_cannot_advance_dead_character():
    c = live_to_death(5)
    with pytest.raises(ValueError):
        advance_year(c, random.Random(0))


def test_unknown_choice_rejected():
    c = new_character("Aren", random.Random(1))
    with pytest.raises(ValueError):
        advance_year(c, random.Random(0), "train:not_a_skill")


def test_summary():
    c = live_to_death(11, "train:archery")
    s = summarize(c)
    assert s["defining_skill"] == "Archery" and s["cause_of_death"]
