"""Creating descendants: identity, relationships, persistence, invalid parents, retries, Power, what is not inherited."""
import json

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app import descendants as service
from app import relationships as rel
from app.content import STATS, get_content
from app.db import init_db
from app.descendants import create_descendant, descendant_seed
from app.inheritance import (
    DescendantConflictError, IncompatibleSpeciesError, InvalidChildAgeError, NoParentsError, ParentNotEligibleError,
    get_inheritance_rules,
)
from app.models import Campaign, CharacterRecord, CharacterRelationship, NpcRecord
from app.npc import CharacterCategory, Npc, NpcFactory, NpcSpec, npc_power, specialised_values, to_character
from app.npc_store import keep_temporary_npc, list_npcs, load_npc, save_npc
from app.power import calculate_power, get_power_config
from app.character import Character
from tests.test_api import client, new_game  # noqa: F401  (client is a fixture)

CT = get_content()
CFG = get_power_config()
FACTORY = NpcFactory()
RULES = get_inheritance_rules()


def adult(species="human", seed=1, **kw):
    spec = NpcSpec(category="persistent", species=species, **kw)
    if spec.age_range is None:
        spec = spec.model_copy(update={"age_range": (35, 35) if species != "dragonkin" else (45, 45)})
    return FACTORY.generate(spec, seed=seed)


def new_db(url="sqlite://"):
    kw = {"connect_args": {"check_same_thread": False}}
    engine = create_engine(url, poolclass=StaticPool, **kw) if url == "sqlite://" else create_engine(url, **kw)
    init_db(engine)
    with Session(engine) as s:
        s.add_all([Campaign(name="one", seed=1), Campaign(name="two", seed=2)])
        s.commit()
    return engine


@pytest.fixture
def db():
    engine = new_db()
    with Session(engine) as s:
        s.engine = engine
        yield s


def keep(db, npc, cid=1):
    save_npc(db, cid, npc)
    return npc.id


def dump(npc):
    return npc.model_dump(mode="json")


def rows(db):
    return len(db.exec(select(NpcRecord)).all()), len(db.exec(select(CharacterRelationship)).all())


# ---------- creating a child ----------

def test_a_child_of_two_parents_is_a_new_persistent_character_linked_to_both(db):
    mum, dad = adult("human", 1), adult("human", 2)
    keep(db, mum), keep(db, dad)
    d = create_descendant(db, 1, [mum.id, dad.id], seed=10)
    kid = d.npc
    assert d.created and d.parents == [mum.id, dad.id] and d.skipped == []
    assert kid.category == CharacterCategory.PERSISTENT and kid.status == "active" and kid.age == 0
    assert kid.species_id == "human" and kid.id not in (mum.id, dad.id) and kid.name
    assert set(rel.parents_of(db, 1, kid.id)) == {mum.id, dad.id}
    assert kid.id in rel.children_of(db, 1, mum.id) and kid.id in rel.children_of(db, 1, dad.id)
    assert [n.id for n in rel.eligible_descendants(db, 1, mum.id)] == [kid.id]


def test_a_child_of_one_parent_when_the_other_is_unknown(db):
    mum = keep(db, adult("elf", 1))
    for parents in ([mum], [mum, None], [None, mum]):
        kid = create_descendant(db, 1, parents, seed=descendant_seed(5, len(parents) + id(parents) % 7)).npc
        assert kid.species_id == "elf" and rel.parents_of(db, 1, kid.id) == [mum]


def test_the_child_is_a_separate_character_not_a_copy(db):
    mum, dad = adult("human", 1, stats={s: 70 for s in STATS}), adult("human", 2, stats={s: 70 for s in STATS})
    keep(db, mum), keep(db, dad)
    kid = create_descendant(db, 1, [mum.id, dad.id], seed=3).npc
    assert kid.id not in (mum.id, dad.id) and kid.name not in (mum.name, dad.name) and kid.seed != mum.seed
    assert kid.stats != mum.stats and kid.stats != dad.stats                    # pulled toward them, not equal to them
    assert all(kid.stats[s] < 70 for s in STATS)                                # regressed toward the middle
    assert kid.age == 0 < mum.age


def test_each_child_gets_a_unique_id_and_siblings_differ(db):
    mum, dad = keep(db, adult("human", 1)), keep(db, adult("human", 2))
    kids = [create_descendant(db, 1, [mum, dad], seed=descendant_seed(99, i)).npc for i in range(12)]
    assert len({k.id for k in kids}) == 12 and len({tuple(k.stats.values()) for k in kids}) > 6
    assert set(rel.children_of(db, 1, mum)) == {k.id for k in kids}


