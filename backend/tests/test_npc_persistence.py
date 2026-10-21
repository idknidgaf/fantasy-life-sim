"""Persisting NPCs and relationships: what is kept, what is not, reloads, retries, deletion and old saves."""
import json

import pytest
from sqlalchemy import event, inspect, text
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app import npc_store
from app import relationships as rel
from app.db import init_db
from app.models import Campaign, CharacterRecord, CharacterRelationship, NpcRecord
from app.npc import (
    CharacterCategory, LifecycleStatus, Npc, NpcFactory, NpcSpec, ensure_all, npc_power, specialised_values,
)
from app.npc_store import (
    DemotionError, TemporaryNpcError, delete_campaign_npcs, delete_npc, discard_temporary, keep_temporary_npc,
    list_npcs, list_temporary, load_npc, load_npcs, save_npc,
)
from app.relationships import RelationshipKind as K, add_parent, add_relationship
from tests.test_api import client, new_game  # noqa: F401  (client is a fixture)
from tests.test_migration import make_old_save

FACTORY = NpcFactory()
PERSISTENT = NpcSpec(category="persistent", species="elf", random_traits=2, talent_chance=1,
                     combat={"magic": 40, "marksmanship": [10, 20]})


def dump(npc):
    return npc.model_dump(mode="json")


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


# ---------- what a persistent NPC keeps ----------

def test_a_persistent_npc_survives_a_real_reload_with_everything_it_had(tmp_path):
    url = f"sqlite:///{tmp_path / 'life.db'}"
    engine = new_db(url)
    npc = FACTORY.generate(PERSISTENT, seed=21)
    with Session(engine) as s:
        save_npc(s, 1, npc)
        s.commit()
    engine.dispose()                                                    # the game is closed...
    reopened = create_engine(url)                                       # ...and started again
    init_db(reopened)
    with Session(reopened) as s:
        back = load_npc(s, 1, npc.id)
    assert dump(back) == dump(npc)
    assert (back.id, back.name, back.species_id, back.age, back.category, back.status) == \
           (npc.id, npc.name, "elf", npc.age, CharacterCategory.PERSISTENT, LifecycleStatus.ACTIVE)
    assert back.stats == npc.stats and back.traits == npc.traits and back.talent == npc.talent
    assert specialised_values(back) == specialised_values(npc) and specialised_values(back)["magic"] == 40
    assert npc_power(back).model_dump() == npc_power(npc).model_dump()


def test_a_light_persistent_npc_is_settled_when_saved(db):
    light = FACTORY.generate(NpcSpec(category="persistent", species="human", detail="light", random_traits=1), seed=3)
    assert not light.has_stats
    save_npc(db, 1, light)
    back = load_npc(db, 1, light.id)
    assert back.has_stats and back.has_combat and back.traits is not None       # nothing is left to regenerate later


def test_saved_values_are_read_back_never_regenerated(db, monkeypatch):
    npc = FACTORY.generate(PERSISTENT, seed=21)
    save_npc(db, 1, npc)
    expected = dump(npc)
    import app.npc as npc_module
    for name in ("_roll_stats", "_roll_skills", "_roll_traits", "_roll_talent"):
        monkeypatch.setattr(npc_module, name, lambda *a, **k: pytest.fail("a saved NPC must not be regenerated"))
    back = load_npc(db, 1, npc.id)
    ensure_all(back)
    npc_power(back)
    assert dump(back) == expected


def test_no_power_value_is_stored_anywhere(db):
    for model in (NpcRecord, CharacterRelationship):
        assert not [c for c in model.__table__.columns.keys() if "power" in c]
    npc = FACTORY.generate(PERSISTENT, seed=21)
    save_npc(db, 1, npc)
    assert "power" not in json.dumps(db.exec(select(NpcRecord)).one().data).lower()


