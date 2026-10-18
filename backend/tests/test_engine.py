import random
from app.engine import new_character, advance_year


def test_character_eventually_dies():
    rng = random.Random(42)
    c = new_character("Aren", rng)
    while c.alive:
        advance_year(c, rng, focus_skill="swordsmanship")
    assert c.age > 0 and c.cause_of_death
    assert len(c.history) == c.age


def test_same_seed_gives_same_life():
    def live(seed):
        rng = random.Random(seed)
        c = new_character("Aren", rng)
        while c.alive:
            advance_year(c, rng)
        return c.age, c.cause_of_death

    assert live(7) == live(7)
