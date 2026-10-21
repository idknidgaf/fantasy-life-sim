"""NPC generation: categories, identity, valid stats, reproducibility, authored characters, Power compatibility."""
import json
import random

import pytest
from pydantic import ValidationError

from app import engine as sim
from app.character import Character
from app.content import STATS, ContentError, get_content
from app.npc import (
    CATEGORY_DETAIL, CharacterCategory, DetailLevel, LifecycleStatus, NamedCharacterDef, Npc, NpcFactory,
    NpcSpec, NpcSpecError, default_age_range, ensure_all, ensure_attributes, load_named_characters,
    named_npc, new_id, npc_power, specialised_values, to_character,
)
from app.power import calculate_power, get_power_config
from app.rules import effective_stat, stage_of

CT = get_content()
CFG = get_power_config()
FACTORY = NpcFactory()
FOUR = {"magic", "swordsmanship", "marksmanship", "martial_arts"}
GENERIC = NpcSpec()
PERSISTENT = NpcSpec(category="persistent")


def gen(spec=GENERIC, seed=1, **kw):
    return FACTORY.generate(spec, seed=seed, **kw)


def dump(npc):
    return npc.model_dump(mode="json")


# ---------- categories ----------

def test_generic_npcs_are_light_and_persistent_npcs_are_complete():
    g, p = gen(GENERIC, 5), gen(PERSISTENT, 5)
    assert (g.category, p.category) == (CharacterCategory.GENERIC, CharacterCategory.PERSISTENT)
    assert CATEGORY_DETAIL[CharacterCategory.GENERIC] == DetailLevel.LIGHT
    assert not (g.has_stats or g.has_combat) and g.traits is None
    assert p.has_stats and p.has_combat and p.traits is not None
    assert g.id and g.name and g.species_id and g.age >= 0                   # identity is always there


def test_detail_can_be_overridden_either_way():
    assert gen(NpcSpec(detail="full"), 5).has_stats
    assert not gen(NpcSpec(category="persistent", detail="light"), 5).has_stats


def test_named_characters_are_never_randomly_generated():
    with pytest.raises(ValidationError):
        NpcSpec(category="named")


def test_category_decides_the_id_prefix():
    assert gen(GENERIC).id.startswith("npc_") and gen(PERSISTENT).id.startswith("npc_")
    with pytest.raises(ValidationError):
        Npc(id="npc_abc", category="named", name="X", species_id="human", age=1)
    with pytest.raises(ValidationError):
        Npc(id="named_abc", category="generic", name="X", species_id="human", age=1)


# ---------- identity ----------

def test_ids_are_unique_and_stable_and_not_the_name():
    npcs = [gen(GENERIC, seed=s) for s in range(500)]
    assert len({n.id for n in npcs}) == 500
    n = npcs[0]
    before = n.id
    n.name = "Someone Else"
    assert n.id == before


def test_two_characters_may_share_a_display_name():
    a = gen(PERSISTENT, 1)
    b = a.model_copy(deep=True, update={"id": new_id(CharacterCategory.PERSISTENT, random.Random(99))})
    assert a.name == b.name and a.id != b.id


def test_the_same_seed_gives_the_same_id_and_a_random_one_is_still_unique():
    assert gen(GENERIC, 7).id == gen(GENERIC, 7).id
    assert new_id(CharacterCategory.GENERIC) != new_id(CharacterCategory.GENERIC)


def test_unique_names_are_opt_in_and_enforced_when_asked():
    batch = FACTORY.generate_many(300, NpcSpec(species="human"), seed=3, unique_names=True)
    assert len({n.name.casefold() for n in batch}) == 300
    taken = {gen(NpcSpec(species="human"), s).name for s in range(20)}
    fresh = [gen(NpcSpec(species="human"), s, taken_names=taken) for s in range(20)]
    assert not taken & {n.name for n in fresh}
    slimes = FACTORY.generate_many(400, NpcSpec(species="slime"), seed=3)          # repeats are allowed
    assert len({n.name for n in slimes}) < 400 and len({n.id for n in slimes}) == 400