def test_the_childs_name_is_generated_for_its_species(db):
    from app.names import get_name_config, get_name_generator
    names = get_name_generator()
    for species in ("human", "elf", "dwarf", "dragonkin", "wolf", "slime"):
        parent = keep(db, adult(species, 4))
        kid = create_descendant(db, 1, [parent], seed=8).npc
        given = get_name_config().cultures[names.culture_for(kid.species_id)].given
        assert kid.name.split()[0].lower().startswith(tuple(p.lower() for p in given.prefixes))


def test_the_child_may_be_any_younger_age(db):
    mum = keep(db, adult("human", 1))
    kid = create_descendant(db, 1, [mum], seed=3, age=12).npc
    assert kid.age == 12 and kid.has_stats                                        # a ready-grown heir, if the caller wants one


# ---------- species rules ----------

def test_mixed_parents_produce_a_listed_species_only(db):
    a, b = keep(db, adult("human", 1)), keep(db, adult("elf", 2))
    kids = {create_descendant(db, 1, [a, b], seed=descendant_seed(1, i)).npc.species_id for i in range(30)}
    assert kids == {"human", "elf"}


@pytest.mark.parametrize("pair", [("elf", "dwarf"), ("human", "wolf"), ("slime", "dragonkin")])
def test_incompatible_parents_make_no_child_and_save_nothing(db, pair):
    a, b = keep(db, adult(pair[0], 1)), keep(db, adult(pair[1], 2))
    before = rows(db)
    with pytest.raises(IncompatibleSpeciesError):
        create_descendant(db, 1, [a, b], seed=3)
    assert rows(db) == before


# ---------- unavailable and invalid parents ----------

def test_a_missing_second_parent_is_skipped_and_reported(db):
    mum = keep(db, adult("human", 1))
    d = create_descendant(db, 1, [mum, "npc_nobody"], seed=3)
    assert d.parents == [mum] and [(s.ref, "no such character" in s.reason) for s in d.skipped] == [("npc_nobody", True)]
    assert rel.parents_of(db, 1, d.npc.id) == [mum]


def test_a_temporary_npc_is_unavailable_until_it_is_promoted(db):
    mum = keep(db, adult("human", 1))
    temp = FACTORY.generate(NpcSpec(species="human", age_range=(30, 30)), seed=5)
    keep_temporary_npc(db, 1, temp)
    d = create_descendant(db, 1, [mum, temp.id], seed=3)
    assert d.parents == [mum] and "promote" in d.skipped[0].reason
    save_npc(db, 1, FACTORY.promote(temp))
    both = create_descendant(db, 1, [mum, temp.id], seed=4)
    assert set(both.parents) == {mum, temp.id} and both.skipped == []


def test_no_available_parent_is_an_error(db):
    with pytest.raises(NoParentsError):
        create_descendant(db, 1, [], seed=1)
    with pytest.raises(NoParentsError, match="no such character"):
        create_descendant(db, 1, ["npc_x", "npc_y"], seed=1)
    other = keep(db, adult("human", 1), cid=2)                                    # exists, but in another campaign
    with pytest.raises(NoParentsError):
        create_descendant(db, 1, [other], seed=1)


def test_invalid_parents_are_errors_not_silent_skips(db):
    a, b, c = (keep(db, adult("human", s)) for s in (1, 2, 3))
    young = keep(db, FACTORY.generate(NpcSpec(category="persistent", species="human", age_range=(12, 12)), seed=9))
    before = rows(db)
    with pytest.raises(ParentNotEligibleError, match="at most 2"):
        create_descendant(db, 1, [a, b, c], seed=1)
    with pytest.raises(ParentNotEligibleError, match="both parents"):
        create_descendant(db, 1, [a, a], seed=1)
    with pytest.raises(ParentNotEligibleError, match="too young"):
        create_descendant(db, 1, [a, young], seed=1)
    with pytest.raises(InvalidChildAgeError, match="younger"):
        create_descendant(db, 1, [a], seed=1, age=35)
    with pytest.raises(InvalidChildAgeError):
        create_descendant(db, 1, [a], seed=1, age=-1)
    assert rows(db) == before


