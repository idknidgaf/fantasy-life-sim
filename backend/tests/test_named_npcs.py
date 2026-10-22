"""Named (authored) characters: definitions, fixed vs generated attributes, stable instantiation, runtime state,
definition updates, lifecycle, story-engine integration, and compatibility with inheritance and legacy.
Synthetic characters only (ids start with `test_`); no game content."""
import re
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app import named
from app import relationships as rel
from app.content import ContentError, get_content
from app.db import get_session, init_db
from app.descendants import create_descendant, descendant_seed
from app.main import app
from app.models import CharacterRecord, NpcRecord, StoryChoiceLog
from app.npc import (
    CharacterCategory, LifecycleStatus, Npc, load_named_characters, named_npc, npc_power, to_character,
)
from app.npc_store import list_npcs, load_npc, save_npc
from app.power import calculate_power
from app.relationships import player_ref
from app.story import service
from app.story.errors import StoryContentError, StoryEffectError
from app.story.library import build_library
from tests.story_helpers import ctx_for, ev, make_life, memory_engine
from tests.test_api import client, new_game  # noqa: F401  (client is a fixture)
from tests import test_legacy as lg

CT = get_content()
TRAIT = next(t for t, d in CT.traits.items() if not d.prerequisites)
BIRTH_TRAITS = [t for t, d in CT.traits.items() if d.birth is not None and t != TRAIT]
TALENT = next(iter(CT.talents))
LIFESPAN = CT.species["human"].lifespan

FILE = f"""
characters:
  - id: test_sage
    version: 1
    name: Test Sage
    title: The Tester
    species: human
    age: 70
    description: A sage, for tests.
    lore: Long lore for tests.
    affiliations: [test_order]
    location: test_tower
    stats: {{strength: 30, dexterity: 40, constitution: 50, wisdom: 90, charisma: 60}}
    specialised_stats: {{magic: 85}}
    traits: [{TRAIT}]
    talent: {TALENT}
    flags: {{met: false, trust: 2}}
    relationships:
      - {{kind: mentor, other: test_pupil}}
    encounter:
      conditions: {{type: age_at_least, value: 5}}
  - id: test_pupil
    name: Test Pupil
    species: human
    age: 20
    stats: {{strength: 90}}
    generate: {{stats: true, stat_range: [30, 60], combat: true, random_traits: 1, talent_chance: 1.0}}
  - id: test_ageless
    name: Test Ageless
    species: human
    age: 300
    lifecycle: {{ageing: immortal}}
  - id: test_elder
    name: Test Elder
    species: human
    age: 70
    lifecycle: {{ageing: longevity, lifespan_multiplier: 3}}
  - id: test_plain
    name: Test Plain
    species: human
    age: 40
"""


def write(tmp_path, text=FILE):
    p = tmp_path / "named_characters.yaml"
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def defs(tmp_path):
    return load_named_characters(path=write(tmp_path))


@pytest.fixture
def db():
    with Session(memory_engine()) as session:
        yield session


@pytest.fixture
def world(db):
    """A campaign (seed 7) with its player character, as a session and the player's record."""
    return db, make_life(db, seed=7, age=40)


def spawn(db, defs, definition_id, campaign_id=1):
    return named.instantiate(db, campaign_id, definition_id, defs=defs)


# ---------------------------------------------------------------- loading and validating definitions

def test_a_full_definition_loads_with_everything_authored(defs):
    d = defs["test_sage"]
    assert (d.version, d.name, d.title, d.species, d.age) == (1, "Test Sage", "The Tester", "human", 70)
    assert (d.description, d.lore, d.affiliations, d.location) == ("A sage, for tests.", "Long lore for tests.",
                                                                    ["test_order"], "test_tower")
    assert d.stats["wisdom"] == 90 and d.specialised_stats == {"magic": 85} and d.talent == TALENT
    assert d.flags == {"met": False, "trust": 2} and d.relationships[0].other == "test_pupil"
    assert d.encounter.conditions == {"type": "age_at_least", "value": 5}
    assert (d.lifecycle.ageing, d.generate.any, d.status) == ("normal", False, LifecycleStatus.ACTIVE)


def test_defaults_are_normal_ageing_and_no_generation(defs):
    plain = defs["test_plain"]
    assert (plain.version, plain.lifecycle.ageing, plain.generate.any, plain.flags, plain.relationships) == (
        1, "normal", False, {}, [])
    assert plain.title is None and plain.encounter.conditions is None


def test_the_shipped_definitions_are_valid_and_do_not_spawn_a_catalogue():
    assert load_named_characters() == {}