def test_names_fit_the_species():
    from app.names import get_name_config, get_name_generator
    gen_names = get_name_generator()
    for species in CT.species:
        given = get_name_config().cultures[gen_names.culture_for(species)].given
        for s in range(20):
            first = gen(NpcSpec(species=species), s).name.split()[0].lower()
            assert first.startswith(tuple(p.lower() for p in given.prefixes))


def test_a_name_culture_can_be_chosen():
    n = gen(NpcSpec(species="human", culture="dwarven"), 4)
    assert n.name.split()[0].lower().startswith(("bor", "dur", "thor", "bal", "dwal", "kaz", "mor", "nor", "brom",
                                                  "bar", "gor", "harn", "rud", "vor", "dag"))


# ---------- reproducibility ----------

def test_the_same_seed_gives_the_same_npc_and_other_seeds_differ():
    for spec in (GENERIC, PERSISTENT):
        assert dump(gen(spec, 11)) == dump(gen(spec, 11))
        assert dump(gen(spec, 11)) != dump(gen(spec, 12))


def test_an_unseeded_npc_records_its_seed_so_it_can_be_reproduced():
    npc = FACTORY.generate(PERSISTENT)
    assert npc.seed is not None
    assert dump(FACTORY.generate(PERSISTENT, seed=npc.seed)) == dump(npc)


def test_lazy_generation_matches_generating_everything_up_front():
    spec = NpcSpec(species="elf", random_traits=2, talent_chance=1, combat_focus=2)
    light, full = gen(spec, 21), gen(spec.model_copy(update={"detail": "full"}), 21)
    assert not light.has_stats
    ensure_all(light)
    assert (light.stats, light.skills, light.traits, light.talent, light.talent_state) == \
           (full.stats, full.skills, full.traits, full.talent, full.talent_state)


def test_sections_can_be_generated_in_any_order_with_the_same_result():
    spec = NpcSpec(species="human", random_traits=2, combat_focus=2)
    a, b = gen(spec, 31), gen(spec, 31)
    ensure_attributes(a, stats=True), ensure_attributes(a, combat=True), ensure_attributes(a, traits=True)
    ensure_attributes(b, traits=True), ensure_attributes(b, combat=True), ensure_attributes(b, stats=True)
    assert dump(a) == dump(b)


def test_only_the_requested_section_is_generated():
    n = gen(GENERIC, 3)
    ensure_attributes(n, combat=True)
    assert n.has_combat and not n.has_stats and n.traits is None


def test_stored_values_are_never_replaced():
    n = gen(PERSISTENT, 3)
    n.stats = {s: 77 for s in STATS}
    ensure_all(n)
    assert set(n.stats.values()) == {77}


def test_a_light_npc_round_trips_through_json_and_still_generates_the_same_details():
    n = gen(NpcSpec(species="dwarf", random_traits=1), 14)
    again = Npc.model_validate(json.loads(json.dumps(dump(n))))
    assert again == n and again.spec == n.spec
    assert dump(ensure_all(again)) == dump(ensure_all(gen(NpcSpec(species="dwarf", random_traits=1), 14)))


# ---------- valid stats ----------

@pytest.mark.parametrize("species", sorted(CT.species))
def test_stats_are_always_valid(species):
    cap = CT.species[species].stat_cap
    for s in range(40):
        n = gen(NpcSpec(category="persistent", species=species, stat_bonus={"strength": 500}), s)
        assert set(n.stats) == set(STATS)
        assert all(isinstance(v, int) and 1 <= v <= cap for v in n.stats.values())
        assert n.stats["strength"] == cap                                  # an over-large bonus is clamped


def test_stat_range_bonus_and_exact_values_are_honoured():
    sp = CT.species["human"]
    n = gen(NpcSpec(category="persistent", species="human", stat_range=(40, 50)), 2)
    assert all(40 + sp.stat_modifiers.get(k, 0) <= v <= 50 + sp.stat_modifiers.get(k, 0) for k, v in n.stats.items())
    plain = gen(NpcSpec(category="persistent", species="human"), 2)
    bonus = gen(NpcSpec(category="persistent", species="human", stat_bonus={"wisdom": 7}), 2)
    assert bonus.stats["wisdom"] == min(100, plain.stats["wisdom"] + 7)
    exact = gen(NpcSpec(category="persistent", species="human", stats={"charisma": 99, "strength": 0}), 2)
    assert exact.stats["charisma"] == 99 and exact.stats["strength"] == 1
    assert exact.stats["wisdom"] == plain.stats["wisdom"]                  # the others still roll