def test_saving_twice_updates_one_row_and_keeps_the_identity(db):
    npc = FACTORY.generate(PERSISTENT, seed=21)
    first = save_npc(db, 1, npc)
    npc.name, npc.age = "Renamed", npc.age + 1
    second = save_npc(db, 1, npc)
    assert first.id == second.id and len(db.exec(select(NpcRecord)).all()) == 1
    assert load_npc(db, 1, npc.id).name == "Renamed"


def test_a_retried_save_that_races_another_writer_updates_instead_of_duplicating(db, monkeypatch):
    npc = FACTORY.generate(PERSISTENT, seed=21)
    save_npc(db, 1, npc)
    real, calls = npc_store._find, {"n": 0}

    def blind_once(*a):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(*a)
    monkeypatch.setattr(npc_store, "_find", blind_once)
    npc.age += 5
    save_npc(db, 1, npc)
    assert len(db.exec(select(NpcRecord)).all()) == 1 and load_npc(db, 1, npc.id).age == npc.age


def test_a_kept_character_cannot_become_temporary_again(db):
    npc = FACTORY.generate(PERSISTENT, seed=21)
    save_npc(db, 1, npc)
    demoted = npc.model_copy(deep=True, update={"category": CharacterCategory.GENERIC})
    with pytest.raises(DemotionError):
        keep_temporary_npc(db, 1, demoted)
    assert load_npc(db, 1, npc.id).category == CharacterCategory.PERSISTENT


# ---------- temporary NPCs stay out of the database unless asked ----------

def test_generic_npcs_are_not_saved_automatically(db):
    generic = FACTORY.generate(NpcSpec(species="human"), seed=2)
    with pytest.raises(TemporaryNpcError, match="keep_temporary_npc"):
        save_npc(db, 1, generic)
    assert db.exec(select(NpcRecord)).all() == []


def test_a_temporary_npc_can_survive_a_boundary_as_little_more_than_a_seed(db):
    spec = NpcSpec(species="dwarf", random_traits=1, combat_focus=2)
    generic = FACTORY.generate(spec, seed=2)
    keep_temporary_npc(db, 1, generic)
    row = db.exec(select(NpcRecord)).one()
    blob = row.data
    assert row.category == "generic" and blob["stats"] is None and blob["skills"] is None and blob["traits"] is None
    assert blob["seed"] == generic.seed and len(json.dumps(blob)) < 1500        # identity and the request, no sections
    back = load_npc(db, 1, generic.id)
    assert back == generic
    assert dump(ensure_all(back)) == dump(ensure_all(FACTORY.generate(spec, seed=2)))   # rebuilt identically


def test_temporary_npcs_are_listed_only_on_request_and_discarded_when_done(db):
    kept = FACTORY.generate(PERSISTENT, seed=1)
    save_npc(db, 1, kept)
    t1, t2 = (FACTORY.generate(NpcSpec(), seed=s) for s in (50, 51))
    for t in (t1, t2):
        keep_temporary_npc(db, 1, t)
    assert [n.id for n in list_npcs(db, 1)] == [kept.id]                        # temporary ones are left out
    assert {n.id for n in list_npcs(db, 1, include_temporary=True)} == {kept.id, t1.id, t2.id}
    assert {n.id for n in list_temporary(db, 1)} == {t1.id, t2.id}
    assert discard_temporary(db, 1, [t1.id, kept.id]) == 1                      # a kept character is never discarded
    assert {n.id for n in list_temporary(db, 1)} == {t2.id}
    assert discard_temporary(db, 1) == 1 and list_temporary(db, 1) == []
    assert load_npc(db, 1, kept.id) is not None


def test_promoting_a_stored_temporary_npc_keeps_its_id_and_replaces_its_row(db):
    generic = FACTORY.generate(NpcSpec(species="human", random_traits=1), seed=9)
    keep_temporary_npc(db, 1, generic)
    save_npc(db, 1, FACTORY.promote(generic))
    row = db.exec(select(NpcRecord)).one()
    assert row.npc_id == generic.id and row.category == "persistent"
    assert load_npc(db, 1, generic.id).has_stats