BAD = [
    ("duplicate id", "  - id: test_plain\n    name: Other\n    species: human\n    age: 1\n", "duplicate id"),
    ("duplicate name", "  - id: other\n    name: test plain\n    species: human\n    age: 1\n", "already used"),
    ("species", "  - id: x\n    name: X\n    species: nonesuch\n    age: 1\n", "unknown species"),
    ("trait", "  - id: x\n    name: X\n    species: human\n    age: 1\n    traits: [nonesuch]\n", "unknown trait"),
    ("talent", "  - id: x\n    name: X\n    species: human\n    age: 1\n    talent: nonesuch\n", "unknown talent"),
    ("specialised", "  - id: x\n    name: X\n    species: human\n    age: 1\n    specialised_stats: {nonesuch: 5}\n",
     "unknown specialised stat"),
    ("over cap", "  - id: x\n    name: X\n    species: human\n    age: 1\n    stats: {strength: 999, dexterity: 1, constitution: 1, wisdom: 1, charisma: 1}\n",
     "stat cap"),
    ("partial stats", "  - id: x\n    name: X\n    species: human\n    age: 1\n    stats: {strength: 50}\n", "all of"),
    ("unknown stat", "  - id: x\n    name: X\n    species: human\n    age: 1\n    stats: {luck: 5}\n    generate: {stats: true}\n",
     "unknown base stat"),
    ("combat clash", "  - id: x\n    name: X\n    species: human\n    age: 1\n    specialised_stats: {magic: 5}\n    generate: {combat: true}\n",
     "authored values are fixed"),
    ("longevity", "  - id: x\n    name: X\n    species: human\n    age: 1\n    lifecycle: {ageing: longevity}\n", "above 1"),
    ("multiplier", "  - id: x\n    name: X\n    species: human\n    age: 1\n    lifecycle: {lifespan_multiplier: 2}\n",
     "only applies"),
    ("immortal dead", "  - id: x\n    name: X\n    species: human\n    age: 1\n    status: deceased\n    lifecycle: {ageing: immortal}\n",
     "immortal"),
    ("rel unknown", "  - id: x\n    name: X\n    species: human\n    age: 1\n    relationships: [{kind: friend, other: ghost}]\n",
     "unknown character 'ghost'"),
    ("rel self", "  - id: x\n    name: X\n    species: human\n    age: 1\n    relationships: [{kind: friend, other: x}]\n",
     "themselves"),
    ("rel kind", "  - id: x\n    name: X\n    species: human\n    age: 1\n    relationships: [{kind: nemesis, other: test_plain}]\n",
     "unknown relationship kind"),
    ("rel duplicate", "  - id: x\n    name: X\n    species: human\n    age: 1\n    relationships: [{kind: friend, other: test_plain}, {kind: friend, other: test_plain}]\n",
     "duplicate relationship"),
    ("condition", "  - id: x\n    name: X\n    species: human\n    age: 1\n    encounter: {conditions: {type: nonesuch}}\n",
     "unknown condition type"),
    ("flag key", "  - id: x\n    name: X\n    species: human\n    age: 1\n    flags: {Bad Key: 1}\n", "flag"),
    ("affiliation", "  - id: x\n    name: X\n    species: human\n    age: 1\n    affiliations: [Not Slug]\n", "affiliation"),
    ("unknown field", "  - id: x\n    name: X\n    species: human\n    age: 1\n    power: 9000\n", "power"),
    ("version", "  - id: x\n    name: X\n    species: human\n    age: 1\n    version: 0\n", "version"),
]


@pytest.mark.parametrize("label, entry, message", BAD, ids=[b[0] for b in BAD])
def test_invalid_definitions_are_reported(tmp_path, label, entry, message):
    with pytest.raises(ContentError, match=message):
        load_named_characters(path=write(tmp_path, FILE + entry))


def test_parentage_must_be_possible(tmp_path):
    too_many = FILE + """
  - id: kid
    name: Kid
    species: human
    age: 1
    relationships:
      - {kind: parent, other: test_sage, self_is: target}
      - {kind: parent, other: test_pupil, self_is: target}
      - {kind: parent, other: test_plain, self_is: target}
"""
    with pytest.raises(ContentError, match="3 parents"):
        load_named_characters(path=write(tmp_path, too_many))
    circular = FILE + """
  - id: loop_a
    name: Loop A
    species: human
    age: 50
    relationships: [{kind: parent, other: loop_b}]
  - id: loop_b
    name: Loop B
    species: human
    age: 30
    relationships: [{kind: parent, other: loop_a}]
"""
    with pytest.raises(ContentError, match="own ancestor"):
        load_named_characters(path=write(tmp_path, circular))


def test_every_problem_is_reported_at_once(tmp_path):
    bad = FILE + "  - id: a\n    name: A\n    species: nonesuch\n    age: 1\n  - id: b\n    name: B\n    species: human\n    age: 1\n    traits: [nonesuch]\n"
    with pytest.raises(ContentError) as e:
        load_named_characters(path=write(tmp_path, bad))
    assert "unknown species" in str(e.value) and "unknown trait" in str(e.value)


# ---------------------------------------------------------------- starting attributes: fixed, optional generated

def test_authored_attributes_are_used_exactly_as_written(defs, world):
    db, _ = world
    npc = spawn(db, defs, "test_sage").npc
    assert npc.name == "Test Sage" and npc.species_id == "human" and npc.age == 70
    assert npc.stats == {"strength": 30, "dexterity": 40, "constitution": 50, "wisdom": 90, "charisma": 60}
    assert to_character(npc, CT).skills["magic"] == 85
    assert TRAIT in npc.traits and (npc.talent, npc.talent_state) == (TALENT, "dormant")
    assert npc.category == CharacterCategory.NAMED and npc.seed is None and npc.spec is None


def test_authored_attributes_do_not_depend_on_the_campaign_seed(defs):
    a = named.build_npc(defs["test_sage"], campaign_seed=1)
    b = named.build_npc(defs["test_sage"], campaign_seed=999)
    assert a == b


def test_nothing_is_generated_unless_allowed(defs):
    plain = named.build_npc(defs["test_plain"], campaign_seed=5)
    assert plain.stats is None and plain.talent is None and plain.talent_state == "none"
    assert plain.traits == [] and plain.skills is None          # unset stays unset: not rolled behind the author's back
    sage = named.build_npc(defs["test_sage"], campaign_seed=5)
    assert sage.traits == [TRAIT]                                # no extra traits without generate.random_traits


def test_generate_fills_only_what_was_not_authored(defs):
    pupil = named.build_npc(defs["test_pupil"], campaign_seed=7)
    assert pupil.stats["strength"] == 90                          # authored: fixed, not rolled
    assert all(30 <= pupil.stats[s] <= 60 for s in pupil.stats if s != "strength")   # generated, in range
    assert pupil.skills                                           # specialised stats drawn: none were authored
    assert len(pupil.traits) == 1                                 # random_traits: 1
    assert pupil.talent is not None and pupil.talent_state == "dormant"      # talent_chance: 1.0


