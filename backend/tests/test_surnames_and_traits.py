"""Surname inheritance and fallback naming; configurable trait inheritance, birth traits versus acquired ones."""
import copy

import pytest
from sqlmodel import Session

from app import relationships as rel
from app.character import Character
from app.content import ContentError, TraitInheritance, get_content
from app.descendants import create_descendant
from app.inheritance import (
    InheritanceRules, ParentSource, TraitRules, get_inheritance_rules, is_inheritable, load_inheritance_rules,
    plan_inheritance, trait_chance, trait_origin,
)
from app.names import get_name_generator
from app.npc import Npc, NpcFactory, NpcSpec
from app.npc_store import save_npc
from app.surnames import choose_surname, derive_surname, parent_surname
from tests.progress_helpers import new_db, person

CT = get_content()
NAMES = get_name_generator()
RULES = get_inheritance_rules()
CID = 1


def src(ref, name="Aren Brookwood", species="human", surname="__derive__", **kw):
    sn = parent_surname(None, name) if surname == "__derive__" else surname
    return ParentSource(ref, Character(name=name, species_id=species, age=40, stats={s: 50 for s in CT.species[species].stat_modifiers} or {}, **kw), sn)


def with_rules(**naming):
    r = RULES.model_copy(deep=True)
    for k, v in naming.items():
        setattr(r.naming.surname, k, v)
    return r


@pytest.fixture
def db():
    with Session(new_db()) as s:
        yield s


def keep(db, **kw):
    n = person(age=40, **kw)
    save_npc(db, CID, n)
    return n


# ---------- surnames ----------

def test_a_child_takes_the_first_parents_surname_by_default():
    c = choose_surname([src("a", "Mara Ashford"), src("b", "Tor Greenwood")], "human", 1, RULES, NAMES)
    assert c.surname == "Ashford" and c.source == "a"


def test_the_second_parent_is_used_when_the_first_has_no_surname():
    c = choose_surname([src("a", "Mara", surname=None), src("b", "Tor Greenwood")], "human", 1, RULES, NAMES)
    assert c.surname == "Greenwood" and c.source == "b"


def test_random_parent_mode_is_seeded():
    rules = with_rules(mode="random_parent")
    ps = [src("a", "Mara Ashford"), src("b", "Tor Greenwood")]
    picks = {s: choose_surname(ps, "human", s, rules, NAMES).surname for s in range(40)}
    assert set(picks.values()) == {"Ashford", "Greenwood"}
    assert all(choose_surname(ps, "human", s, rules, NAMES).surname == v for s, v in picks.items())


def test_mode_none_and_per_culture_overrides_stop_inheritance():
    ps = [src("a", "Mara Ashford")]
    assert choose_surname(ps, "human", 1, with_rules(mode="none", fallback="none"), NAMES).surname is None
    per = with_rules(cultures={"human": "none"}, fallback="none")
    assert choose_surname(ps, "human", 1, per, NAMES).with_surname is False


def test_fallback_generate_defers_to_the_name_generator_and_none_goes_without():
    ps = [src("a", "Mara", surname=None)]
    assert choose_surname(ps, "human", 1, RULES, NAMES).with_surname is None
    assert choose_surname(ps, "human", 1, with_rules(fallback="none"), NAMES).with_surname is False


def test_cultures_without_family_names_never_get_one():
    c = choose_surname([src("a", "Mara Ashford")], "wolf", 1, RULES, NAMES)
    assert c.surname is None and c.with_surname is False
    assert choose_surname([src("a", "Ashpaw", species="wolf", surname=None)], "slime", 1, RULES, NAMES).surname is None


def test_cross_culture_parents_follow_the_setting():
    elf_parent = [src("a", "Aelwen Silversong", species="elf")]
    assert choose_surname(elf_parent, "human", 1, RULES, NAMES).surname == "Silversong"
    assert choose_surname(elf_parent, "human", 1, with_rules(cross_culture="fallback"), NAMES).surname is None


def test_names_with_odd_shapes_are_handled():
    assert derive_surname("Aelwen") is None and derive_surname("") is None and derive_surname(None) is None
    assert derive_surname("Mary Jane Watson") == "Watson"
    assert parent_surname("  ", "Mara") is None and parent_surname("Stone", "Mara Ash") == "Stone"


def test_a_descendant_inherits_a_parents_surname_into_its_name_and_field(db):
    p = keep(db, seed=3, surname="Ashford")
    assert p.surname == "Ashford"
    d = create_descendant(db, CID, [p.id], seed=5)
    assert d.npc.surname == "Ashford" and d.npc.name.endswith(" Ashford") and d.npc.id.startswith("npc_")
    assert "Ashford" not in d.npc.id


def test_a_descendant_of_a_playable_character_uses_the_characters_name(db):
    from app import engine as sim
    from app.models import CharacterRecord
    pc = CharacterRecord.from_character(CID, sim.new_character("Aren Brookwood", "human", 1))
    pc.age = 30
    db.add(pc)
    db.flush()
    d = create_descendant(db, CID, [rel.player_ref(pc)], seed=6)
    assert d.npc.surname == "Brookwood"


def test_a_single_named_parent_gives_a_generated_surname_by_culture_chance(db):
    p = keep(db, seed=4, with_surname=False)
    assert p.surname is None
    surnames = {create_descendant(db, CID, [p.id], seed=100 + i).npc.surname for i in range(25)}
    assert any(surnames) and len(surnames) > 1                     # drawn by the culture's own chance, not copied


def test_wolf_children_have_a_single_name(db):
    p = person("wolf", age=6, seed=2)
    save_npc(db, CID, p)
    d = create_descendant(db, CID, [p.id], seed=9)
    assert d.npc.surname is None and " " not in d.npc.name


