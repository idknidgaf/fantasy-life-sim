"""Inheritance rules (pure): species compatibility, bounded stats, starting aptitude, traits, talents, reproducibility."""
import statistics

import pytest

from app.character import Character
from app.content import STATS, ContentError, get_content
from app.inheritance import (
    Crossing, IncompatibleSpeciesError, NoParentsError, ParentNotEligibleError, ParentSource, TIERS,
    adult_age, check_parents, get_inheritance_rules, load_inheritance_rules, offspring_species, plan_inheritance,
)
from app.power import get_power_config

CT = get_content()
CFG = get_power_config()
RULES = get_inheritance_rules()


def parent(species="human", age=None, stats=None, skills=None, traits=(), talent=None, name="P", ref=None):
    age = adult_age(species, RULES, CT) + 5 if age is None else age          # a comfortable adult of that species
    return ParentSource(ref or f"npc_{name}", Character(
        name=name, species_id=species, age=age, stats={s: 50 for s in STATS} if stats is None else stats,
        skills=skills or {}, traits=list(traits), hidden_talent=talent, talent_state="dormant" if talent else "none"))


def tweak(**sections):
    """The shipped rules with some sections changed, e.g. tweak(stats={"variation": 0})."""
    return RULES.model_copy(update={k: (v if isinstance(v, list) else getattr(RULES, k).model_copy(update=v))
                                    for k, v in sections.items()})


def plan(parents, seed=1, rules=RULES, **kw):
    return plan_inheritance(parents, seed, rules=rules, **kw)


# ---------- the shipped rules ----------

def test_the_shipped_rules_are_valid():
    assert load_inheritance_rules() == RULES
    assert {frozenset(c.parents) for c in RULES.crossings} == {frozenset({"human", "elf"}), frozenset({"human", "dwarf"})}


def write(tmp_path, text):
    p = tmp_path / "inheritance.yaml"
    p.write_text(text)
    return p


@pytest.mark.parametrize("text, message", [
    ("crossings: [{parents: [human, catfolk], offspring: {human: 1}}]", "unknown species"),
    ("crossings: [{parents: [human, elf], offspring: {catfolk: 1}}]", "unknown species"),
    ("crossings: [{parents: [human, human], offspring: {human: 1}}]", "two different species"),
    ("crossings: [{parents: [human, elf], offspring: {human: 1}}, {parents: [elf, human], offspring: {elf: 1}}]", "listed twice"),
    ("crossings: [{parents: [human, elf], offspring: {human: 0}}]", "above 0"),
    ("parents: {min_stage: toddlerhood}", "life stage"),
    ("parents: {cannot_parent: [catfolk]}", "unknown species"),
    ("stats: {heritability: 1.5}", "heritability"),
    ("stats: {start_bounds: [0, 50]}", "start at 1"),
    ("stats: {roll_range: [60, 20]}", "min, max"),
    ("traits: {chance: {mythic: 0.5}}", "rarity tier"),
    ("traits: {chance: {common: 2}}", "between 0 and 1"),
    ("traits: {extra: [nope]}", "unknown trait"),
    ("traits: {colour: red}", "colour"),
])
def test_invalid_rules_are_reported(tmp_path, text, message):
    with pytest.raises(ContentError, match=message):
        load_inheritance_rules(path=write(tmp_path, text))


# ---------- species ----------

def test_same_species_and_single_parents_pass_their_species_on():
    for sp in CT.species:
        assert offspring_species([sp, sp], RULES) == {sp: 1.0} and offspring_species([sp], RULES) == {sp: 1.0}


def test_listed_crossings_work_in_either_order_and_offer_only_the_listed_species():
    assert offspring_species(["human", "elf"], RULES) == offspring_species(["elf", "human"], RULES) == {"human": 1, "elf": 1}
    seen = {plan([parent("human", name="a"), parent("elf", name="b")], seed=s).species_id for s in range(80)}
    assert seen == {"human", "elf"}                                          # both outcomes happen, nothing else


@pytest.mark.parametrize("a, b", [("elf", "dwarf"), ("human", "wolf"), ("wolf", "slime"), ("human", "dragonkin"),
                                  ("elf", "dragonkin"), ("dwarf", "slime")])
def test_species_that_are_not_listed_cannot_have_children_together(a, b):
    with pytest.raises(IncompatibleSpeciesError, match="no crossing is listed"):
        plan([parent(a, name="a"), parent(b, name="b")])


def test_species_can_be_barred_from_parenthood():
    rules = tweak(parents={"cannot_parent": ["slime"]})
    with pytest.raises(ParentNotEligibleError, match="cannot be a parent"):
        plan([parent("slime")], rules=rules)
    plan([parent("wolf")], rules=rules)