def test_authored_values_beat_generation_even_when_generation_is_allowed(tmp_path):
    p = write(tmp_path, f"""
characters:
  - id: test_mixed
    name: Test Mixed
    species: human
    age: 30
    stats: {{strength: 77, dexterity: 66, constitution: 55, wisdom: 44, charisma: 33}}
    traits: [{TRAIT}]
    talent: {TALENT}
    generate: {{stats: true, random_traits: 2, talent_chance: 1.0}}
""")
    d = load_named_characters(path=p)["test_mixed"]
    for seed in range(1, 15):
        npc = named.build_npc(d, campaign_seed=seed)
        assert npc.stats == {"strength": 77, "dexterity": 66, "constitution": 55, "wisdom": 44, "charisma": 33}
        assert TRAIT in npc.traits and len(npc.traits) == 3 and npc.talent == TALENT


def test_generated_attributes_are_stable_for_a_campaign_and_differ_between_campaigns(defs):
    again = [named.build_npc(defs["test_pupil"], campaign_seed=7) for _ in range(2)]
    assert again[0] == again[1]
    others = [named.build_npc(defs["test_pupil"], campaign_seed=s).stats for s in range(100, 110)]
    assert len({tuple(sorted(o.items())) for o in others}) > 1


def test_generated_attributes_are_saved_once_and_never_rerolled(defs, world):
    db, _ = world
    first = spawn(db, defs, "test_pupil").npc
    stats, traits, skills = dict(first.stats), list(first.traits), dict(first.skills)
    db.commit()
    again = spawn(db, defs, "test_pupil").npc
    assert (again.stats, again.traits, again.skills) == (stats, traits, skills)
    assert first.seed is None and load_npc(db, 1, first.id).seed is None      # nothing left that could reroll it later


def test_power_is_calculated_never_stored(defs, world):
    db, _ = world
    npc = spawn(db, defs, "test_sage").npc
    assert "power" not in Npc.model_fields and "power" not in npc.model_dump()
    assert "power" not in load_npc(db, 1, npc.id).model_dump()
    assert npc_power(npc).total == calculate_power(to_character(npc, CT), CT).total == 85
    npc.skills = {**npc.skills, "swordsmanship": 10}              # a change is reflected at once
    assert npc_power(npc).total == 95


# ---------------------------------------------------------------- stable ids and one instance per definition

def test_the_instance_has_a_stable_id_linked_to_its_definition(defs, world):
    db, _ = world
    inst = spawn(db, defs, "test_sage")
    assert inst.created and inst.npc.id == "named_test_sage" == named.runtime_id("test_sage")
    assert (inst.npc.definition, inst.npc.definition_version) == ("test_sage", 1)
    assert (inst.npc.location, inst.npc.flags) == ("test_tower", {"met": False, "trust": 2})
    saved = load_npc(db, 1, "named_test_sage")
    assert saved == inst.npc and named.get_instance(db, 1, "test_sage") == saved


def test_referencing_a_definition_from_many_encounters_never_duplicates_it(defs, world):
    db, _ = world
    first = spawn(db, defs, "test_sage")
    for _ in range(4):                                            # four more encounters, a retry, a reload
        again = spawn(db, defs, "test_sage")
        assert again.created is False and again.npc == first.npc
    db.commit()
    assert len(db.exec(select(NpcRecord).where(NpcRecord.npc_id == "named_test_sage")).all()) == 1


def test_a_dead_or_retired_character_is_not_created_again(defs, world):
    db, _ = world
    spawn(db, defs, "test_plain")
    named.set_status(db, 1, "test_plain", "deceased", "a fall", defs=defs)
    again = spawn(db, defs, "test_plain")
    assert again.created is False and again.npc.status == LifecycleStatus.DECEASED
    assert len(db.exec(select(NpcRecord)).all()) == 1


def test_nothing_is_instantiated_unless_asked(defs, world):
    db, _ = world
    assert list_npcs(db, 1, category=CharacterCategory.NAMED) == []
    assert named.get_instance(db, 1, "test_sage") is None
    with pytest.raises(named.UnknownDefinitionError):
        named.instantiate(db, 1, "no_such_character", defs=defs)


def test_the_same_definition_in_two_campaigns_is_two_independent_characters(defs, db):
    a, b = make_life(db, seed=1).campaign_id, make_life(db, seed=2).campaign_id
    npc_a, npc_b = spawn(db, defs, "test_pupil", a).npc, spawn(db, defs, "test_pupil", b).npc
    assert npc_a.id == npc_b.id
    named.set_flag(db, a, "test_pupil", "mine", True)
    assert "mine" not in named.get_instance(db, b, "test_pupil").flags
    assert len(db.exec(select(NpcRecord).where(NpcRecord.npc_id == npc_a.id)).all()) == 2


# ---------------------------------------------------------------- runtime state survives save / load

def test_runtime_state_survives_a_restart(defs, tmp_path):
    url = f"sqlite:///{tmp_path / 'save.db'}"
    eng = create_engine(url)
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        rec = make_life(s, seed=7)
        spawn(s, defs, "test_sage", rec.campaign_id)
        spawn(s, defs, "test_pupil", rec.campaign_id)
        named.set_flag(s, 1, "test_sage", "met", True)
        named.increment_flag(s, 1, "test_sage", "trust", 3)
        named.advance_all(s, 1, 4, defs=defs)
        named.set_status(s, 1, "test_pupil", "retired", defs=defs)
        named.link(s, 1, "test_sage", "friend", player_ref(rec))
        s.commit()
    eng.dispose()

    eng = create_engine(url)                                      # a new process, same file
    init_db(eng)
    with Session(eng) as s:
        sage = named.get_instance(s, 1, "test_sage")
        assert (sage.age, sage.flags, sage.location) == (74, {"met": True, "trust": 5}, "test_tower")
        assert named.get_instance(s, 1, "test_pupil").status == LifecycleStatus.RETIRED
        assert rel.related_to(s, 1, "named_test_sage", "friend") == ["pc_1"]
        assert rel.related_to(s, 1, "named_test_sage", "mentor") == ["named_test_pupil"]
        assert spawn(s, defs, "test_sage").created is False and named.get_instance(s, 1, "test_sage").age == 74
    eng.dispose()


