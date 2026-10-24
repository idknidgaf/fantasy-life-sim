"""The yearly world tick: ageing and growth once per year, retries, reloads, old saves, determinism, and Age Up."""
import copy

import pytest
from sqlmodel import Session, select

from app import named
from app.descendants import create_descendant
from app.models import Campaign, NpcRecord
from app.npc import Npc, NpcProgress
from app.npc_store import list_npcs, load_npc, save_npc, stamp_progress, world_year_of
from app.npc_world import advance_world, sync_npc_to_year
from app.power import get_power_config
from app.progression import get_growth_config
from tests.progress_helpers import CT, new_db, person
from tests.test_api import client, new_game  # noqa: F401  (client is a fixture)
from tests.test_named_npcs import defs  # noqa: F401  (fixture)

CID = 1


@pytest.fixture
def db():
    with Session(new_db()) as s:
        yield s


def set_world(db, year, cid=CID):
    c = db.get(Campaign, cid)
    c.world_year = year
    db.add(c)
    db.flush()


def saved(db, age=20, seed=1, **kw):
    n = person(age=age, seed=seed, **kw)
    save_npc(db, CID, n)
    return n.id


# ---------- once per simulated year ----------

def test_saving_stamps_the_world_year_the_npc_is_current_for(db):
    set_world(db, 7)
    nid = saved(db)
    assert load_npc(db, CID, nid).progress.aged_year == 7 and world_year_of(db, CID) == 7


def test_an_npc_ages_and_grows_exactly_once_per_world_year(db):
    nid = saved(db, age=14)
    set_world(db, 1)
    advance_world(db, CID, 1)
    once = load_npc(db, CID, nid)
    assert once.age == 15 and once.progress.developed_age == 15 and once.progress.aged_year == 1
    for _ in range(3):                                    # a retry, or the same year processed again, changes nothing
        assert advance_world(db, CID, 1) == []
    assert load_npc(db, CID, nid) == once


def test_a_gap_of_several_years_is_applied_year_by_year(db):
    nid = saved(db, age=14)
    advance_world(db, CID, 5)
    n = load_npc(db, CID, nid)
    assert n.age == 19 and n.progress.developed_age == 19 and n.progress.aged_year == 5


def test_going_year_by_year_equals_one_big_step(db):
    a, b = saved(db, age=14, seed=2), saved(db, age=14, seed=3)
    one = copy.deepcopy(load_npc(db, CID, a))
    step = copy.deepcopy(one)
    cfg, pcfg = get_growth_config(), get_power_config()
    sync_npc_to_year(one, 6, CT, cfg, pcfg)
    for y in range(1, 7):
        sync_npc_to_year(step, y, CT, cfg, pcfg)
    assert one.stats == step.stats and one.age == step.age == 20


def test_an_npc_created_at_an_older_age_is_not_grown_again_by_the_tick(db):
    nid = saved(db, age=40, seed=4)
    before = load_npc(db, CID, nid)
    advance_world(db, CID, 0)
    assert load_npc(db, CID, nid) == before               # nothing is owed for years already developed


def test_the_same_saved_state_replays_identically(db):
    a, b = saved(db, age=10, seed=9), saved(db, age=10, seed=9)
    assert a == b                                          # same seed, same id: one NPC
    snapshot = load_npc(db, CID, a).model_dump(mode="json")
    advance_world(db, CID, 12)
    first = load_npc(db, CID, a)
    other = Npc.model_validate(snapshot)
    sync_npc_to_year(other, 12, CT, get_growth_config(), get_power_config())
    assert other.stats == first.stats and other.age == first.age


# ---------- who is and is not touched ----------

def test_named_retired_and_deceased_characters_are_left_alone(db):
    ids = {k: saved(db, age=30, seed=i) for i, k in enumerate(["ok", "retired", "dead"], start=20)}
    r = load_npc(db, CID, ids["retired"]); r.retire(); save_npc(db, CID, r)
    d = load_npc(db, CID, ids["dead"]); d.mark_deceased("illness"); save_npc(db, CID, d)
    out = advance_world(db, CID, 3)
    assert [o.npc_id for o in out] == [ids["ok"]]
    assert load_npc(db, CID, ids["retired"]).age == 30 and load_npc(db, CID, ids["dead"]).age == 30


def test_named_characters_are_not_aged_or_grown_here(db, defs):
    inst = named.instantiate(db, CID, "test_plain", defs=defs).npc
    before = load_npc(db, CID, inst.id)
    assert advance_world(db, CID, 5) == []                 # they age by their own lifecycle rule (named.advance_all)
    assert load_npc(db, CID, inst.id) == before and before.progress is None