def test_crossings_are_configurable():
    rules = tweak(crossings=[Crossing(parents=("elf", "dwarf"), offspring={"dwarf": 1})])
    assert plan([parent("elf", name="a"), parent("dwarf", name="b")], rules=rules).species_id == "dwarf"
    with pytest.raises(IncompatibleSpeciesError):
        plan([parent("human", name="a"), parent("elf", name="b")], rules=rules)


# ---------- parent checks ----------

def test_bad_parent_lists_are_refused():
    with pytest.raises(NoParentsError):
        check_parents([], RULES, CT)
    with pytest.raises(ParentNotEligibleError, match="at most 2"):
        check_parents([parent(name=n) for n in "abc"], RULES, CT)
    with pytest.raises(ParentNotEligibleError, match="both parents"):
        check_parents([parent(name="a", ref="x"), parent(name="b", ref="x")], RULES, CT)


def test_parents_must_be_adults_of_their_own_species():
    assert adult_age("human", RULES, CT) == 18 and adult_age("elf", RULES, CT) == 27
    with pytest.raises(ParentNotEligibleError, match="too young"):
        plan([parent("human", age=17)])
    plan([parent("human", age=18)])
    with pytest.raises(ParentNotEligibleError, match="too young"):
        plan([parent("elf", age=26)])                                       # an elf of 26 is not an adult yet
    plan([parent("elf", age=27)])
    assert plan([parent("wolf", age=adult_age("wolf", RULES, CT))])


# ---------- base stats ----------

def test_stats_always_stay_within_valid_limits():
    extremes = [{s: v for s in STATS} for v in (1, 100, 120)]
    for species in CT.species:
        cap = CT.species[species].stat_cap
        for stats in extremes:
            for seed in range(60):
                p = plan([parent(species, stats=stats)], seed=seed)
                assert set(p.stats) == set(STATS) and all(1 <= v <= CT.species[p.species_id].stat_cap for v in p.stats.values())
        assert cap >= 100


def test_a_newborns_aptitude_never_starts_outside_the_start_bounds():
    for seed in range(200):
        p = plan([parent(stats={s: 100 for s in STATS})], seed=seed)           # human child: no species modifiers
        assert all(10 <= v <= 75 for v in p.stats.values()), p.stats
        q = plan([parent(stats={s: 1 for s in STATS})], seed=seed)
        assert all(10 <= v <= 75 for v in q.stats.values())


def test_the_child_is_pulled_toward_the_parents_but_never_a_copy():
    strong = [plan([parent(stats={s: 80 for s in STATS})], seed=s).stats["strength"] for s in range(300)]
    weak = [plan([parent(stats={s: 20 for s in STATS})], seed=s).stats["strength"] for s in range(300)]
    mean_strong, mean_weak = statistics.mean(strong), statistics.mean(weak)
    assert mean_strong - mean_weak == pytest.approx(0.4 * 60, abs=3)         # h * the gap between the parents
    assert 40 < mean_strong < 80 and 20 < mean_weak < 60                     # regressed toward the middle, not copied
    assert len(set(strong)) > 10                                             # and varied


def test_heritability_and_variation_are_configurable():
    p = [parent(stats={s: 70 for s in STATS})]
    exact = plan(p, rules=tweak(stats={"heritability": 1.0, "variation": 0}))
    assert set(exact.stats.values()) == {70}                                 # entirely the parents' aptitude
    a = plan([parent(stats={s: 90 for s in STATS})], seed=4, rules=tweak(stats={"heritability": 0.0}))
    b = plan([parent(stats={s: 10 for s in STATS})], seed=4, rules=tweak(stats={"heritability": 0.0}))
    assert a.stats == b.stats                                                # no heritability: parents do not matter
    assert plan(p, seed=4, rules=tweak(stats={"variation": 0, "heritability": 0.5})).stats != \
           plan(p, seed=4, rules=tweak(stats={"variation": 20, "heritability": 0.5})).stats


def test_two_parents_are_averaged_and_species_bonuses_are_not_inherited():
    rules = tweak(stats={"heritability": 1.0, "variation": 0})
    human = parent("human", stats={s: 60 for s in STATS}, name="h")
    other = parent("human", stats={s: 40 for s in STATS}, name="o")
    assert set(plan([human, other], rules=rules).stats.values()) == {50}
    dwarf = parent("dwarf", stats={**{s: 50 for s in STATS}, "strength": 58}, name="d")          # +8 is the dwarf bonus
    assert plan([dwarf, human], rules=rules, seed=2).report.aptitude["strength"] == pytest.approx((50 + 60) / 2)
    kid = plan([dwarf, human], rules=rules, seed=2)
    assert kid.stats["strength"] == min(75, 55) + CT.species[kid.species_id].stat_modifiers.get("strength", 0)


