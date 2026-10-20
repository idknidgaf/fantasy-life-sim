"""Power: specialised stats (1:1, base stats excluded), the formula, additive modifiers, dynamic recalculation,
safe zeros, no double counting, and consistency between the backend breakdown and what the frontend renders."""
import copy
import re
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app import rules
from app.character import Character
from app.content import STATS, ContentError, get_content
from app.models import CharacterRecord
from app.power import (
    PowerBreakdown, PowerComponent, PowerConfig, PowerFormula, PowerLine, PowerModifiers, PowerProviders,
    SpecialisedStatDef, calculate_power, fmt, load_power_config,
)

from .test_api import client, new_game  # noqa: F401  (fixture)

CT = get_content()
CFG = load_power_config(CT)                       # the shipped configuration
BASE = {s: 100 for s in STATS}                    # maxed base stats: they must contribute nothing


def char(skills=None, **kw) -> Character:
    return Character(name="T", stats=dict(BASE), skills=dict(skills or {}), **kw)


def fixed(value: float, kind="x", sid="a", label="A"):
    """A provider returning one fixed line, to test the formula independent of game data."""
    return lambda c, ct, cfg: [PowerLine(source_type=kind, source_id=sid, label=label, value=value)]


def mods(*values: float):
    return lambda c, ct, cfg: [PowerLine(source_type="t", source_id=f"m{i}", label=f"M{i}", value=v)
                               for i, v in enumerate(values)]


def check_breakdown(b: PowerBreakdown) -> None:
    """The breakdown must add up exactly as its own formula says."""
    for part in (b.stats, b.equipment, b.skills):
        assert part.total == pytest.approx(sum(l.value for l in part.lines), abs=0.01)
    assert b.subtotal == pytest.approx(b.stats.total + b.equipment.total + b.skills.total, abs=0.01)
    assert b.modifiers.total == pytest.approx(sum(l.value for l in b.modifiers.lines), abs=1e-4)
    assert b.modifiers.multiplier == pytest.approx(max(0, 1 + b.modifiers.total), abs=1e-4)
    assert b.total == pytest.approx(b.subtotal * b.modifiers.multiplier, abs=0.01)
    assert fmt(b.stats.total) in b.formula.substituted and fmt(b.modifiers.total) in b.formula.substituted
    assert b.formula.result == f"= {fmt(b.subtotal)} × {fmt(b.modifiers.multiplier)} = {fmt(b.total)}"


# ---------------------------------------------------------------- the shipped configuration

def test_shipped_specialised_stats_and_one_to_one_weights():
    assert list(CFG.specialised_stats) == ["swordsmanship", "marksmanship", "magic", "martial_arts"]
    assert {i: d.name for i, d in CFG.specialised_stats.items()} == {
        "swordsmanship": "Swordsmanship", "marksmanship": "Marksmanship", "magic": "Magic", "martial_arts": "Martial Arts"}
    assert all(d.weight == 1.0 for d in CFG.specialised_stats.values())          # 1:1 for every one
    assert all(d.purpose for d in CFG.specialised_stats.values())
    assert CFG.skill_power == {}
    assert not set(CFG.specialised_stats) & set(STATS)                           # never a base stat


# ---------------------------------------------------------------- Stats Power = the four specialised stats, 1:1

def test_each_specialised_stat_counts_one_to_one():
    for stat, skill in (("swordsmanship", "swordsmanship"), ("marksmanship", "archery"),
                        ("magic", "elemental_magic"), ("martial_arts", "martial_arts")):
        b = calculate_power(char({skill: 37.5}), CT, CFG)
        line = next(l for l in b.stats.lines if l.source_id == stat)
        assert line.value == 37.5 and line.inputs == {"value": 37.5, "weight": 1.0}
        assert b.stats.total == 37.5 == b.total                                  # nothing else contributes


def test_spec_example_100_swordsmanship_and_100_magic_is_200():
    b = calculate_power(char({"swordsmanship": 100, "elemental_magic": 100}), CT, CFG)
    assert b.stats.total == 200 and b.total == 200
    assert b.formula.substituted == "Final Power = (200 + 0 + 0) × (1 + 0)"