def test_parents_in_the_same_direct_line_are_refused_unless_the_rule_is_off(db):
    mum = keep(db, adult("human", 1))
    kid = create_descendant(db, 1, [mum], seed=2, age=0).npc
    grown = kid.model_copy(deep=True, update={"age": 25})
    save_npc(db, 1, grown)                                                        # the child is now an adult
    with pytest.raises(ParentNotEligibleError, match="direct line"):
        create_descendant(db, 1, [mum, kid.id], seed=3)
    off = RULES.model_copy(update={"parents": RULES.parents.model_copy(update={"forbid_direct_line": False})})
    assert create_descendant(db, 1, [mum, kid.id], seed=3, rules=off).created


# ---------- all-or-nothing, and retries ----------

def test_the_child_and_its_links_are_saved_together_or_not_at_all(db, monkeypatch):
    mum = keep(db, adult("human", 1))
    pending = FACTORY.generate(NpcSpec(category="persistent", species="human"), seed=77)
    save_npc(db, 1, pending)                                                      # the caller's other, uncommitted work
    before = rows(db)

    def boom(*a, **k):
        raise rel.RelationshipError("simulated failure while linking")
    monkeypatch.setattr(service, "add_parent", boom)
    with pytest.raises(rel.RelationshipError):
        create_descendant(db, 1, [mum], seed=5)
    assert rows(db) == before                                                     # no half-made child left behind
    assert load_npc(db, 1, pending.id) is not None                                # and the caller's work survives


def test_repeating_a_call_returns_the_same_child_without_duplicates(db):
    a, b = keep(db, adult("human", 1)), keep(db, adult("human", 2))
    first = create_descendant(db, 1, [a, b], seed=5)
    again = create_descendant(db, 1, [b, a], seed=5)                              # a retry, even in another order
    assert first.created and not again.created and again.npc.id == first.npc.id
    assert dump(again.npc) == dump(first.npc)
    assert rows(db) == (3, 2)


def test_reusing_a_seed_for_different_parents_is_refused(db):
    a, b = keep(db, adult("human", 1)), keep(db, adult("human", 2))
    create_descendant(db, 1, [a], seed=5)
    with pytest.raises(DescendantConflictError, match="another seed"):
        create_descendant(db, 1, [b], seed=5)
    assert rows(db) == (3, 1)


def test_the_same_parents_and_seed_always_give_the_same_child():
    kids = []
    for _ in range(2):
        engine = new_db()
        with Session(engine) as s:
            a, b = keep(s, adult("human", 1, combat={"magic": 50}, traits=["iron_constitution"])), keep(s, adult("elf", 2))
            kids.append(dump(create_descendant(s, 1, [a, b], seed=42).npc))
    assert kids[0] == kids[1]


# ---------- persistence ----------

def test_a_descendant_and_its_family_survive_a_real_reload(tmp_path):
    url = f"sqlite:///{tmp_path / 'family.db'}"
    engine = new_db(url)
    with Session(engine) as s:
        mum = keep(s, adult("human", 1, combat={"swordsmanship": 60}, traits=["iron_constitution"]))
        dad = keep(s, adult("elf", 2, combat={"magic": 40}))
        kid = create_descendant(s, 1, [mum, dad], seed=7, rules=RULES.model_copy(update={"traits": RULES.traits.model_copy(
            update={"overrides": {"iron_constitution": 1.0}})})).npc
        s.commit()
    engine.dispose()
    reopened = create_engine(url)
    init_db(reopened)
    with Session(reopened) as s:
        back = load_npc(s, 1, kid.id)
        assert dump(back) == dump(kid) and back.category == CharacterCategory.PERSISTENT
        assert set(rel.parents_of(s, 1, kid.id)) == {mum, dad}
        assert [n.id for n in rel.eligible_descendants(s, 1, mum)] == [kid.id]
        assert npc_power(back).model_dump() == npc_power(kid).model_dump()
        assert back.stats == kid.stats and back.skills == kid.skills and back.traits == kid.traits


# ---------- Power and the existing systems ----------

def test_power_comes_only_from_the_childs_own_skills_through_the_existing_calculation(db):
    mum = adult("human", 1, combat={"magic": 60, "swordsmanship": 40})
    keep(db, mum)
    kid = create_descendant(db, 1, [mum.id], seed=3).npc
    levels = specialised_values(kid)
    assert 0 < levels["magic"] < 10 and 0 < levels["swordsmanship"] < 10 and levels["marksmanship"] == 0
    power = npc_power(kid)
    assert power.stats.total == pytest.approx(sum(levels.values()))              # no bonus on top of the skills
    twin = Character(name="x", species_id=kid.species_id, skills=dict(kid.skills), traits=list(kid.traits),
                     stats=dict(kid.stats))
    assert power.model_dump() == calculate_power(twin, CT, CFG).model_dump()
    assert npc_power(kid).total < npc_power(mum).total / 4                       # a head start, nowhere near the parent