# ---------------------------------------------------------------- definition updates never reset a living character

def test_a_new_definition_version_does_not_reset_runtime_state(tmp_path, world):
    db, _ = world
    v1 = load_named_characters(path=write(tmp_path))
    spawn(db, v1, "test_sage")
    spawn(db, v1, "test_pupil")
    named.advance_all(db, 1, 5, defs=v1)                          # age 75
    named.set_flag(db, 1, "test_sage", "met", True)
    named.set_flag(db, 1, "test_sage", "trust", 9)
    named.set_status(db, 1, "test_sage", "retired", defs=v1)
    sage = named.get_instance(db, 1, "test_sage")
    sage.location = "wandering"
    sage.stats = {**sage.stats, "strength": 41}                   # play changed them
    save_npc(db, 1, sage)
    before = named.get_instance(db, 1, "test_sage")
    link_before = rel.related_to(db, 1, "named_test_sage", "mentor")

    v2 = load_named_characters(path=write(tmp_path, FILE.replace("version: 1\n    name: Test Sage", "version: 2\n    name: Test Sage Renamed")
                                          .replace("age: 70", "age: 10", 1)
                                          .replace("flags: {met: false, trust: 2}", "flags: {met: false, trust: 2, fresh: 1}")
                                          .replace("stats: {strength: 30,", "stats: {strength: 99,")))
    report = spawn(db, v2, "test_sage")
    after = report.npc
    assert report.created is False and report.sync.changed == ["name", "flags"]
    assert (report.sync.version_from, report.sync.version_to) == (1, 2) and after.definition_version == 2
    assert after.name == "Test Sage Renamed"                      # the display name is brought up to date
    assert after.flags == {"met": True, "trust": 9, "fresh": 1}   # a new flag is added; existing values are kept
    for kept in ("age", "status", "location", "species_id", "stats", "skills", "traits", "talent", "talent_state", "cause_of_death"):
        assert getattr(after, kept) == getattr(before, kept), kept
    assert after.age == 75 and after.stats["strength"] == 41 and after.status == LifecycleStatus.RETIRED
    assert rel.related_to(db, 1, "named_test_sage", "mentor") == link_before
    again = spawn(db, v2, "test_sage")                            # syncing is itself idempotent
    assert again.sync.changed == [] and again.npc == after


def test_a_definition_that_gains_a_relationship_adds_it_without_touching_the_others(tmp_path, world):
    db, _ = world
    v1 = load_named_characters(path=write(tmp_path))
    spawn(db, v1, "test_sage")
    spawn(db, v1, "test_plain")
    mentor_before = rel.related_to(db, 1, "named_test_sage", "mentor")
    v2 = load_named_characters(path=write(tmp_path, FILE.replace("version: 1\n    name: Test Sage", "version: 2\n    name: Test Sage")
                                          .replace("      - {kind: mentor, other: test_pupil}",
                                                   "      - {kind: mentor, other: test_pupil}\n      - {kind: friend, other: test_plain}")))
    assert "relationships" in spawn(db, v2, "test_sage").sync.changed
    assert rel.related_to(db, 1, "named_test_sage", "friend") == ["named_test_plain"]
    assert rel.related_to(db, 1, "named_test_sage", "mentor") == mentor_before


def test_a_removed_definition_leaves_the_character_alone(defs, world):
    db, _ = world
    npc = spawn(db, defs, "test_plain").npc
    report = named.sync_npc(db, 1, npc, None)
    assert report.orphan and named.get_instance(db, 1, "test_plain") == npc
    assert named.advance_all(db, 1, 3, defs={}) == []                       # an orphan is not aged by a rule it lost
    assert named.get_instance(db, 1, "test_plain").age == 40


# ---------------------------------------------------------------- lifecycle rules

def test_normal_ageing_ends_in_death_by_old_age_past_the_species_lifespan(defs, world):
    db, _ = world
    spawn(db, defs, "test_plain")                                           # age 40
    assert named.advance_all(db, 1, 10, defs=defs)[0].died is False
    assert named.get_instance(db, 1, "test_plain").age == 50
    report = named.advance_all(db, 1, LIFESPAN, defs=defs)[0]
    npc = named.get_instance(db, 1, "test_plain")
    assert report.died and npc.status == LifecycleStatus.DECEASED and npc.cause_of_death == "old age"
    assert named.advance_all(db, 1, 5, defs=defs) == []                     # the dead do not age


def test_longevity_multiplies_the_lifespan(defs, world):
    db, _ = world
    spawn(db, defs, "test_elder")                                           # age 70, lifespan x3
    assert named.max_age(defs["test_elder"]) == LIFESPAN * 3 and named.max_age(defs["test_plain"]) == LIFESPAN
    named.advance_all(db, 1, LIFESPAN, defs=defs)                           # 150: a normal person would be long dead
    assert named.get_instance(db, 1, "test_elder").alive
    named.advance_all(db, 1, LIFESPAN * 2, defs=defs)                       # 310 > 240
    assert not named.get_instance(db, 1, "test_elder").alive


def test_an_immortal_neither_ages_nor_dies(defs, world):
    db, _ = world
    spawn(db, defs, "test_ageless")
    assert named.max_age(defs["test_ageless"]) is None
    assert named.advance_all(db, 1, 10_000, defs=defs) == []
    npc = named.get_instance(db, 1, "test_ageless")
    assert npc.age == 300 and npc.alive
    with pytest.raises(named.ImmortalError):
        named.set_status(db, 1, "test_ageless", "deceased", defs=defs)
    assert named.get_instance(db, 1, "test_ageless").alive
    named.set_status(db, 1, "test_ageless", "retired", defs=defs)           # but they can leave the story
    assert named.get_instance(db, 1, "test_ageless").status == LifecycleStatus.RETIRED