def test_other_campaigns_are_untouched(db):
    mine = saved(db, age=20, seed=40)
    other = person(age=20, seed=41)
    save_npc(db, 2, other)
    advance_world(db, CID, 4)
    assert load_npc(db, 2, other.id).age == 20 and load_npc(db, CID, mine).age == 24


def test_a_persistent_npc_dies_of_old_age_once_past_their_lifespan(db):
    nid = saved(db, age=79, seed=50)
    advance_world(db, CID, 2)
    n = load_npc(db, CID, nid)
    assert n.status.value == "deceased" and n.cause_of_death == "old age" and n.age == 81
    assert advance_world(db, CID, 10) == [] and load_npc(db, CID, nid).age == 81


# ---------- older saves ----------

def test_an_npc_saved_before_progression_existed_is_stamped_without_backfilled_growth(db):
    n = person(age=30, seed=60)
    old = n.model_dump(mode="json")
    old.pop("progress"); old.pop("surname")
    db.add(NpcRecord(campaign_id=CID, npc_id=n.id, category="persistent", status="active", species_id="human",
                     name=n.name, age=30, data=old))
    db.flush()
    loaded = load_npc(db, CID, n.id)
    assert loaded.progress is None and loaded.surname is None
    advance_world(db, CID, 5)
    after = load_npc(db, CID, n.id)
    assert after.age == 30 and after.stats == n.stats and after.progress.aged_year == 5      # stamped, nothing owed
    advance_world(db, CID, 6)
    assert load_npc(db, CID, n.id).age == 31


def test_progress_state_survives_a_save_and_reload(db):
    nid = saved(db, age=12, seed=61)
    advance_world(db, CID, 3)
    db.commit()
    db.expire_all()
    n = load_npc(db, CID, nid)
    assert isinstance(n.progress, NpcProgress) and n.progress.developed_age == 15


def test_temporary_npcs_are_not_persisted_by_progression(db):
    from app.npc import NpcFactory, NpcSpec
    from app.npc_store import keep_temporary_npc
    light = NpcFactory().generate(NpcSpec(species="human"), seed=70)
    keep_temporary_npc(db, CID, light)
    advance_world(db, CID, 3)
    rec = db.exec(select(NpcRecord).where(NpcRecord.npc_id == light.id)).one()
    assert rec.data["progress"] is None and rec.data["stats"] is None and rec.age == light.age


# ---------- through the game ----------

def test_age_up_moves_the_world_clock_and_every_persistent_npc_once(client, db):  # noqa: F811
    new_game(client)
    from app.models import CharacterRecord
    from app.relationships import player_ref
    with Session(client.engine) as s:
        rec = s.exec(select(CharacterRecord)).first()
        rec.age = 30; s.add(rec); s.commit()
        d = create_descendant(s, 1, [player_ref(rec)], seed=7)
        s.commit()
        kid = d.npc.id
    import tests.test_legacy as lg
    lg.set_latest(client, pending_event=None, queued_events=[])
    assert client.post("/api/turn/advance").status_code == 200
    with Session(client.engine) as s:
        assert s.get(Campaign, 1).world_year == 1 and load_npc(s, 1, kid).age == 1
    lg.set_latest(client, pending_event=None, queued_events=[])
    client.post("/api/turn/advance")
    with Session(client.engine) as s:
        assert s.get(Campaign, 1).world_year == 2 and load_npc(s, 1, kid).age == 2
        n = load_npc(s, 1, kid)
        assert n.progress.developed_age == 2 and n.progress.aged_year == 2


def test_the_world_clock_does_not_move_when_the_player_dies(client):  # noqa: F811
    new_game(client)
    import tests.test_legacy as lg
    lg.set_latest(client, alive=False, cause_of_death="test")
    r = client.post("/api/turn/advance")
    assert r.status_code == 409                            # the existing rule: the dead cannot age up
    with Session(client.engine) as s:
        assert s.get(Campaign, 1).world_year == 0


def test_read_endpoints_never_age_or_grow_anyone(client):  # noqa: F811
    new_game(client)
    from app.models import CharacterRecord
    from app.relationships import player_ref
    with Session(client.engine) as s:
        rec = s.exec(select(CharacterRecord)).first()
        rec.age = 30; s.add(rec)
        d = create_descendant(s, 1, [player_ref(rec)], seed=7)
        s.commit()
        kid = d.npc.id
        snapshot = s.exec(select(NpcRecord)).one().data
    for _ in range(3):
        assert client.get(f"/api/npcs/{kid}").status_code == 200
        client.get("/api/npcs"); client.get(f"/api/npcs/{kid}/family")
    with Session(client.engine) as s:
        assert s.exec(select(NpcRecord)).one().data == snapshot
        assert s.get(Campaign, 1).world_year == 0
