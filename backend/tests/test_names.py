"""Fantasy name generation: appropriate to the species, reproducible, repeatable unless uniqueness is requested."""
import random
import statistics

import pytest

from app.content import ContentError, get_content
from app.names import (
    NameConfig, NameExhaustedError, NameGenerator, get_name_config, get_name_generator, load_name_config,
)

CT = get_content()
CFG = get_name_config()
GEN = get_name_generator()


def names(species, n=300, seed=1, **kw):
    rng = random.Random(seed)
    return [GEN.generate(rng=rng, species=species, **kw) for _ in range(n)]


# ---------- the shipped rules ----------

def test_the_shipped_name_rules_are_valid_and_cover_every_species():
    load_name_config(CT.species)
    assert set(CFG.species_cultures) <= set(CT.species)
    for species in CT.species:
        assert GEN.culture_for(species) in CFG.cultures
    assert GEN.culture_for("a_species_added_later") == CFG.default_culture


@pytest.mark.parametrize("species", sorted(CT.species))
def test_names_are_tidy_for_every_species(species):
    culture = CFG.cultures[GEN.culture_for(species)]
    for n in names(species):
        for part, rules in ((n.given, culture.given), (n.surname, culture.surname)):
            if part is None:
                continue
            assert part.isalpha() and part[0].isupper() and part[1:].islower()
            assert rules.length[0] <= len(part) <= rules.length[1]
            assert not any(part.lower()[i] == part.lower()[i + 1] == part.lower()[i + 2] for i in range(len(part) - 2))


@pytest.mark.parametrize("species", sorted(CT.species))
def test_names_are_built_from_that_cultures_own_parts(species):
    given = CFG.cultures[GEN.culture_for(species)].given
    for n in names(species):
        low = n.given.lower()
        assert any(low.startswith(p.lower()) for p in given.prefixes), n.given
        assert any(low.endswith(s.lower()) for s in given.suffixes), n.given


def test_each_species_sounds_like_itself():
    """Every species opens its names with its own culture's prefixes, and the cultures do not share names."""
    species = ("elf", "dwarf", "dragonkin", "wolf", "slime")
    drawn = {sp: {n.given for n in names(sp, n=400)} for sp in species}
    for sp in species:
        opening = tuple(p.lower() for p in CFG.cultures[GEN.culture_for(sp)].given.prefixes)
        assert all(n.lower().startswith(opening) for n in drawn[sp]), sp
    for i, a in enumerate(species):
        for b in species[i + 1:]:
            assert not drawn[a] & drawn[b], f"{a} and {b} share names: {sorted(drawn[a] & drawn[b])[:3]}"


# ---------- surnames ----------

def test_surname_chance_follows_the_culture():
    human = names("human", n=2000)
    rate = statistics.mean(n.surname is not None for n in human)
    assert rate == pytest.approx(CFG.cultures["human"].surname.chance, abs=0.04)
    assert all(n.surname is None for n in names("wolf") + names("slime"))          # chance 0 for beasts


def test_surname_can_be_forced_off_forced_on_or_supplied():
    rng = random.Random(3)
    assert all(GEN.generate(rng=rng, species="human", with_surname=False).surname is None for _ in range(50))
    assert all(GEN.generate(rng=rng, species="human", with_surname=True).surname for _ in range(50))
    family = GEN.generate(rng=rng, species="dwarf", surname="Ironvale")
    assert family.surname == "Ironvale" and family.full == f"{family.given} Ironvale"


def test_full_name_joins_given_and_surname():
    n = GEN.generate(rng=random.Random(2), species="elf", with_surname=True)
    assert n.full == f"{n.given} {n.surname}" == str(n)


# ---------- reproducibility ----------

def test_the_same_seed_gives_the_same_names_and_other_seeds_differ():
    assert names("elf", seed=9) == names("elf", seed=9)
    assert names("elf", seed=9) != names("elf", seed=10)