def test_species_modifiers_apply_like_they_do_for_a_playable_character():
    dragon = [gen(NpcSpec(category="persistent", species="dragonkin"), s).stats["strength"] for s in range(60)]
    slime = [gen(NpcSpec(category="persistent", species="slime"), s).stats["strength"] for s in range(60)]
    assert sum(dragon) / 60 > sum(slime) / 60 + 10


# ---------- age and species ----------

def test_default_age_is_adult_for_the_species():
    assert default_age_range("human", CT) == (18, 49)
    for species in CT.species:
        lo, hi = default_age_range(species, CT)
        for s in range(30):
            n = gen(NpcSpec(species=species), s)
            assert lo <= n.age <= hi
            assert stage_of(to_character(n), CT).id in {"young_adult", "adulthood"}


def test_age_range_and_species_pool_are_configurable():
    ages = {gen(NpcSpec(species="human", age_range=(7, 9)), s).age for s in range(60)}
    assert ages <= {7, 8, 9} and len(ages) > 1
    assert gen(NpcSpec(species="human", age_range=(5, 5)), 1).age == 5
    pool = {gen(NpcSpec(species=["elf", "dwarf"]), s).species_id for s in range(60)}
    assert pool == {"elf", "dwarf"}
    assert len({gen(GENERIC, s).species_id for s in range(120)}) >= 4       # None: any species


# ---------- traits and talents ----------

def test_traits_innate_chosen_and_random():
    slime = gen(NpcSpec(category="persistent", species="slime"), 1)
    assert "regenerative" in slime.traits                                  # innate
    chosen = gen(NpcSpec(category="persistent", species="human", traits=["iron_constitution"]), 1)
    assert "iron_constitution" in chosen.traits
    rolled = gen(NpcSpec(category="persistent", species="human", random_traits=2), 6)
    assert 1 <= len(rolled.traits) <= 2 and set(rolled.traits) <= set(CT.traits)
    assert all(CT.traits[t].birth is not None for t in rolled.traits)
    # effective stats include trait bonuses through the existing rule, with nothing duplicated into the stats
    c = to_character(chosen)
    assert effective_stat(c, CT, "constitution") == chosen.stats["constitution"] + 6


def test_talents_explicit_chance_and_none():
    t = next(iter(CT.talents))
    explicit = gen(NpcSpec(category="persistent", talent=t), 1)
    assert (explicit.talent, explicit.talent_state) == (t, "dormant")
    assert gen(PERSISTENT, 1).talent is None
    lucky = [gen(NpcSpec(category="persistent", species="human", talent_chance=1), s) for s in range(30)]
    assert all(n.talent in CT.talents or n.talent is None for n in lucky) and any(n.talent for n in lucky)
    assert all((n.talent_state == "dormant") == bool(n.talent) for n in lucky)


# ---------- combat and the four specialised stats ----------

def test_explicit_specialised_stats_map_to_the_games_skills():
    n = gen(NpcSpec(category="persistent", species="human",
                    combat={"marksmanship": 30, "swordsmanship": [10, 20], "magic": 5}), 1)
    assert n.skills["archery"] == 30 and 10 <= n.skills["swordsmanship"] <= 20 and n.skills["magic"] == 5
    values = specialised_values(n)
    assert set(values) == FOUR and values["marksmanship"] == 30 and values["martial_arts"] == 0


def test_random_combat_picks_the_requested_number_of_specialised_stats():
    for focus in (0, 1, 2, 3):
        n = gen(NpcSpec(category="persistent", species="human", combat_focus=focus, combat_level=(10, 20)), 8)
        trained = [v for v in specialised_values(n).values() if v > 0]
        assert len(trained) == focus and all(10 <= v <= 20 for v in trained)


def test_random_combat_respects_what_the_species_can_train_but_authored_combat_does_not():
    wolf = gen(NpcSpec(category="persistent", species="wolf", combat_focus=4), 1)
    assert not any(specialised_values(wolf).values())                      # none of the four is trainable by wolves
    authored = gen(NpcSpec(category="persistent", species="wolf", combat={"martial_arts": 12}), 1)
    assert specialised_values(authored)["martial_arts"] == 12