def test_not_every_named_character_is_immortal_or_frozen(defs):
    assert {i: d.lifecycle.ageing for i, d in defs.items()} == {
        "test_sage": "normal", "test_pupil": "normal", "test_ageless": "immortal", "test_elder": "longevity", "test_plain": "normal"}


def test_status_changes_follow_the_lifecycle_rules(defs, world):
    db, _ = world
    spawn(db, defs, "test_plain")
    assert named.set_status(db, 1, "test_plain", "retired", defs=defs).status == LifecycleStatus.RETIRED
    assert named.set_status(db, 1, "test_plain", "active", defs=defs).status == LifecycleStatus.ACTIVE
    named.set_status(db, 1, "test_plain", "deceased", "plague", defs=defs)
    named.set_status(db, 1, "test_plain", "deceased", "something else", defs=defs)      # repeating changes nothing
    assert named.get_instance(db, 1, "test_plain").cause_of_death == "plague"
    with pytest.raises(named.NamedError):
        named.set_status(db, 1, "test_plain", "active", defs=defs)                      # the dead stay dead
    with pytest.raises(named.NotInstantiatedError):
        named.set_status(db, 1, "test_sage", "retired", defs=defs)


def test_only_introduced_characters_age(defs, world):
    db, _ = world
    spawn(db, defs, "test_plain")
    named.advance_all(db, 1, 5, defs=defs)
    assert named.get_instance(db, 1, "test_plain").age == 45 and named.get_instance(db, 1, "test_sage") is None


def test_age_up_ages_introduced_named_characters_and_nobody_else(client, defs, monkeypatch):
    monkeypatch.setattr("app.named.definitions", lambda: defs)
    new_game(client)
    with Session(client.engine) as s:
        spawn(s, defs, "test_plain")
        generic = create_persistent(s)
        s.commit()
    lg.set_latest(client, pending_event=None, queued_events=[])
    assert client.post("/api/turn/advance").status_code == 200
    with Session(client.engine) as s:
        assert named.get_instance(s, 1, "test_plain").age == 41
        assert load_npc(s, 1, generic.id).age == generic.age               # ordinary NPCs do not age (existing behaviour)


def create_persistent(session):
    from app.npc import NpcFactory, NpcSpec
    npc = NpcFactory().generate(NpcSpec(category="persistent", species="human", age_range=(30, 30)), seed=5)
    save_npc(session, 1, npc)
    return npc


# ---------------------------------------------------------------- relationships

def test_authored_relationships_are_made_when_both_characters_exist_in_any_order(defs, world):
    db, _ = world
    spawn(db, defs, "test_sage")
    assert rel.related_to(db, 1, "named_test_sage", "mentor") == []         # the pupil is not here yet: no dangling link
    spawn(db, defs, "test_pupil")
    assert rel.related_to(db, 1, "named_test_sage", "mentor") == ["named_test_pupil"]
    spawn(db, defs, "test_pupil")
    spawn(db, defs, "test_sage")
    assert len(rel.relationships_of(db, 1, "named_test_sage", "mentor")) == 1      # and never twice


def test_the_other_order_gives_the_same_links(defs, world):
    db, _ = world
    spawn(db, defs, "test_pupil")
    spawn(db, defs, "test_sage")
    assert rel.related_to(db, 1, "named_test_sage", "mentor") == ["named_test_pupil"]


# ---------------------------------------------------------------- story engine: validation of references

def lib(defs, *events):
    return build_library([("test", e) for e in events], CT, named=defs)


def spawn_event(event_id="meet", **kw):
    return ev(event_id, choices=[{"id": "go", "text": "Go", "effects": [{"type": "spawn_npc", "npc": "test_sage"}]}], **kw)


def test_events_may_reference_named_characters_by_definition_id(defs):
    library = lib(
        defs,
        ev("a", npc="test_sage", conditions={"all": [
            {"type": "npc_exists", "npc": "test_sage"}, {"type": "npc_status", "npc": "test_sage", "status": "active"},
            {"type": "npc_related", "npc": "test_sage", "kind": "mentor", "other": "test_pupil"},
            {"type": "npc_related", "npc": "test_sage", "kind": "friend", "other": "player"},
            {"type": "npc_flag_equals", "npc": "test_sage", "flag": "met", "value": True},
            {"type": "npc_flag_at_least", "npc": "test_sage", "flag": "trust", "value": 3}]}),
        ev("b", choices=[{"id": "x", "text": "X", "effects": [
            {"type": "spawn_npc", "npc": "test_sage"}, {"type": "set_npc_flag", "npc": "test_sage", "flag": "met", "value": True},
            {"type": "increment_npc_flag", "npc": "test_sage", "flag": "trust"},
            {"type": "set_npc_status", "npc": "test_plain", "status": "retired"},
            {"type": "link_npc", "npc": "test_sage", "kind": "friend", "other": "player"}]}]))
    assert len(library) == 2 and library.get("a").npc == "test_sage"


@pytest.mark.parametrize("where, event, message", [
    ("event npc", ev("a", npc="ghost"), "unknown named character 'ghost'"),
    ("condition", ev("a", conditions={"type": "npc_exists", "npc": "ghost"}), "unknown named character 'ghost'"),
    ("other", ev("a", conditions={"type": "npc_related", "npc": "test_sage", "kind": "friend", "other": "ghost"}),
     "unknown named character 'ghost'"),
    ("effect", ev("a", choices=[{"id": "x", "text": "X", "effects": [{"type": "spawn_npc", "npc": "ghost"}]}]),
     "unknown named character 'ghost'"),
    ("choice condition", ev("a", choices=[{"id": "x", "text": "X", "conditions": {"type": "npc_flag_at_least", "npc": "ghost", "flag": "f", "value": 1}}]),
     "unknown named character 'ghost'"),
])
def test_unknown_named_references_are_rejected_at_load(defs, where, event, message):
    with pytest.raises(StoryContentError, match=message):
        lib(defs, event)