def test_stats_power_is_the_plain_sum_of_the_four():
    b = calculate_power(char({"swordsmanship": 10, "archery": 20, "spirit_magic": 30, "martial_arts": 40}), CT, CFG)
    assert [l.value for l in b.stats.lines] == [10, 20, 30, 40]
    assert b.stats.total == 100 and b.stats.implemented


def test_magic_adds_up_its_schools():
    b = calculate_power(char({"elemental_magic": 30, "spirit_magic": 20, "necromancy": 5, "enchantment": 1}), CT, CFG)
    magic = next(l for l in b.stats.lines if l.source_id == "magic")
    assert magic.value == 56 and len(magic.sources) == 4                          # each school shown as a source
    assert "Elemental Magic 30" in magic.sources


def test_all_four_are_listed_even_at_zero_so_the_breakdown_is_complete():
    b = calculate_power(char(), CT, CFG)
    assert [l.source_id for l in b.stats.lines] == ["swordsmanship", "marksmanship", "magic", "martial_arts"]
    assert all(l.value == 0 and l.sources == [] for l in b.stats.lines)
    assert b.total == 0


def test_other_skills_do_not_add_power():
    b = calculate_power(char({"tactics": 80, "stealth": 60, "exploration": 99, "smithing": 40}), CT, CFG)
    assert b.stats.total == 0 and b.total == 0


# ---------------------------------------------------------------- base stats are excluded

def test_base_stats_never_contribute_to_power():
    empty = calculate_power(char(), CT, CFG)
    assert empty.total == 0                                                       # all five base stats at 100
    for stat in STATS:
        c = char({"swordsmanship": 10})
        c.stats[stat] = 1
        assert calculate_power(c, CT, CFG).total == 10                            # changing a base stat: no effect
        c.stats[stat] = 999
        assert calculate_power(c, CT, CFG).total == 10
    assert not [l for l in empty.stats.lines if l.source_id in STATS]


def test_trait_stat_bonuses_do_not_add_power_but_still_work_for_checks():
    c = char({"swordsmanship": 10}, traits=["iron_constitution"])                 # +6 Constitution
    assert calculate_power(c, CT, CFG).total == 10
    assert rules.effective_stat(c, CT, "constitution") == 106                     # base-stat behaviour is intact


def test_base_stat_functionality_is_untouched():
    c = char()
    c.stats["strength"] = 50
    assert rules.effective_stat(c, CT, "strength") == 50
    assert rules.requirement_met({"stat": "strength", "min": 40}, c, CT) is True
    assert rules.requirement_met({"stat": "strength", "min": 60}, c, CT) is False


def test_breakdown_says_base_stats_are_excluded():
    note = calculate_power(char(), CT, CFG).stats.note
    assert "Base stats" in note and "do not add Power" in note


def test_a_specialised_stat_cannot_be_a_base_stat(tmp_path):
    p = tmp_path / "power.yaml"
    p.write_text("specialised_stats:\n  strength: {name: Strength, skills: [swordsmanship]}\n")
    with pytest.raises(ContentError, match="base stat"):
        load_power_config(CT, p)


def test_the_old_base_stat_weight_format_is_rejected(tmp_path):
    p = tmp_path / "power.yaml"
    p.write_text("stat_weights: {strength: 1.0}\n")
    with pytest.raises(ContentError, match="stat_weights"):
        load_power_config(CT, p)


# ---------------------------------------------------------------- extensible without code

def test_new_specialised_stat_is_just_a_config_entry():
    cfg = CFG.model_copy(deep=True)
    cfg.specialised_stats["soul_craft"] = SpecialisedStatDef(name="Soul Craft", skills=["soul_control"])
    b = calculate_power(char({"soul_control": 12, "swordsmanship": 8}), CT, cfg)
    assert b.stats.total == 20 and [l.source_id for l in b.stats.lines][-1] == "soul_craft"