def test_the_childs_own_species_modifiers_and_cap_apply():
    rules = tweak(stats={"heritability": 1.0, "variation": 0})
    for seed in range(40):
        elf_stats = {s: 60 + CT.species["elf"].stat_modifiers.get(s, 0) for s in STATS}      # aptitude exactly 60 once the elf bonus is removed
        kid = plan([parent("human", stats={s: 60 for s in STATS}, name="a"), parent("elf", stats=elf_stats, name="b")],
                   seed=seed, rules=rules)
        expect = {s: max(1, min(CT.species[kid.species_id].stat_cap, 60 + CT.species[kid.species_id].stat_modifiers.get(s, 0)))
                  for s in STATS}
        assert kid.stats == expect


def test_a_parent_without_stats_leaves_the_child_to_roll_normally():
    unknown = parent(stats={})
    p = plan([unknown], seed=3)
    assert all(v is None for v in p.report.aptitude.values()) and all(10 <= v <= 75 for v in p.stats.values())


# ---------- specialised stats ----------

def test_specialised_stats_give_a_small_head_start_never_the_parents_level():
    p = plan([parent(skills={"swordsmanship": 60, "magic": 40, "archery": 5})], rules=tweak(specialised={"variation": 0}))
    assert p.combat == {"swordsmanship": 6.0, "magic": 4.0}                  # 10% of the parent; archery 5 is below the minimum
    assert "marksmanship" not in p.combat and "martial_arts" not in p.combat
    assert all(level < 60 for level in p.combat.values())


def test_the_head_start_is_capped_averaged_and_can_be_switched_off():
    big = parent(skills={"swordsmanship": 100}, name="a")
    assert plan([big], rules=tweak(specialised={"variation": 0})).combat == {"swordsmanship": 10.0}      # capped
    two = [big, parent(skills={}, name="b")]
    assert plan(two, rules=tweak(specialised={"variation": 0})).combat == {"swordsmanship": 5.0}         # averaged with an untrained parent
    assert plan([big], rules=tweak(specialised={"fraction": 0.0})).combat == {}


def test_species_that_cannot_train_a_skill_do_not_inherit_it():
    wolf = parent("wolf", age=6, skills={"swordsmanship": 80, "martial_arts": 80})
    assert plan([wolf]).combat == {}                                         # wolves cannot train these


def test_only_the_four_specialised_stats_can_be_inherited():
    p = plan([parent(skills={"tracking": 90, "cooking": 90, "swordsmanship": 50})])
    assert set(p.combat) <= set(CFG.specialised_stats) and "tracking" not in p.combat


# ---------- traits ----------

def rate(parents, trait, rules=RULES, n=2000):
    return statistics.mean(trait in plan(parents, seed=s, rules=rules).traits for s in range(n))


def test_inheritance_follows_the_trait_chance_for_its_tier():
    one = [parent(traits=["iron_constitution"])]                                 # a common trait
    assert rate(one, "iron_constitution") == pytest.approx(0.30, abs=0.04)
    both = [parent(traits=["iron_constitution"], name="a"), parent(traits=["iron_constitution"], name="b")]
    assert rate(both, "iron_constitution") == pytest.approx(1 - 0.7 ** 2, abs=0.04)      # each parent rolls separately
    assert rate([parent()], "iron_constitution") == 0                            # a trait nobody has is never invented


def test_chances_and_overrides_are_configurable():
    one = [parent(traits=["iron_constitution"])]
    assert rate(one, "iron_constitution", tweak(traits={"overrides": {"iron_constitution": 1.0}}), n=200) == 1
    assert rate(one, "iron_constitution", tweak(traits={"overrides": {"iron_constitution": 0.0}}), n=200) == 0
    assert rate(one, "iron_constitution", tweak(traits={"chance": {"common": 0.7}}), n=2000) == pytest.approx(0.7, abs=0.04)


def test_only_eligible_traits_are_inherited():
    holder = [parent(traits=["blessed", "pack_alpha", "second_wind", "iron_constitution"])]
    always = tweak(traits={"overrides": {t: 1.0 for t in ("blessed", "pack_alpha", "second_wind", "iron_constitution")}})
    got = plan(holder, rules=always).traits
    assert got == ["iron_constitution"]                                      # achievements have no birth rule: not eligible
    opened = tweak(traits={"extra": ["blessed"], "overrides": {"blessed": 1.0, "iron_constitution": 0.0}})
    assert plan(holder, rules=opened).traits == ["blessed"]                  # listed, so eligible
    barred = tweak(traits={"exclude": ["iron_constitution"], "overrides": {"iron_constitution": 1.0}})
    assert plan(holder, rules=barred).traits == []