def test_no_power_is_stored_on_the_child(db):
    mum = keep(db, adult("human", 1, combat={"magic": 60}))
    kid = create_descendant(db, 1, [mum], seed=3).npc
    assert "power" not in json.dumps(db.exec(select(NpcRecord).where(NpcRecord.npc_id == kid.id)).one().data).lower()


def test_the_child_works_with_every_existing_npc_function(db):
    mum = keep(db, adult("human", 1))
    kid = create_descendant(db, 1, [mum], seed=3).npc
    assert isinstance(to_character(kid), Character) and npc_power(kid).total >= 0
    assert [n.id for n in list_npcs(db, 1)].count(kid.id) == 1


# ---------- inheritance is not development ----------

def test_parents_skills_inventory_and_achievements_are_not_handed_down(db):
    mum = adult("human", 1, combat={"swordsmanship": 80})
    mum.skills.update({"tracking": 90, "cooking": 70})                            # developed, non-specialised skills
    mum.traits = ["blessed", "pack_alpha"]                                        # achievement traits
    mum.talent, mum.talent_state = "master_of_none", "awakened"
    keep(db, mum)
    always = RULES.model_copy(update={"talent": RULES.talent.model_copy(update={"inherit_chance": 1.0})})
    kid = create_descendant(db, 1, [mum.id], seed=3, rules=always).npc
    assert "tracking" not in kid.skills and "cooking" not in kid.skills           # developed skills stay with the parent
    assert not {"blessed", "pack_alpha"} & set(kid.traits)                        # achievement traits are not eligible
    assert kid.talent == "master_of_none" and kid.talent_state == "dormant"       # only dormant potential, never awakened
    assert kid.skills["swordsmanship"] < 10                                       # aptitude, not the parent's 80


def test_a_child_has_no_items_titles_or_history_to_inherit():
    assert not {"items", "history", "title", "titles", "inventory"} & set(Npc.model_fields)


def test_creating_a_child_leaves_the_parents_untouched(db):
    a, b = adult("human", 1, combat={"magic": 50}), adult("elf", 2)
    keep(db, a), keep(db, b)
    snapshot = [json.dumps(r.data, sort_keys=True) for r in db.exec(select(NpcRecord).order_by(NpcRecord.id))]
    create_descendant(db, 1, [a.id, b.id], seed=3)
    after = [json.dumps(r.data, sort_keys=True) for r in db.exec(select(NpcRecord).order_by(NpcRecord.id))][:2]
    assert after == snapshot


# ---------- the playable character as a parent ----------

def test_the_playable_character_can_be_a_parent_and_is_left_unchanged(client):  # noqa: F811
    new_game(client, seed=7)
    cid = client.get("/api/campaigns").json()[0]["id"]
    with Session(client.engine) as s:
        rec = s.exec(select(CharacterRecord)).first()
        rec.age = 30
        s.add(rec)
        s.commit()
        s.refresh(rec)
        before = json.dumps(rec.model_dump(mode="json"), sort_keys=True, default=str)
        pc = rel.player_ref(rec)
        npc_parent = keep(s, adult("human", 5), cid)
        d = create_descendant(s, cid, [pc, npc_parent], seed=11)
        assert d.parents == [pc, npc_parent] and set(rel.parents_of(s, cid, d.npc.id)) == {pc, npc_parent}
        assert d.npc.species_id in ("human",) and d.npc.id.startswith("npc_")
        assert [n.id for n in rel.eligible_descendants(s, cid, pc)] == [d.npc.id]
        s.commit()
        s.refresh(rec)
        assert json.dumps(rec.model_dump(mode="json"), sort_keys=True, default=str) == before


def test_an_unknown_or_underage_playable_character_is_not_a_parent(client):  # noqa: F811
    new_game(client, seed=7)
    cid = client.get("/api/campaigns").json()[0]["id"]
    with Session(client.engine) as s:
        rec = s.exec(select(CharacterRecord)).first()
        with pytest.raises(NoParentsError, match="no such playable character"):
            create_descendant(s, cid, ["pc_9999"], seed=1)
        with pytest.raises(ParentNotEligibleError, match="too young"):               # a new character is a baby
            create_descendant(s, cid, [rel.player_ref(rec)], seed=1)