@pytest.mark.parametrize("bad", [
    {"type": "npc_status", "npc": "test_sage", "status": "asleep"},
    {"type": "npc_related", "npc": "test_sage", "kind": "nemesis", "other": "player"},
    {"type": "npc_exists"},
    {"type": "npc_exists", "npc": "test_sage", "extra": 1},
    {"type": "npc_flag_at_least", "npc": "test_sage", "flag": "f", "value": "many"},
])
def test_named_condition_parameters_are_validated(defs, bad):
    with pytest.raises(StoryContentError):
        lib(defs, ev("a", conditions=bad))


def test_encounter_conditions_of_a_definition_are_checked_against_the_story(tmp_path):
    p = write(tmp_path, FILE.replace("conditions: {type: age_at_least, value: 5}",
                                     "conditions: {type: event_completed, event: no_such_event}"))
    d = load_named_characters(path=p)
    with pytest.raises(StoryContentError, match="unknown event 'no_such_event'"):
        build_library([("t", ev("real"))], CT, named=d)
    assert len(build_library([("t", ev("no_such_event"))], CT, named=d)) == 1


# ---------------------------------------------------------------- story engine: conditions and effects at runtime

def choose(db, rec, library, event_id, choice="go"):
    inst = service.start_event(db, rec, event_id, library=library)
    return service.resolve_choice(db, rec, inst["instance_id"], choice, library=library)


@pytest.fixture
def story_world(world, defs, monkeypatch):
    monkeypatch.setattr("app.named.definitions", lambda: defs)
    return world


def test_spawn_effect_introduces_a_character_once_however_many_encounters(story_world, defs):
    db, rec = story_world
    library = lib(defs, spawn_event("meet"), spawn_event("meet_again"))
    assert choose(db, rec, library, "meet")["effects"][0] == {
        "type": "spawn_npc", "npc": "test_sage", "spawned": True, "reason": None}
    again = choose(db, rec, library, "meet_again")["effects"][0]
    assert again["spawned"] is False and again["reason"] == "already exists"
    assert len(db.exec(select(NpcRecord).where(NpcRecord.npc_id == "named_test_sage")).all()) == 1


def test_a_definitions_encounter_conditions_gate_the_spawn(story_world, tmp_path, monkeypatch):
    db, rec = story_world
    gated = load_named_characters(path=write(tmp_path, FILE.replace("{type: age_at_least, value: 5}",
                                                                    "{type: age_at_least, value: 99}")))
    monkeypatch.setattr("app.named.definitions", lambda: gated)
    library = lib(gated, spawn_event())
    result = choose(db, rec, library, "meet")["effects"][0]
    assert result["spawned"] is False and result["reason"] == "encounter conditions not met"
    assert named.get_instance(db, 1, "test_sage") is None
    rec.age = 100
    db.add(rec)
    db.commit()
    library2 = lib(gated, spawn_event("meet2"))
    assert choose(db, rec, library2, "meet2")["effects"][0]["spawned"] is True


def test_no_event_spawns_a_character_it_was_not_told_to(story_world, defs):
    db, rec = story_world
    library = lib(defs, ev("quiet", npc="test_sage"))                # referencing is not spawning
    choose(db, rec, library, "quiet", "ok")
    assert named.get_instance(db, 1, "test_sage") is None


def test_conditions_read_existence_status_flags_and_relationships(story_world, defs):
    db, rec = story_world
    ctx = ctx_for(db, rec, lib(defs, ev("x")))

    def holds(cond):
        from app.story.conditions import evaluate, parse_condition
        return evaluate(parse_condition(cond), ctx)

    exists = {"type": "npc_exists", "npc": "test_sage"}
    assert not holds(exists) and not holds({"type": "npc_status", "npc": "test_sage", "status": "active"})
    assert not holds({"type": "npc_flag_at_least", "npc": "test_sage", "flag": "trust", "value": 0})     # not introduced
    spawn(db, defs, "test_sage")
    spawn(db, defs, "test_pupil")
    assert holds(exists) and holds({"type": "npc_status", "npc": "test_sage", "status": "active"})
    assert not holds({"type": "npc_status", "npc": "test_sage", "status": "deceased"})
    assert holds({"type": "npc_flag_equals", "npc": "test_sage", "flag": "met", "value": False})
    assert not holds({"type": "npc_flag_equals", "npc": "test_sage", "flag": "met", "value": 0})         # false is not 0
    assert holds({"type": "npc_flag_at_least", "npc": "test_sage", "flag": "trust", "value": 2})
    assert not holds({"type": "npc_flag_at_least", "npc": "test_sage", "flag": "trust", "value": 3})
    assert holds({"type": "npc_flag_at_least", "npc": "test_sage", "flag": "unset", "value": 0})         # unset counts as 0
    assert holds({"type": "npc_related", "npc": "test_sage", "kind": "mentor", "other": "test_pupil"})
    assert holds({"type": "npc_related", "npc": "test_sage", "kind": "mentor", "other": "test_pupil", "role": "source"})
    assert not holds({"type": "npc_related", "npc": "test_sage", "kind": "mentor", "other": "test_pupil", "role": "target"})
    assert holds({"type": "npc_related", "npc": "test_pupil", "kind": "mentor", "other": "test_sage", "role": "target"})
    assert not holds({"type": "npc_related", "npc": "test_sage", "kind": "friend", "other": "player"})
    named.link(db, 1, "test_sage", "friend", player_ref(rec))
    assert holds({"type": "npc_related", "npc": "test_sage", "kind": "friend", "other": "player"})
    named.set_status(db, 1, "test_sage", "deceased", defs=defs)
    assert holds({"type": "npc_status", "npc": "test_sage", "status": "deceased"}) and holds(exists)