# ---------- loading ----------

def test_load_many_is_one_query_in_the_requested_order_and_skips_missing(db):
    npcs = [FACTORY.generate(PERSISTENT, seed=s) for s in range(5)]
    for n in npcs:
        save_npc(db, 1, n)
    db.commit()
    wanted = [npcs[3].id, "npc_missing", npcs[0].id, npcs[3].id]
    counted = []
    listener = lambda conn, cur, stmt, *a: counted.append(stmt)
    event.listen(db.engine, "before_cursor_execute", listener)
    try:
        got = load_npcs(db, 1, wanted)
    finally:
        event.remove(db.engine, "before_cursor_execute", listener)
    assert [n.id for n in got] == [npcs[3].id, npcs[0].id] and len(counted) == 1
    assert load_npcs(db, 1, []) == []


# ---------- relationships survive a reload ----------

def test_a_family_survives_a_real_reload(tmp_path):
    url = f"sqlite:///{tmp_path / 'family.db'}"
    engine = new_db(url)
    mum, dad, kid, grand = (FACTORY.generate(PERSISTENT, seed=s) for s in (1, 2, 3, 4))
    with Session(engine) as s:
        for n in (mum, dad, kid, grand):
            save_npc(s, 1, n)
        add_parent(s, 1, kid.id, mum.id, dad.id)
        add_relationship(s, 1, K.PARENT, kid.id, grand.id)
        add_relationship(s, 1, K.PARTNER, mum.id, dad.id)
        s.commit()
    engine.dispose()
    reopened = create_engine(url)
    init_db(reopened)
    with Session(reopened) as s:
        assert set(rel_parents(s, kid.id)) == {mum.id, dad.id}
        assert [n.id for n in rel_descendants(s, mum.id)] == [kid.id, grand.id]
        assert load_npc(s, 1, kid.id).name == kid.name
        assert rel_partners(s, mum.id) == [dad.id]


def rel_parents(s, i):
    return rel.parents_of(s, 1, i)


def rel_descendants(s, i):
    return rel.eligible_descendants(s, 1, i)


def rel_partners(s, i):
    return rel.related_to(s, 1, i, K.PARTNER)


def test_deleting_an_npc_removes_its_relationships_and_is_safe_to_repeat(db):
    a, b = (FACTORY.generate(PERSISTENT, seed=s) for s in (1, 2))
    save_npc(db, 1, a), save_npc(db, 1, b)
    add_relationship(db, 1, K.PARENT, a.id, b.id)
    assert delete_npc(db, 1, a.id) and delete_npc(db, 1, a.id) is False
    assert db.exec(select(CharacterRelationship)).all() == []
    assert rel.parents_of(db, 1, b.id) == [] and load_npc(db, 1, b.id) is not None


def test_discarding_temporary_npcs_never_touches_the_family_tree(db):
    a, b = (FACTORY.generate(PERSISTENT, seed=s) for s in (1, 2))
    save_npc(db, 1, a), save_npc(db, 1, b)
    add_relationship(db, 1, K.PARENT, a.id, b.id)
    keep_temporary_npc(db, 1, FACTORY.generate(NpcSpec(), seed=60))
    discard_temporary(db, 1)
    assert len(db.exec(select(CharacterRelationship)).all()) == 1


# ---------- deleting a campaign, and the leak that used to exist ----------

def test_deleting_a_campaign_removes_its_npcs_and_relationships(client):  # noqa: F811
    game = new_game(client, seed=7)
    cid = client.get("/api/campaigns").json()[0]["id"]
    a, b = (FACTORY.generate(PERSISTENT, seed=s) for s in (1, 2))
    with Session(client.engine) as s:
        for n in (a, b):
            save_npc(s, cid, n)
        add_relationship(s, cid, K.PARENT, a.id, b.id)
        keep_temporary_npc(s, cid, FACTORY.generate(NpcSpec(), seed=3))
        s.commit()
    assert client.delete(f"/api/campaigns/{cid}").status_code == 204
    with Session(client.engine) as s:
        assert s.exec(select(NpcRecord)).all() == [] and s.exec(select(CharacterRelationship)).all() == []
    assert game["character"]


