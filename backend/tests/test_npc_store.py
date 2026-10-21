"""Saving and loading NPCs. Generic NPCs are temporary; persistent and named ones survive a save."""
import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.db import init_db
from app.models import Campaign, CharacterRecord, NpcRecord
from app.npc import CharacterCategory, LifecycleStatus, NpcFactory, NpcSpec, named_npc, load_named_characters, npc_power
from app.npc_store import TemporaryNpcError, delete_npc, list_npcs, load_npc, save_npc
from tests.test_migration import make_old_save

FACTORY = NpcFactory()
PERSISTENT = NpcSpec(category="persistent", species="human", random_traits=1, combat_focus=2)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add_all([Campaign(name="one", seed=1), Campaign(name="two", seed=2)])
        s.commit()
        yield s


def test_a_saved_npc_loads_back_identical(session):
    npc = FACTORY.generate(PERSISTENT, seed=5)
    save_npc(session, 1, npc)
    session.commit()
    session.expire_all()
    assert load_npc(session, 1, npc.id) == npc
    assert load_npc(session, 1, "npc_does_not_exist") is None


def test_the_values_stay_fixed_after_loading_and_power_is_unchanged(session):
    npc = FACTORY.generate(PERSISTENT, seed=5)
    save_npc(session, 1, npc)
    loaded = load_npc(session, 1, npc.id)
    assert loaded.stats == npc.stats and loaded.skills == npc.skills and loaded.traits == npc.traits
    assert npc_power(loaded).model_dump() == npc_power(npc).model_dump()


def test_saving_again_updates_the_same_row_and_keeps_the_id(session):
    npc = FACTORY.generate(PERSISTENT, seed=5)
    first = save_npc(session, 1, npc)
    npc.name, npc.age = "Renamed Person", npc.age + 3
    npc.mark_deceased("old age")
    second = save_npc(session, 1, npc)
    assert first.id == second.id and len(session.exec(select(NpcRecord)).all()) == 1
    row = session.exec(select(NpcRecord)).one()
    assert (row.npc_id, row.name, row.status) == (npc.id, "Renamed Person", "deceased")     # id never changed
    assert load_npc(session, 1, npc.id).cause_of_death == "old age"


def test_generic_npcs_are_temporary_and_are_not_saved(session):
    generic = FACTORY.generate(NpcSpec(), seed=2)
    with pytest.raises(TemporaryNpcError, match="promote"):
        save_npc(session, 1, generic)
    assert session.exec(select(NpcRecord)).all() == []
    save_npc(session, 1, FACTORY.promote(generic))                          # promoted, it can be kept
    assert load_npc(session, 1, generic.id).category == CharacterCategory.PERSISTENT


def test_named_characters_can_be_saved(session, tmp_path):
    p = tmp_path / "n.yaml"
    p.write_text("characters:\n  - {id: old_hob, name: Old Hob, species: dwarf, age: 140}\n")
    hob = named_npc(load_named_characters(path=p)["old_hob"])
    save_npc(session, 1, hob)
    loaded = load_npc(session, 1, "named_old_hob")
    assert loaded == hob and loaded.name == "Old Hob"


def test_each_campaign_keeps_its_own_copy_of_the_same_character(session):
    npc = FACTORY.generate(PERSISTENT, seed=5)
    save_npc(session, 1, npc)
    other = npc.model_copy(deep=True)
    other.mark_deceased("a fall")
    save_npc(session, 2, other)
    assert load_npc(session, 1, npc.id).alive and not load_npc(session, 2, npc.id).alive
    assert delete_npc(session, 2, npc.id) and load_npc(session, 1, npc.id) is not None


def test_list_filters(session):
    humans = [FACTORY.generate(PERSISTENT, seed=s) for s in range(3)]
    elf = FACTORY.generate(NpcSpec(category="persistent", species="elf"), seed=9)
    for n in [*humans, elf]:
        save_npc(session, 1, n)
    humans[0].retire()
    save_npc(session, 1, humans[0])
    save_npc(session, 2, FACTORY.generate(PERSISTENT, seed=77))
    assert len(list_npcs(session, 1)) == 4 and len(list_npcs(session, 2)) == 1
    assert [n.id for n in list_npcs(session, 1, species_id="elf")] == [elf.id]
    assert [n.id for n in list_npcs(session, 1, status=LifecycleStatus.RETIRED)] == [humans[0].id]
    assert len(list_npcs(session, 1, category=CharacterCategory.PERSISTENT)) == 4
    assert list_npcs(session, 1, category=CharacterCategory.NAMED) == []


def test_the_database_refuses_a_duplicate_id_within_a_campaign(session):
    npc = FACTORY.generate(PERSISTENT, seed=5)
    save_npc(session, 1, npc)
    session.add(NpcRecord(campaign_id=1, npc_id=npc.id, category="persistent", status="active",
                          species_id="human", name="dup"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_delete_reports_whether_anything_was_removed(session):
    npc = FACTORY.generate(PERSISTENT, seed=5)
    save_npc(session, 1, npc)
    assert delete_npc(session, 1, npc.id) is True and delete_npc(session, 1, npc.id) is False


def test_the_npc_table_is_added_to_an_old_save_without_touching_the_player(tmp_path):
    """A save from before NPCs (and before several later columns) upgrades; the player's record survives."""
    engine = make_old_save(tmp_path / "old.db")
    init_db(engine)
    assert NpcRecord.__tablename__ in inspect(engine).get_table_names()
    with Session(engine) as s:
        assert s.exec(select(CharacterRecord)).first().name == "Old Aren"
        save_npc(s, 1, FACTORY.generate(PERSISTENT, seed=5))
        s.commit()
        assert len(list_npcs(s, 1)) == 1