def test_the_number_of_inherited_traits_is_capped():
    holder = [parent(traits=["iron_constitution", "fleet_footwork", "keen_sense"])]
    every = {"overrides": {"iron_constitution": 1.0, "fleet_footwork": 1.0, "keen_sense": 1.0}}
    assert len(plan(holder, rules=tweak(traits={**every, "max_inherited": 2})).traits) == 2
    assert len(plan(holder, rules=tweak(traits={**every, "max_inherited": 3})).traits) == 3
    assert plan(holder, rules=tweak(traits={**every, "max_inherited": 0})).traits == []


def test_a_species_own_traits_are_not_counted_as_inherited():
    elf_with_innate = [parent("elf", age=40, traits=["keen_sense"])]
    rules = tweak(traits={"overrides": {"keen_sense": 1.0}})
    assert "keen_sense" not in plan(elf_with_innate, rules=rules).traits       # the elf child has it as a species trait anyway


def test_trait_sources_are_reported():
    p = plan([parent(traits=["fleet_footwork"], name="a", ref="npc_a"), parent(name="b", ref="npc_b")],
             rules=tweak(traits={"overrides": {"fleet_footwork": 1.0}}))
    assert p.report.trait_sources == {"fleet_footwork": ["npc_a"]}


# ---------- talents ----------

def test_a_parents_talent_is_passed_on_with_the_configured_chance():
    holder = [parent(talent="master_of_none")]
    got = [plan(holder, seed=s).talent for s in range(3000)]
    assert statistics.mean(t == "master_of_none" for t in got) == pytest.approx(0.10, abs=0.03)
    assert set(got) <= {None, "master_of_none"}                              # never some other talent


def test_a_talent_is_never_automatic_and_can_be_switched_off_or_forced():
    holder = [parent(talent="master_of_none")]
    assert plan(holder, rules=tweak(talent={"inherit_chance": 0.0})).talent is None
    forced = plan(holder, rules=tweak(talent={"inherit_chance": 1.0}))
    assert forced.talent == "master_of_none" and forced.report.talent_source == "npc_P" and forced.talent_chance == 0
    assert plan([parent()], rules=tweak(talent={"inherit_chance": 1.0})).talent is None     # no talent to pass on


def test_a_child_holds_at_most_one_talent_whatever_the_parents_have():
    two = [parent(talent="master_of_none", name="a"), parent(talent="unbroken_will", name="b")]
    rules = tweak(talent={"inherit_chance": 1.0})
    seen = {plan(two, seed=s, rules=rules).talent for s in range(60)}
    assert seen == {"master_of_none", "unbroken_will"}                       # either, never both and never neither


def test_the_child_must_be_eligible_for_an_inherited_talent():
    crossing = Crossing(parents=("human", "dragonkin"), offspring={"dragonkin": 1})
    rules = tweak(crossings=[crossing], talent={"inherit_chance": 1.0})
    humans = [parent("human", talent="divine_favor", name="a"), parent("dragonkin", name="b")]    # divine_favor: humanoids only
    kid = plan(humans, rules=rules)
    assert kid.species_id == "dragonkin" and kid.talent is None
    assert kid.talent_chance == rules.talent.base_chance                     # falls back to a normal newborn roll
    ok = plan([parent("human", talent="master_of_none", name="a"), parent("dragonkin", name="b")], rules=rules)
    assert ok.talent == "master_of_none"


def test_without_an_inherited_talent_the_child_rolls_like_a_newborn():
    assert plan([parent()]).talent_chance == RULES.talent.base_chance == 0.01


# ---------- reproducibility ----------

def test_the_same_parents_and_seed_give_the_same_plan_and_other_seeds_differ():
    ps = [parent(skills={"magic": 50}, traits=["iron_constitution"], talent="master_of_none", name="a"),
          parent("elf", skills={"swordsmanship": 40}, name="b")]
    assert plan(ps, seed=7) == plan(ps, seed=7)
    assert plan(ps, seed=7) != plan(ps, seed=8)


def test_each_part_has_its_own_dice_so_tuning_one_rule_leaves_the_others_alone():
    ps = [parent(skills={"magic": 50}, traits=["iron_constitution"], name="a")]
    base = plan(ps, seed=9)
    other = plan(ps, seed=9, rules=tweak(traits={"overrides": {"iron_constitution": 1.0}}, talent={"inherit_chance": 1.0}))
    assert (base.species_id, base.stats, base.combat) == (other.species_id, other.stats, other.combat)