def test_a_new_campaign_that_reuses_an_id_does_not_inherit_old_npcs(client):  # noqa: F811
    new_game(client, seed=7)
    cid = client.get("/api/campaigns").json()[0]["id"]
    with Session(client.engine) as s:
        save_npc(s, cid, FACTORY.generate(PERSISTENT, seed=1))
        s.commit()
    client.delete(f"/api/campaigns/{cid}")
    new_game(client, seed=8)
    reused = client.get("/api/campaigns").json()[0]["id"]
    assert reused == cid                                                        # SQLite hands the id out again
    with Session(client.engine) as s:
        assert list_npcs(s, reused, include_temporary=True) == []


def test_one_campaign_does_not_see_anothers_npcs(db):
    npc = FACTORY.generate(PERSISTENT, seed=1)
    save_npc(db, 1, npc)
    assert load_npc(db, 2, npc.id) is None and list_npcs(db, 2) == []
    delete_campaign_npcs(db, 2)
    assert load_npc(db, 1, npc.id) is not None


# ---------- existing saves ----------

def test_old_orphans_are_cleaned_once_and_valid_rows_survive(db):
    keep = FACTORY.generate(PERSISTENT, seed=1)
    save_npc(db, 1, keep)
    db.add(NpcRecord(campaign_id=999, npc_id="npc_orphan", category="persistent", status="active",
                     species_id="human", name="Orphan"))                         # left by a campaign deleted long ago
    db.add(CharacterRelationship(campaign_id=999, kind="friend", source_id="a", target_id="b"))
    db.commit()
    init_db(db.engine)
    init_db(db.engine)                                                          # and it is safe to repeat
    db.expire_all()
    assert [r.npc_id for r in db.exec(select(NpcRecord))] == [keep.id]
    assert db.exec(select(CharacterRelationship)).all() == []


def test_an_old_save_upgrades_keeps_its_player_and_gains_working_npc_tables(tmp_path):
    engine = make_old_save(tmp_path / "old.db")
    before = {t: {c["name"] for c in inspect(engine).get_columns(t)} for t in inspect(engine).get_table_names()}
    init_db(engine)
    after = inspect(engine)
    assert {"npcrecord", "characterrelationship"} <= set(after.get_table_names())
    for table, columns in before.items():                                       # nothing existing lost a column
        assert columns <= {c["name"] for c in after.get_columns(table)}
    with Session(engine) as s:
        player = s.exec(select(CharacterRecord)).first()
        assert player.name == "Old Aren"
        a, b = (FACTORY.generate(PERSISTENT, seed=n) for n in (1, 2))
        save_npc(s, 1, a), save_npc(s, 1, b)
        add_relationship(s, 1, K.PARENT, a.id, b.id)
        s.commit()
        assert s.exec(select(CharacterRecord)).first().name == "Old Aren"       # the player is untouched
        assert len(s.exec(select(CharacterRecord)).all()) == 1


def test_npc_work_never_alters_the_playable_character_table(db):
    db.add(CharacterRecord(campaign_id=1, name="Aren"))
    db.commit()
    before = dump_row(db.exec(select(CharacterRecord)).one())
    npc = FACTORY.generate(PERSISTENT, seed=1)
    save_npc(db, 1, npc)
    add_relationship(db, 1, K.PARENT, rel.player_ref(db.exec(select(CharacterRecord)).one()), npc.id)
    delete_npc(db, 1, npc.id)
    assert dump_row(db.exec(select(CharacterRecord)).one()) == before


def dump_row(rec):
    return json.dumps(rec.model_dump(mode="json"), sort_keys=True, default=str)