def test_weight_is_configurable_and_zero_weight_contributes_nothing():
    cfg = CFG.model_copy(deep=True)
    cfg.specialised_stats["swordsmanship"].weight = 2.5
    cfg.specialised_stats["magic"].weight = 0
    b = calculate_power(char({"swordsmanship": 10, "elemental_magic": 50}), CT, cfg)
    assert b.stats.total == 25 and [l.source_id for l in b.stats.lines].count("magic") == 0


# ---------------------------------------------------------------- the existing formula and modifiers still apply

def test_final_formula_with_all_components_and_modifiers():
    providers = PowerProviders(equipment=fixed(20, "equipment"), skills=fixed(30, "skill"),
                               modifiers=mods(0.10, 0.20))
    b = calculate_power(char({"swordsmanship": 70, "archery": 30}), CT, CFG, providers)
    assert (b.stats.total, b.equipment.total, b.skills.total) == (100, 20, 30)
    assert b.subtotal == 150
    assert b.modifiers.total == pytest.approx(0.30)
    assert b.total == pytest.approx(195)                                          # 150 x 1.3
    assert b.formula.substituted == "Final Power = (100 + 20 + 30) × (1 + 0.3)"
    assert b.formula.result == "= 150 × 1.3 = 195"
    check_breakdown(b)


def test_spec_example_with_modifiers():
    b = calculate_power(char({"swordsmanship": 100, "elemental_magic": 100}), CT, CFG,
                        PowerProviders(modifiers=mods(0.25)))
    assert b.stats.total == 200 and b.total == pytest.approx(250)


def test_percentage_modifiers_add_they_do_not_multiply():
    b = calculate_power(char({"swordsmanship": 100}), CT, CFG, PowerProviders(modifiers=mods(0.10, 0.20)))
    assert b.modifiers.multiplier == pytest.approx(1.3) and b.total == pytest.approx(130)   # not 100 x 1.1 x 1.2
    assert b.modifiers.percent_text == "+30%"


def test_negative_modifiers_reduce_power_and_never_go_below_zero():
    c = char({"swordsmanship": 100})
    assert calculate_power(c, CT, CFG, PowerProviders(modifiers=mods(-0.25))).total == pytest.approx(75)
    crushed = calculate_power(c, CT, CFG, PowerProviders(modifiers=mods(-1.5)))
    assert crushed.total == 0 and crushed.modifiers.multiplier == 0 and crushed.notes


def test_trait_power_modifier_is_a_modifier_with_its_source():
    ct = copy.deepcopy(CT)                                                        # never mutate the shared content
    ct.traits["iron_constitution"].modifiers.power = 0.10
    ct.traits["fleet_footwork"].modifiers.power = 0.20
    b = calculate_power(char({"swordsmanship": 100}, traits=["iron_constitution", "fleet_footwork"]), ct, CFG)
    assert {(l.source_type, l.source_id, l.value) for l in b.modifiers.lines} == {
        ("trait", "iron_constitution", 0.10), ("trait", "fleet_footwork", 0.20)}
    assert b.total == pytest.approx(130)
    check_breakdown(b)


def test_skill_power_flat_bonus_still_works_alongside_specialised_stats():
    cfg = CFG.model_copy(update={"skill_power": {"tactics": 5, "stealth": 7}})
    b = calculate_power(char({"swordsmanship": 100, "tactics": 3.0, "stealth": 0}), CT, cfg)
    assert [l.source_id for l in b.skills.lines] == ["tactics"] and b.skills.total == 5      # level 0: not unlocked
    assert b.stats.total == 100 and b.total == 105


# ---------------------------------------------------------------- dynamic recalculation, nothing persisted

def test_power_is_recalculated_from_current_state():
    c = char({"swordsmanship": 10})
    assert calculate_power(c, CT, CFG).total == 10
    rules.gain_skill(c, CT, "swordsmanship", 5)                                   # training raises it
    after_train = calculate_power(c, CT, CFG).total
    assert after_train > 10
    c.skills["archery"] = 7
    assert calculate_power(c, CT, CFG).total == pytest.approx(after_train + 7)
    c.skills["archery"] = 0
    assert calculate_power(c, CT, CFG).total == pytest.approx(after_train)