def test_effects_change_flags_status_and_links(story_world, defs):
    db, rec = story_world
    library = lib(defs, ev("act", choices=[{"id": "go", "text": "Go", "effects": [
        {"type": "spawn_npc", "npc": "test_sage"}, {"type": "spawn_npc", "npc": "test_plain"},
        {"type": "set_npc_flag", "npc": "test_sage", "flag": "met", "value": True},
        {"type": "increment_npc_flag", "npc": "test_sage", "flag": "trust", "amount": 5},
        {"type": "increment_npc_flag", "npc": "test_sage", "flag": "fresh"},
        {"type": "set_npc_status", "npc": "test_plain", "status": "deceased", "cause": "a duel"},
        {"type": "link_npc", "npc": "test_sage", "kind": "mentor", "other": "player"},
        {"type": "link_npc", "npc": "test_sage", "kind": "mentor", "other": "player"}]}]))      # repeating is harmless
    choose(db, rec, library, "act")
    sage, plain = named.get_instance(db, 1, "test_sage"), named.get_instance(db, 1, "test_plain")
    assert sage.flags == {"met": True, "trust": 7, "fresh": 1}
    assert plain.status == LifecycleStatus.DECEASED and plain.cause_of_death == "a duel"
    assert sorted(rel.related_to(db, 1, "named_test_sage", "mentor")) == ["pc_1"]          # once, however often it ran


@pytest.mark.parametrize("failing", [
    {"type": "set_npc_status", "npc": "test_ageless", "status": "deceased"},             # immortal
    {"type": "set_npc_flag", "npc": "test_plain", "flag": "f", "value": 1},               # never introduced
    {"type": "increment_npc_flag", "npc": "test_sage", "flag": "title_flag"},             # (set to text below)
])
def test_a_failing_npc_effect_rolls_the_whole_choice_back(story_world, defs, failing):
    db, rec = story_world
    effects = [{"type": "spawn_npc", "npc": "test_sage"}, {"type": "spawn_npc", "npc": "test_ageless"},
               {"type": "set_npc_flag", "npc": "test_sage", "flag": "title_flag", "value": "text"},
               {"type": "set_npc_flag", "npc": "test_sage", "flag": "early", "value": 1}, failing]
    library = lib(defs, ev("act", choices=[{"id": "go", "text": "Go", "effects": effects}]))
    inst = service.start_event(db, rec, "act", library=library)
    with pytest.raises(StoryEffectError):
        service.resolve_choice(db, rec, inst["instance_id"], "go", library=library)
    assert named.get_instance(db, 1, "test_sage") is None and named.get_instance(db, 1, "test_ageless") is None
    assert db.exec(select(StoryChoiceLog)).all() == []                       # nothing about the choice was recorded
    from app.models import StoryEventInstance
    assert db.exec(select(StoryEventInstance)).one().status in ("pending", "presented")      # and it can be tried again


def test_choices_can_be_gated_on_a_named_characters_state(story_world, defs):
    db, rec = story_world
    spawn(db, defs, "test_sage")
    library = lib(defs, ev("talk", npc="test_sage", choices=[
        {"id": "ask", "text": "Ask", "locked_hint": "They do not trust you yet",
         "conditions": {"type": "npc_flag_at_least", "npc": "test_sage", "flag": "trust", "value": 5}},
        {"id": "leave", "text": "Leave"}]))
    view = service.start_event(db, rec, "talk", library=library)
    assert {c["id"]: c["available"] for c in view["choices"]} == {"ask": False, "leave": True}
    named.increment_flag(db, 1, "test_sage", "trust", 3)
    view = service.get_pending(db, rec, library=library)[0]
    assert {c["id"]: c["available"] for c in view["choices"]} == {"ask": True, "leave": True}


# ---------------------------------------------------------------- story engine: the existing encounter window and queue

def test_a_named_encounter_uses_the_normal_queue_and_shows_its_character(client, defs, monkeypatch):
    monkeypatch.setattr("app.named.definitions", lambda: defs)
    lib_ = lib(defs, ev("other_event"), spawn_event("meet", npc="test_sage"),
               ev("after", choices=[{"id": "ok", "text": "OK"}], npc="test_sage",
                  conditions={"type": "npc_exists", "npc": "test_sage"}))
    monkeypatch.setattr("app.story.service.get_story_library", lambda: lib_)
    new_game(client)
    lg.set_latest(client, pending_event=None, queued_events=[], age=10)      # no legacy birth event; old enough for the encounter conditions
    a = client.post("/api/story/events/other_event/start").json()["instance_id"]
    b = client.post("/api/story/events/meet/start").json()["instance_id"]
    queue = client.get("/api/story/events/pending").json()
    assert [(e["event_id"], e["status"]) for e in queue] == [("other_event", "presented"), ("meet", "pending")]
    assert queue[1]["npc"] == {"id": "test_sage", "name": "Test Sage", "title": "The Tester",
                               "description": "A sage, for tests.", "status": None}      # not introduced yet
    assert queue[0]["npc"] is None
    assert client.post(f"/api/story/instances/{b}/choices/go").status_code == 409        # the queue order still rules
    client.post(f"/api/story/instances/{a}/choices/ok")
    res = client.post(f"/api/story/instances/{b}/choices/go")
    assert res.status_code == 200 and res.json()["effects"][0]["spawned"] is True
    client.post("/api/story/events/after/start")
    shown = client.get("/api/story/events/pending").json()[0]
    assert shown["event_id"] == "after" and shown["npc"]["status"] == "active"             # now they exist