def test_only_the_four_specialised_stats_exist():
    assert set(CFG.specialised_stats) == FOUR
    with pytest.raises(NpcSpecError, match="unknown specialised stat"):
        gen(NpcSpec(combat={"elemental_magic": 10}))


# ---------- combat stats only when needed; Power reuse ----------

def test_power_generates_combat_on_demand_without_changing_the_npc():
    n = gen(NpcSpec(species="human", combat={"swordsmanship": 40}), 3)
    assert not n.has_combat
    assert npc_power(n).total == pytest.approx(40, abs=1)                  # swordsmanship 40, nothing else
    assert not n.has_combat and not n.has_stats                            # still light
    stored = dump(n)
    npc_power(n)
    assert dump(n) == stored


def test_power_matches_the_existing_calculation_exactly():
    for seed in range(25):
        n = gen(NpcSpec(category="persistent", species=["human", "elf", "dwarf", "dragonkin"], combat_focus=3), seed)
        playable = Character(name="x", species_id=n.species_id, skills=dict(n.skills), traits=list(n.traits),
                             stats=dict(n.stats))
        assert npc_power(n).model_dump() == calculate_power(playable, CT, CFG).model_dump()


def test_base_stats_never_add_power_and_power_is_not_stored():
    spec = NpcSpec(category="persistent", species="human", combat={"magic": 20})
    weak, strong = gen(spec.model_copy(update={"stat_range": (1, 1)}), 5), gen(spec.model_copy(update={"stat_range": (90, 90)}), 5)
    assert npc_power(weak).total == npc_power(strong).total
    assert not any("power" in key for key in Npc.model_fields)
    assert "power" not in json.dumps(dump(weak)).lower()


def test_specialised_values_add_up_to_stats_power():
    n = gen(NpcSpec(category="persistent", species="human", combat_focus=3), 4)
    assert sum(specialised_values(n).values()) == pytest.approx(npc_power(n).stats.total)


def test_the_adapter_is_a_real_character_the_existing_rules_accept():
    n = gen(PERSISTENT, 2)
    c = to_character(n)
    assert isinstance(c, Character) and c.name == n.name and c.species_id == n.species_id and c.age == n.age
    assert stage_of(c, CT).id and sim.max_health(c, CT) > 0


# ---------- lifecycle ----------

def test_lifecycle_status():
    n = gen(PERSISTENT, 1)
    assert n.status == LifecycleStatus.ACTIVE and n.alive
    n.retire()
    assert n.status == LifecycleStatus.RETIRED and n.alive
    n.reactivate()
    n.mark_deceased("a fever")
    assert not n.alive and n.cause_of_death == "a fever"
    for action in (n.retire, n.reactivate):
        with pytest.raises(ValueError, match="dead"):
            action()


def test_promotion_keeps_the_same_character_and_completes_it():
    g = gen(NpcSpec(species="human", random_traits=1, combat_focus=2), 17)
    p = FACTORY.promote(g)
    assert (p.id, p.name, p.age, p.species_id) == (g.id, g.name, g.age, g.species_id)
    assert p.category == CharacterCategory.PERSISTENT and p.spec.category == CharacterCategory.PERSISTENT
    assert p.has_stats and p.has_combat and p.traits is not None
    assert not g.has_stats                                                  # the original was not touched
    with pytest.raises(ValueError, match="only a generic"):
        FACTORY.promote(p)


# ---------- bad requests ----------

@pytest.mark.parametrize("spec, message", [
    (NpcSpec(species="catfolk"), "unknown species"),
    (NpcSpec(species=["human", "catfolk"]), "unknown species"),
    (NpcSpec(stat_bonus={"luck": 1}), "unknown stat"),
    (NpcSpec(stats={"luck": 1}), "unknown stat"),
    (NpcSpec(traits=["nope"]), "unknown trait"),
    (NpcSpec(talent="nope"), "unknown talent"),
    (NpcSpec(culture="atlantean"), "unknown name culture"),
])
def test_unknown_references_are_rejected(spec, message):
    with pytest.raises(NpcSpecError, match=message):
        FACTORY.generate(spec, seed=1)