def test_surname_inheritance_does_not_change_the_unique_id(db):
    p = keep(db, seed=5, surname="Ashford")
    a = create_descendant(db, CID, [p.id], seed=7)
    again = create_descendant(db, CID, [p.id], seed=7)
    assert again.created is False and again.npc.id == a.npc.id


def test_the_name_generator_still_works_without_any_family_logic():
    import random
    n = NAMES.generate(rng=random.Random(1), species="human")
    assert n.given and (n.surname is None or n.full.endswith(n.surname))


def test_surname_rules_validate_their_culture_names(tmp_path):
    import yaml
    from app.inheritance import INHERITANCE_FILE
    raw = yaml.safe_load(INHERITANCE_FILE.read_text())
    raw["naming"]["surname"]["cultures"] = {"atlantean": "none"}
    path = tmp_path / "i.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ContentError, match="unknown name culture"):
        load_inheritance_rules(path=path)


# ---------- trait inheritance ----------

def ct_with(**inheritance):
    ct = copy.deepcopy(CT)
    for trait_id, patch in inheritance.items():
        ct.traits[trait_id].inheritance = TraitInheritance(**patch)
    return ct


def test_not_every_trait_is_inheritable_by_default():
    rules = RULES.traits
    inheritable = {t for t in CT.traits if is_inheritable(t, rules, CT)}
    assert inheritable == {t for t, d in CT.traits.items() if d.birth is not None}
    assert "vampiric_edge" not in inheritable and "worldrender" not in inheritable and "second_wind" not in inheritable


def test_birth_and_acquired_traits_are_told_apart():
    assert trait_origin(CT.traits["iron_constitution"]) == "birth"
    assert trait_origin(CT.traits["second_wind"]) == "acquired"
    assert trait_origin(CT.traits["fleet_footwork"]) == "both"
    assert trait_origin(CT.traits["regenerative"]) == "granted"


def test_extra_still_adds_a_trait_and_exclude_still_wins():
    assert is_inheritable("second_wind", TraitRules(extra=["second_wind"]), CT)
    assert not is_inheritable("iron_constitution", TraitRules(exclude=["iron_constitution"]), CT)
    assert not is_inheritable("iron_constitution", TraitRules(extra=["iron_constitution"], exclude=["iron_constitution"]), CT)


def test_a_trait_definition_can_opt_in_or_out():
    ct = ct_with(second_wind={"inheritable": True}, iron_constitution={"inheritable": False})
    assert is_inheritable("second_wind", RULES.traits, ct) and not is_inheritable("iron_constitution", RULES.traits, ct)
    assert not is_inheritable("iron_constitution", TraitRules(extra=["iron_constitution"]), ct)      # the definition wins over extra


def test_chance_comes_from_overrides_then_the_definition_then_the_tier():
    ct = ct_with(keen_sense={"chance": 0.9})
    assert trait_chance("keen_sense", TraitRules(), ct) == 0.9
    assert trait_chance("keen_sense", TraitRules(overrides={"keen_sense": 0.4}), ct) == 0.4
    assert trait_chance("iron_constitution", TraitRules(), ct) == TraitRules().chance["common"]


def test_a_certain_trait_is_always_passed_and_an_impossible_one_never():
    ct = ct_with(iron_constitution={"chance": 1.0}, keen_sense={"chance": 0.0})
    parent = src("p", "Dur Stoneforge", traits=["iron_constitution", "keen_sense"])
    for seed in range(15):
        plan = plan_inheritance([parent], seed, ct=ct)
        assert "iron_constitution" in plan.traits and "keen_sense" not in plan.traits


def test_an_acquired_only_trait_is_not_passed_even_when_the_parent_has_it():
    parent = src("p", "Mara Ash", traits=["second_wind", "aegis_of_titan"])
    assert all(plan_inheritance([parent], s).traits == [] for s in range(30))


def test_extra_makes_an_acquired_trait_inheritable():
    rules = RULES.model_copy(deep=True)
    rules.traits.extra = ["second_wind"]
    rules.traits.overrides = {"second_wind": 1.0}
    parent = src("p", "Mara Ash", traits=["second_wind"])
    assert plan_inheritance([parent], 1, rules=rules).traits == ["second_wind"]


def test_inherited_traits_must_meet_their_prerequisites():
    rules = RULES.model_copy(deep=True)
    rules.traits.extra = ["worldrender"]                    # needs adversary_instinct
    rules.traits.overrides = {"worldrender": 1.0}
    parent = src("p", "Mara Ash", traits=["worldrender"])
    assert all("worldrender" not in plan_inheritance([parent], s, rules=rules).traits for s in range(10))


def test_the_extra_list_is_checked_against_trait_definitions(tmp_path):
    import yaml
    from app.inheritance import INHERITANCE_FILE
    raw = yaml.safe_load(INHERITANCE_FILE.read_text())
    raw["traits"]["extra"] = ["no_such_trait"]
    path = tmp_path / "i.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ContentError, match="unknown trait 'no_such_trait'"):
        load_inheritance_rules(path=path)


def test_a_conflict_between_extra_and_a_definition_is_reported():
    ct = ct_with(second_wind={"inheritable": False})
    rules = RULES.model_copy(deep=True)
    rules.traits.extra = ["second_wind"]
    from app.inheritance import _cross_check
    from app.power import get_power_config
    assert any("inheritable: false" in e for e in _cross_check(rules, ct, get_power_config(), "x"))


def test_a_parent_with_a_removed_trait_is_handled():
    parent = src("p", "Mara Ash", traits=["trait_that_was_removed", "iron_constitution"])
    plan_inheritance([parent], 3)                           # must not raise


def test_existing_trait_definitions_still_load_without_an_inheritance_entry():
    assert all(d.inheritance == TraitInheritance() for d in CT.traits.values())