def test_named_npc_state_and_story_choices_persist_across_save_load(tmp_path, defs, monkeypatch):
    monkeypatch.setattr("app.named.definitions", lambda: defs)
    lib_ = lib(defs, spawn_event("meet"))
    monkeypatch.setattr("app.story.service.get_story_library", lambda: lib_)
    from fastapi.testclient import TestClient
    url = f"sqlite:///{tmp_path / 'save.db'}"

    def connect():
        eng = create_engine(url, connect_args={"check_same_thread": False})
        init_db(eng)

        def override():
            with Session(eng) as session:
                yield session
        app.dependency_overrides[get_session] = override
        tc = TestClient(app)
        tc.engine = eng
        return tc

    try:
        c = connect()
        new_game(c)
        lg.set_latest(c, pending_event=None, queued_events=[], age=10)
        iid = c.post("/api/story/events/meet/start").json()["instance_id"]
        c.post(f"/api/story/instances/{iid}/choices/go")
        with Session(c.engine) as s:
            named.set_flag(s, 1, "test_sage", "met", True)
            s.commit()
        c.engine.dispose()
        c = connect()
        with Session(c.engine) as s:
            assert named.get_instance(s, 1, "test_sage").flags["met"] is True
            assert len(s.exec(select(StoryChoiceLog)).all()) == 1
        again = c.post("/api/story/events/meet/start")                       # one-time: cannot meet them "again"
        assert again.status_code == 409
        c.engine.dispose()
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------- inheritance and legacy

def test_a_named_character_can_be_a_parent_of_descendants(defs, world):
    db, rec = world
    spawn(db, defs, "test_sage")
    rec.age = 60
    db.add(rec)
    db.commit()
    child = create_descendant(db, 1, ["named_test_sage", player_ref(rec)], seed=descendant_seed(3, 0), age=2).npc
    db.commit()
    assert set(rel.parents_of(db, 1, child.id)) == {"named_test_sage", player_ref(rec)}
    assert child.id in rel.children_of(db, 1, "named_test_sage")
    assert child.category == CharacterCategory.PERSISTENT and child.id.startswith("npc_")
    parent = named.get_instance(db, 1, "test_sage")                          # the parent is unchanged by having a child
    assert parent.flags == {"met": False, "trust": 2} and parent.age == 70


def test_a_named_character_is_never_offered_as_an_heir_and_stays_intact(client, defs, monkeypatch):
    monkeypatch.setattr("app.named.definitions", lambda: defs)
    new_game(client)
    [kid] = lg.make_family(client, ages=(6,))
    with Session(client.engine) as s:
        spawn(s, defs, "test_sage")
        named.set_flag(s, 1, "test_sage", "met", True)
        rec = lg.latest(s)
        rel.add_parent(s, 1, "named_test_sage", player_ref(rec))      # a named character who is the player's child
        s.commit()
    lg.kill(client)
    state = lg.legacy_state(client)
    assert [h["npc_id"] for h in state["heirs"]] == [kid]                    # only the ordinary descendant
    refused = lg.cont(client, "named_test_sage")
    assert refused.status_code == 409 and "not an eligible" in refused.json()["detail"]
    assert lg.cont(client, kid).status_code == 200
    with Session(client.engine) as s:
        sage = named.get_instance(s, 1, "test_sage")
        assert sage is not None and sage.flags["met"] is True and sage.status == LifecycleStatus.ACTIVE
        assert rel.parents_of(s, 1, "named_test_sage") == ["pc_1"]           # the family link to the dead player survives


def test_named_characters_survive_the_legacy_transition_without_duplication(client, defs, monkeypatch):
    monkeypatch.setattr("app.named.definitions", lambda: defs)
    new_game(client)
    [kid] = lg.make_family(client, ages=(6,))
    with Session(client.engine) as s:
        spawn(s, defs, "test_pupil")
        named.link(s, 1, "test_pupil", "friend", player_ref(lg.latest(s)))
        s.commit()
    lg.kill(client)
    assert lg.cont(client, kid).status_code == 200
    with Session(client.engine) as s:
        assert spawn(s, defs, "test_pupil").created is False                  # still the one instance
        assert len(s.exec(select(NpcRecord).where(NpcRecord.npc_id == "named_test_pupil")).all()) == 1
        assert rel.related_to(s, 1, "named_test_pupil", "friend") == ["pc_1"]   # the old life's link is history
        named.link(s, 1, "test_pupil", "friend", player_ref(lg.latest(s)))       # and the new life can form its own
        s.commit()
        assert set(rel.related_to(s, 1, "named_test_pupil", "friend")) == {"pc_1", "pc_2"}


# ---------------------------------------------------------------- frontend contract and schema export

def _ts_fields(source: str, name: str) -> set[str]:
    m = re.search(rf"export interface {name} \{{(.*?)\n\}}", source, re.S)
    assert m, f"interface {name} missing from frontend/src/types.ts"
    return set(re.findall(r"^\s+(\w+)\??:", m.group(1), re.M))


def test_frontend_types_match_the_story_npc_api():
    from app.story.api import StoryEventRead, StoryNpcRead
    ts = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "types.ts").read_text(encoding="utf-8")
    assert _ts_fields(ts, "StoryNpc") == set(StoryNpcRead.model_fields)
    assert "npc" in _ts_fields(ts, "StoryEvent") and "npc" in StoryEventRead.model_fields


def test_the_exported_story_schema_lists_the_named_character_types():
    from app.story.export_schema import build
    schema = build()
    assert {"npc_exists", "npc_status", "npc_related", "npc_flag_equals", "npc_flag_at_least"} <= set(schema["condition_types"])
    assert {"spawn_npc", "set_npc_flag", "increment_npc_flag", "set_npc_status", "link_npc"} <= set(schema["effect_types"])
    assert "npc" in schema["event_file"]["$defs"]["EventDefinition"]["properties"]