# ---------- repeats are allowed, uniqueness is opt-in ----------

def test_display_names_may_repeat_by_default():
    drawn = [n.full for n in names("slime", n=2000)]
    assert len(set(drawn)) < len(drawn)


def test_taken_names_are_never_repeated_and_the_check_ignores_case():
    rng = random.Random(1)
    first = GEN.generate(rng=random.Random(5), species="human", with_surname=False)
    for _ in range(200):
        again = GEN.generate(rng=rng, species="human", with_surname=False, taken=[first.full.upper()])
        assert again.full != first.full


def test_unique_batches_have_no_duplicates():
    batch = GEN.generate_many(600, rng=random.Random(4), species="human", unique=True)
    assert len({n.full.casefold() for n in batch}) == 600
    pre = {n.full for n in names("slime", n=20, seed=7)}
    more = GEN.generate_many(40, rng=random.Random(8), species="slime", unique=True, taken=pre)
    assert not pre & {n.full for n in more}


def test_an_exhausted_name_space_raises_instead_of_looping_or_repeating():
    tiny = NameGenerator(NameConfig.model_validate({
        "default_culture": "t", "cultures": {"t": {"given": {"prefixes": ["Ba"], "suffixes": ["ro"]}}}}))
    only = tiny.generate(rng=random.Random(1))
    assert only.full == "Baro"
    with pytest.raises(NameExhaustedError):
        tiny.generate(rng=random.Random(1), taken=[only.full])


def test_a_crowded_name_space_widens_with_surnames_when_uniqueness_requires_it():
    small = NameGenerator(NameConfig.model_validate({"default_culture": "t", "cultures": {"t": {
        "given": {"prefixes": ["Ba", "Be"], "suffixes": ["ro"]},
        "surname": {"chance": 0, "prefixes": ["Oak", "Elm", "Ash"], "suffixes": ["wood", "field"]}}}}))
    drawn = small.generate_many(12, rng=random.Random(2), unique=True)
    assert len({n.full for n in drawn}) == 12 and any(n.surname for n in drawn)


# ---------- bad rules are rejected ----------

def write(tmp_path, text):
    p = tmp_path / "names.yaml"
    p.write_text(text)
    return p


GOOD = "default_culture: a\ncultures:\n  a:\n    given: {prefixes: [Ba], suffixes: [ro]}\n"


@pytest.mark.parametrize("text, message", [
    ("default_culture: nope\ncultures:\n  a:\n    given: {prefixes: [Ba], suffixes: [ro]}\n", "default_culture"),
    (GOOD + "species_cultures: {human: missing}\n", "unknown culture"),
    ("default_culture: a\ncultures:\n  a:\n    given: {prefixes: ['B-a'], suffixes: [ro]}\n", "letters only"),
    ("default_culture: a\ncultures:\n  a:\n    given: {prefixes: [Ba], suffixes: [ro], length: [1, 3]}\n", "shortest possible"),
    ("default_culture: a\ncultures:\n  a:\n    given: {prefixes: [Ba], suffixes: [ro], middle: [0, 2]}\n", "no syllables|none"),
    ("default_culture: a\ncultures:\n  a:\n    given: {prefixes: [Ba], suffixes: [ro], colour: red}\n", "colour"),
])
def test_invalid_name_rules_are_reported(tmp_path, text, message):
    with pytest.raises(ContentError, match=message):
        load_name_config(path=write(tmp_path, text))


def test_species_named_in_the_rules_must_exist(tmp_path):
    p = write(tmp_path, GOOD + "species_cultures: {dragon_of_doom: a}\n")
    load_name_config(path=p)                                                   # fine without a species list
    with pytest.raises(ContentError, match="unknown species"):
        load_name_config(CT.species, path=p)


def test_unknown_culture_is_an_error():
    with pytest.raises(KeyError):
        GEN.generate(rng=random.Random(1), culture="atlantean")