@pytest.mark.parametrize("kw", [{"age_range": (9, 3)}, {"stat_range": (50, 20)}, {"talent_chance": 2}, {"random_traits": -1}])
def test_nonsense_numbers_are_rejected(kw):
    with pytest.raises(ValidationError):
        NpcSpec(**kw)


# ---------- authored (named) characters ----------

NAMED = """
characters:
  - id: archmage_valen
    name: Valen of the Seventh Tower
    species: elf
    age: 212
    description: Keeper of the Seventh Tower.
    stats: {strength: 30, dexterity: 45, constitution: 40, wisdom: 90, charisma: 60}
    specialised_stats: {magic: 85}
    traits: [keen_sense]
  - id: old_hob
    name: Old Hob
    species: dwarf
    age: 140
"""


def named_file(tmp_path, text=NAMED):
    p = tmp_path / "named_characters.yaml"
    p.write_text(text)
    return p


def test_the_shipped_named_character_file_is_valid():
    assert load_named_characters() == {}


def test_named_characters_keep_their_authored_name_and_id(tmp_path):
    defs = load_named_characters(path=named_file(tmp_path))
    valen = named_npc(defs["archmage_valen"])
    assert valen.name == "Valen of the Seventh Tower" and valen.id == "named_archmage_valen"
    assert valen.category == CharacterCategory.NAMED and valen.seed is None and valen.spec is None
    assert valen.stats["wisdom"] == 90 and specialised_values(valen)["magic"] == 85
    assert "keen_sense" in valen.traits
    assert named_npc(defs["archmage_valen"]) == valen                       # nothing random at all


def test_named_characters_only_have_what_was_authored(tmp_path):
    hob = named_npc(load_named_characters(path=named_file(tmp_path))["old_hob"])
    assert not hob.has_stats and hob.talent is None
    assert npc_power(hob).total == 0 and not hob.has_stats                  # nothing is invented for them
    assert dump(ensure_all(hob)) == dump(hob)


def test_named_power_comes_from_the_existing_calculation(tmp_path):
    valen = named_npc(load_named_characters(path=named_file(tmp_path))["archmage_valen"])
    assert npc_power(valen).total == 85 == calculate_power(Character(name="x", skills={"magic": 85}), CT, CFG).total


@pytest.mark.parametrize("text, message", [
    (NAMED.replace("species: elf", "species: catfolk"), "unknown species"),
    (NAMED.replace("[keen_sense]", "[nope]"), "unknown trait"),
    (NAMED.replace("{magic: 85}", "{elemental_magic: 85}"), "unknown specialised stat"),
    (NAMED.replace("old_hob", "archmage_valen"), "duplicate id"),
    (NAMED.replace("Old Hob", "valen of the seventh tower"), "already used"),
    (NAMED.replace("archmage_valen", "Archmage Valen"), "lowercase"),
    (NAMED.replace("stats: {strength: 30, dexterity: 45, constitution: 40, wisdom: 90, charisma: 60}", "stats: {strength: 30}"), "all of"),
    ("people: []", "characters"),
])
def test_bad_named_characters_are_reported(tmp_path, text, message):
    with pytest.raises(ContentError, match=message):
        load_named_characters(path=named_file(tmp_path, text))


def test_a_named_definition_never_goes_through_name_generation(tmp_path):
    d = NamedCharacterDef(id="zethin_warden", name="the Warden", species="human", age=40)
    assert named_npc(d).name == "the Warden"


# ---------- existing gameplay is untouched ----------

def test_playable_character_creation_is_unchanged():
    """Values recorded from engine.new_character before the NPC system existed."""
    c = sim.new_character("Aren", "human", 42, talent_chance=0)
    assert c.stats == {"constitution": 59, "dexterity": 28, "wisdom": 30, "strength": 40, "charisma": 45}
    assert (c.traits, c.location, c.health) == ([], "city", 99)
    w = sim.new_character("Wulf", "wolf", 7, talent_chance=0)
    assert w.stats == {"constitution": 43, "dexterity": 67, "wisdom": 48, "strength": 51, "charisma": 31}
    assert (w.traits, w.skills) == (["fleet_footwork"], {"tracking": 3.0, "beast_instinct": 2.0})


def test_the_playable_character_model_gained_no_npc_fields():
    assert not {"id", "category", "status", "seed", "spec"} & set(Character.model_fields)