def test_power_is_not_stored_anywhere():
    assert "power" not in Character.model_fields
    assert not [col for col in CharacterRecord.__table__.columns.keys() if "power" in col]


def test_no_new_character_fields_or_columns_were_needed():
    """Specialised stats are read from existing skills, so saves are untouched and no migration exists."""
    assert "specialised_stats" not in Character.model_fields
    assert "specialised_stats" not in CharacterRecord.__table__.columns.keys()


def test_api_reflects_state_changes_immediately(client):
    new_game(client)
    first = client.get("/api/power").json()
    with Session(client.engine) as s:
        rec = s.exec(select(CharacterRecord)).one()
        rec.skills = {**rec.skills, "swordsmanship": rec.skills.get("swordsmanship", 0) + 20}
        rec.stats = {**rec.stats, "strength": 100}                                # a base stat change: no effect
        s.add(rec)
        s.commit()
    second = client.get("/api/power").json()
    assert second["total"] == pytest.approx(first["total"] + 20)
    assert [l["source_id"] for l in second["stats"]["lines"]] == ["swordsmanship", "marksmanship", "magic", "martial_arts"]


# ---------------------------------------------------------------- missing / unimplemented parts are zero

def test_unimplemented_components_default_to_zero_with_a_reason():
    b = calculate_power(char({"swordsmanship": 10}, items={"healing_salve": 3}), CT, CFG)   # inventory is not equipment
    assert b.equipment.total == 0 and b.equipment.implemented is False and "no equipment system" in b.equipment.note
    assert b.skills.total == 0 and b.skills.implemented is False and b.skills.note
    assert b.modifiers.total == 0 and b.modifiers.multiplier == 1 and b.modifiers.note
    assert b.total == b.stats.total == 10


def test_empty_config_and_empty_character_are_safe():
    empty = calculate_power(Character(name="Blank"), CT, PowerConfig())
    assert empty.total == 0 and empty.stats.lines == [] and empty.formula.result == "= 0 × 1 = 0"
    blank = calculate_power(Character(name="Blank"), CT, CFG)                     # no stats, no skills at all
    assert blank.total == 0 and len(blank.stats.lines) == 4
    check_breakdown(blank)


def test_missing_power_file_means_defaults(tmp_path):
    assert load_power_config(CT, tmp_path / "nope.yaml") == PowerConfig()


@pytest.mark.parametrize("body, fragment", [
    ("specialised_stats: {a: {name: A, skills: [no_such_skill]}}", "unknown skill 'no_such_skill'"),
    ("specialised_stats: {a: {name: A, skills: []}}", "skills"),
    ("specialised_stats: {a: {name: A, skills: [archery], weight: -1}}", "weight"),
    ("specialised_stats: {a: {name: A, skills: [archery], extra: 1}}", "extra"),
    ("specialised_stats: {a: {skills: [archery]}}", "name"),
    ("skill_power: {not_a_skill: 3}", "unknown skill 'not_a_skill'"),
    ("surprise: 1", "surprise"),
])
def test_invalid_power_config_is_rejected(tmp_path, body, fragment):
    p = tmp_path / "power.yaml"
    p.write_text(body)
    with pytest.raises(ContentError, match=fragment):
        load_power_config(CT, p)


# ---------------------------------------------------------------- no duplicate / double counting

def test_a_skill_cannot_feed_two_specialised_stats(tmp_path):
    p = tmp_path / "power.yaml"
    p.write_text("specialised_stats:\n  a: {name: A, skills: [archery]}\n  b: {name: B, skills: [archery]}\n")
    with pytest.raises(ContentError, match="counted twice"):
        load_power_config(CT, p)
    with pytest.raises(ValueError, match="counted twice"):                        # also guarded on the model itself
        PowerConfig(specialised_stats={"a": SpecialisedStatDef(name="A", skills=["archery"]),
                                       "b": SpecialisedStatDef(name="B", skills=["archery"])})


def test_a_specialised_skill_cannot_also_get_a_flat_skill_bonus(tmp_path):
    p = tmp_path / "power.yaml"
    p.write_text("specialised_stats:\n  a: {name: A, skills: [archery]}\nskill_power: {archery: 5}\n")
    with pytest.raises(ContentError, match="counted twice"):
        load_power_config(CT, p)


def test_a_skill_listed_twice_in_one_stat_is_rejected():
    with pytest.raises(ValueError, match="listed twice in 'a'"):
        PowerConfig(specialised_stats={"a": SpecialisedStatDef(name="A", skills=["archery", "archery"])})


def test_a_trait_listed_twice_gives_its_modifier_once():
    ct = copy.deepcopy(CT)
    ct.traits["iron_constitution"].modifiers.power = 0.10
    b = calculate_power(char({"swordsmanship": 100}, traits=["iron_constitution"] * 2), ct, CFG)
    assert [l.value for l in b.modifiers.lines] == [0.10] and b.total == pytest.approx(110)


def test_duplicate_contributions_from_a_provider_are_counted_once_and_reported():
    twice = lambda c, ct, cfg: [PowerLine(source_type="equipment", source_id="sword", label="Sword", value=15),   # noqa: E731
                                PowerLine(source_type="equipment", source_id="sword", label="Sword", value=15)]
    b = calculate_power(char(), CT, CFG, PowerProviders(equipment=twice))
    assert b.equipment.total == 15 and len(b.equipment.lines) == 1
    assert any("duplicate" in n.lower() for n in b.notes)


def test_inventory_items_never_count_as_equipment():
    bag = calculate_power(char(items={"healing_salve": 5, "ancient_relic": 2}), CT, CFG)
    assert bag.equipment.total == 0 and bag.total == 0


def test_extension_through_providers_changes_only_that_component():
    c = char({"swordsmanship": 30})
    base = calculate_power(c, CT, CFG)
    b = calculate_power(c, CT, CFG, PowerProviders(equipment=fixed(40, "equipment", "sword", "Sword")))
    assert b.equipment.total == 40 and b.equipment.implemented
    assert b.stats.total == base.stats.total and b.total == base.total + 40


# ---------------------------------------------------------------- backend <-> frontend consistency

def test_api_response_is_exactly_the_calculation_and_adds_up(client):
    new_game(client)
    body = client.get("/api/power").json()
    check_breakdown(PowerBreakdown.model_validate(body))
    with Session(client.engine) as s:
        from app import engine as sim
        rec = s.exec(select(CharacterRecord)).one()
        expected = calculate_power(sim.repair(rec.to_character()), CT)
    assert body == expected.model_dump(mode="json")


def test_api_requires_a_campaign(client):
    assert client.get("/api/power").status_code == 404


def _ts_interface_fields(source: str, name: str) -> set[str]:
    m = re.search(rf"export interface {name} \{{(.*?)\n\}}", source, re.S)
    assert m, f"interface {name} missing from frontend/src/types.ts"
    return set(re.findall(r"^\s+(\w+)\??:", m.group(1), re.M))


@pytest.mark.parametrize("name, model", [
    ("PowerBreakdown", PowerBreakdown), ("PowerComponent", PowerComponent), ("PowerLine", PowerLine),
    ("PowerModifiers", PowerModifiers), ("PowerFormula", PowerFormula)])
def test_frontend_types_match_the_backend_response_model(name, model):
    """The frontend renders the API response as typed in types.ts: the two must not drift apart."""
    ts = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "types.ts").read_text(encoding="utf-8")
    assert _ts_interface_fields(ts, name) == set(model.model_fields)


def test_power_tab_does_no_power_arithmetic():
    """Every number shown comes from the backend: the component must not compute Power itself."""
    src = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "PowerTab.tsx").read_text(encoding="utf-8")
    body = re.sub(r"/\*.*?\*/|//.*", "", src, flags=re.S)
    assert not re.search(r"\.reduce\(|Math\.|\*\s*power|power\.\w+\s*[-+*/]\s*power", body)


def test_power_tab_does_not_mention_base_stats_as_a_power_source():
    """The UI shows whatever lines the backend sends; it must not hard-code the old base-stat rows."""
    src = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "PowerTab.tsx").read_text(encoding="utf-8")
    assert not re.search(r"strength|dexterity|constitution|wisdom|charisma", src, re.I)
