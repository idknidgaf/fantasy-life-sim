"""Legacy: completing a life, eligible heirs, the playable-character transition, retries, save/load, and the
existing death paths (old age and combat). Synthetic families only; no game content."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from app import legacy
from app import relationships as rel
from app.content import get_content
from app.db import get_session, init_db
from app.descendants import create_descendant, descendant_seed
from app.main import app
from app.models import (
    CharacterRecord, CharacterRelationship, CombatRecord, LifeRecord, NpcRecord, PlayableRole, StoryEventInstance,
)
from app.npc import CharacterCategory, NpcFactory, NpcSpec, to_character
from app.npc_store import keep_temporary_npc, load_npc, save_npc
from app.power import calculate_power
from app.relationships import player_ref
from tests.test_api import client, new_game  # noqa: F401  (client is a fixture)
from tests.test_combat import combat_def

CT = get_content()
FACTORY = NpcFactory()


# ---------------------------------------------------------------- helpers

def latest(s: Session) -> CharacterRecord:
    return s.exec(select(CharacterRecord).order_by(CharacterRecord.id.desc())).first()


def set_latest(client, **fields):
    with Session(client.engine) as s:
        rec = latest(s)
        for k, v in fields.items():
            setattr(rec, k, v)
        s.add(rec)
        s.commit()


def make_family(client, ages=(3,), parent_age=60, seed=5):
    """Children of the (current) playable character, saved as persistent NPCs. Returns their ids."""
    set_latest(client, age=parent_age)
    ids = []
    with Session(client.engine) as s:
        rec = latest(s)
        for i, age in enumerate(ages):
            ids.append(create_descendant(s, rec.campaign_id, [player_ref(rec)], seed=descendant_seed(seed, i), age=age).npc.id)
        s.commit()
    return ids


def adult_npc(seed=77, species="human"):
    return FACTORY.generate(NpcSpec(category="persistent", species=species, age_range=(35, 35)), seed=seed)


def kill(client, cause="old age"):
    """Mark the current character dead directly (the life is then recorded lazily, as for an older save)."""
    set_latest(client, alive=False, cause_of_death=cause, health=0)


def dead_game(client, ages=(3,), **kw):
    new_game(client, **kw)
    ids = make_family(client, ages)
    kill(client)
    return ids


def legacy_state(client):
    r = client.get("/api/legacy")
    assert r.status_code == 200, r.text
    return r.json()


def cont(client, npc_id=None):
    return client.post("/api/legacy/continue", json={} if npc_id is None else {"npc_id": npc_id})


def counts(client):
    with Session(client.engine) as s:
        return {"records": len(s.exec(select(CharacterRecord)).all()), "npcs": len(s.exec(select(NpcRecord)).all()),
                "lives": len(s.exec(select(LifeRecord)).all()), "roles": len(s.exec(select(PlayableRole)).all()),
                "links": len(s.exec(select(CharacterRelationship)).all())}


def edges(client):
    with Session(client.engine) as s:
        return sorted((r.kind, r.source_id, r.target_id) for r in s.exec(select(CharacterRelationship)))


# ---------------------------------------------------------------- no eligible descendants

def test_a_living_character_has_no_heirs_and_cannot_continue(client):
    new_game(client)
    make_family(client)
    s = legacy_state(client)
    assert s["status"] == "alive" and s["heirs"] == [] and s["lives"] == []
    assert cont(client).status_code == 409


def test_death_with_no_descendants_is_game_over_and_nobody_is_picked_for_you(client):
    new_game(client)
    kill(client)
    with Session(client.engine) as s:                                  # strangers exist, but are not family
        save_npc(s, 1, adult_npc(1))
        save_npc(s, 1, adult_npc(2))
        keep_temporary_npc(s, 1, FACTORY.generate(NpcSpec(), seed=3))
        s.commit()
    before = counts(client)
    state = legacy_state(client)
    assert state["status"] == "game_over" and state["heirs"] == []
    assert [l["name"] for l in state["lives"]] == ["Aren"] and state["lives"][0]["successor_name"] is None
    r = cont(client)
    assert r.status_code == 409 and "ends here" in r.json()["detail"]
    assert cont(client, "npc_whatever").status_code == 409
    assert client.get("/api/character").json()["alive"] is False        # still the dead character, no one took over
    assert counts(client) == {**before, "lives": 1}                      # only the life record was added


# ---------------------------------------------------------------- one heir, several heirs

def test_one_eligible_descendant_continues_without_a_choice(client):
    [kid] = dead_game(client, ages=(7,))
    state = legacy_state(client)
    assert state["status"] == "awaiting_heir" and [h["npc_id"] for h in state["heirs"]] == [kid]
    h = state["heirs"][0]
    assert h["relation"] == "child" and h["age"] == 7 and h["species_name"] == "Human" and h["power"] >= 0
    r = cont(client)                                                    # no id: there is only one
    assert r.status_code == 200 and r.json()["alive"] is True and r.json()["age"] == 7


def test_several_descendants_need_a_choice_and_the_choice_is_honoured(client):
    first, second, third = dead_game(client, ages=(3, 5, 9))
    state = legacy_state(client)
    assert [h["npc_id"] for h in state["heirs"]] == [first, second, third]
    r = cont(client)
    assert r.status_code == 400 and "choose" in r.json()["detail"].lower()
    assert counts(client)["records"] == 1                                # nothing happened
    name = {h["npc_id"]: h["name"] for h in state["heirs"]}[second]
    r = cont(client, second)
    assert r.status_code == 200 and r.json()["name"] == name and r.json()["age"] == 5
    with Session(client.engine) as s:                                    # the siblings are untouched
        assert {n for n in (first, third) if load_npc(s, 1, n) is not None} == {first, third}
        assert load_npc(s, 1, second) is None                            # the chosen one is now the playable record


# ---------------------------------------------------------------- who is (not) eligible

def test_dead_retired_unrelated_and_temporary_characters_are_never_offered(client):
    new_game(client)
    alive, dead, retired = make_family(client, ages=(3, 4, 5))
    with Session(client.engine) as s:
        gone = load_npc(s, 1, dead)
        gone.mark_deceased("a fever")
        save_npc(s, 1, gone)
        away = load_npc(s, 1, retired)
        away.retire()
        save_npc(s, 1, away)
        stranger = adult_npc(9)
        save_npc(s, 1, stranger)
        keep_temporary_npc(s, 1, FACTORY.generate(NpcSpec(), seed=4))
        s.commit()
    kill(client)
    assert [h["npc_id"] for h in legacy_state(client)["heirs"]] == [alive]
    for refused in (dead, retired, stranger.id):
        r = cont(client, refused)
        assert r.status_code == 409 and "not an eligible" in r.json()["detail"]
    assert counts(client)["records"] == 1


def test_grandchildren_count_nearest_generation_first_and_are_labelled(client):
    new_game(client)
    set_latest(client, age=60)
    with Session(client.engine) as s:
        rec = latest(s)
        child = create_descendant(s, 1, [player_ref(rec)], seed=descendant_seed(1, 0), age=30).npc
        grand = create_descendant(s, 1, [child.id], seed=descendant_seed(1, 1), age=2).npc
        s.commit()
    kill(client)
    heirs = legacy_state(client)["heirs"]
    assert [(h["npc_id"], h["relation"]) for h in heirs] == [(child.id, "child"), (grand.id, "descendant")]


def test_a_dead_ancestors_other_lines_are_not_offered(client):
    """Only the dead character's own descendants: not their spouse's other children, not an earlier life's family."""
    new_game(client)
    [kid] = make_family(client, ages=(3,))
    with Session(client.engine) as s:
        outsider = adult_npc(21)
        save_npc(s, 1, outsider)
        create_descendant(s, 1, [outsider.id], seed=descendant_seed(2, 0), age=2)     # someone else's child
        s.commit()
    kill(client)
    assert [h["npc_id"] for h in legacy_state(client)["heirs"]] == [kid]


# ---------------------------------------------------------------- switching the active character

def test_the_chosen_heir_becomes_the_active_character_for_everything(client):
    [kid] = dead_game(client, ages=(12,))
    with Session(client.engine) as s:
        expected_power = calculate_power(to_character(load_npc(s, 1, kid), CT), CT).total
        old_id = latest(s).id
    new = cont(client, kid).json()
    assert new["id"] != old_id and new["alive"] is True and new["age"] == 12
    assert client.get("/api/character").json()["id"] == new["id"]
    assert client.get("/api/campaign").json()["character"]["id"] == new["id"]
    assert client.get("/api/power").json()["total"] == pytest.approx(expected_power)
    assert client.get("/api/history").json()[0]["kind"] == "legacy"      # reachable in the existing history log
    assert client.get("/api/actions").status_code == 200
    aged = client.post("/api/turn/advance")                              # progression starts from the heir's own age
    assert aged.status_code == 200 and aged.json()["age"] == 13
    with Session(client.engine) as s:
        role = s.exec(select(PlayableRole)).one()
        assert role.character_id == new["id"] and latest(s).id == new["id"]


def test_the_new_character_is_the_same_person_not_a_reroll(client):
    [kid] = dead_game(client, ages=(9,), species="human")
    with Session(client.engine) as s:
        npc = load_npc(s, 1, kid)
        stored = to_character(npc, CT)
    new = cont(client, kid).json()
    with Session(client.engine) as s:
        rec = latest(s)
        assert (rec.name, rec.species_id, rec.age) == (npc.name, npc.species_id, npc.age)
        assert rec.stats == npc.stats and rec.skills == stored.skills and rec.traits == stored.traits
        assert (rec.hidden_talent, rec.talent_state) == (npc.talent, npc.talent_state)
        assert rec.alive and rec.cause_of_death is None and rec.health and rec.pending_event is None
    assert new["name"] == npc.name and new["species"]["id"] == npc.species_id


def test_a_heir_who_is_an_adult_starts_in_their_own_stage(client):
    new_game(client)
    set_latest(client, age=70)
    with Session(client.engine) as s:
        kid = create_descendant(s, 1, [player_ref(latest(s))], seed=descendant_seed(3, 0), age=25).npc.id
        s.commit()
    kill(client)
    ch = cont(client, kid).json()
    assert ch["age"] == 25 and ch["stage"]["id"] == "young_adult"


# ---------------------------------------------------------------- both characters' data and the family survive

def test_the_deceased_record_is_untouched_and_the_family_links_survive(client):
    kid, sibling = dead_game(client, ages=(8, 4))
    with Session(client.engine) as s:
        other = adult_npc(31)
        save_npc(s, 1, other)
        rel.add_parent(s, 1, kid, other.id)                              # the heir's second parent
        rel.add_relationship(s, 1, "friend", other.id, kid)             # a symmetric link, order matters
        rel.add_relationship(s, 1, "mentor", other.id, kid)
        s.commit()
        dead_before = latest(s).model_dump()
        old_ref = player_ref(latest(s))
    cont(client, kid)
    with Session(client.engine) as s:
        new = latest(s)
        new_ref = player_ref(new)
        dead_after = s.get(CharacterRecord, dead_before["id"]).model_dump()
        assert dead_after == dead_before                                 # nothing about the dead character changed
        assert rel.parents_of(s, 1, new_ref) == [old_ref, other.id]      # both parents, still
        assert rel.children_of(s, 1, old_ref) == [new_ref, sibling]      # the dead character's children, in order
        assert rel.related_to(s, 1, new_ref, "friend") == [other.id]
        assert rel.related_to(s, 1, other.id, "friend") == [new_ref]
        assert rel.related_to(s, 1, new_ref, "mentor") == []
        assert rel.related_to(s, 1, other.id, "mentor") == [new_ref]
        friend = s.exec(select(CharacterRelationship).where(CharacterRelationship.kind == "friend")).one()
        assert friend.source_id < friend.target_id                       # symmetric links stay stored in order
        assert rel.descendants_of(s, 1, old_ref)[:2] == [new_ref, sibling]
        assert not [l for l in s.exec(select(CharacterRelationship)) if kid in (l.source_id, l.target_id)]


def test_the_new_characters_own_descendants_stay_descendants(client):
    new_game(client)
    set_latest(client, age=70)
    with Session(client.engine) as s:
        child = create_descendant(s, 1, [player_ref(latest(s))], seed=descendant_seed(4, 0), age=30).npc
        grand = create_descendant(s, 1, [child.id], seed=descendant_seed(4, 1), age=2).npc
        s.commit()
    kill(client)
    cont(client, child.id)
    with Session(client.engine) as s:
        new_ref = player_ref(latest(s))
        assert rel.children_of(s, 1, new_ref) == [grand.id]
        assert rel.eligible_descendants(s, 1, new_ref)[0].id == grand.id
    kill(client, "a fall")                                                # the second generation dies in turn
    state = legacy_state(client)
    assert state["status"] == "awaiting_heir" and [h["npc_id"] for h in state["heirs"]] == [grand.id]
    assert [l["name"] for l in state["lives"]][0] == "Aren" and len(state["lives"]) == 2
    assert state["lives"][0]["successor_name"] == state["lives"][1]["name"]


def test_each_life_keeps_its_own_history_and_state(client):
    [kid] = dead_game(client, ages=(6,))
    cont(client, kid)
    client.post("/api/turn/advance")
    with Session(client.engine) as s:
        old, new = s.exec(select(CharacterRecord).order_by(CharacterRecord.id)).all()
        assert old.alive is False and old.history[-1]["kind"] != "legacy"
        assert new.history[0]["kind"] == "legacy" and old.name in new.history[0]["text"]
        assert old.history != new.history and old.age != new.age


# ---------------------------------------------------------------- save / load

def test_the_transition_survives_a_restart(tmp_path):
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
        [kid] = dead_game(c, ages=(10,))
        name = {h["npc_id"]: h["name"] for h in legacy_state(c)["heirs"]}[kid]
        assert cont(c, kid).status_code == 200
        c.engine.dispose()

        c = connect()                                                     # a fresh process, same file
        ch = c.get("/api/character").json()
        assert ch["name"] == name and ch["alive"] and ch["age"] == 10
        state = legacy_state(c)
        assert state["status"] == "alive" and len(state["lives"]) == 1 and state["lives"][0]["successor_name"] == name
        assert cont(c, kid).json()["id"] == ch["id"]                      # a retry after the restart is still harmless
        assert c.post("/api/turn/advance").json()["age"] == 11
        assert counts(c)["records"] == 2 and counts(c)["roles"] == 1
        c.engine.dispose()
    finally:
        app.dependency_overrides.clear()


def test_init_db_upgrades_an_existing_save_with_the_new_tables(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    init_db(eng)
    with eng.begin() as conn:
        conn.execute(text("DROP TABLE liferecord"))
        conn.execute(text("DROP TABLE playablerole"))
    init_db(eng)                                                          # what starting the server does
    with Session(eng) as s:
        assert s.exec(select(LifeRecord)).all() == [] and s.exec(select(PlayableRole)).all() == []


def test_a_dead_character_from_an_older_save_is_recorded_once_when_first_seen(client):
    new_game(client)
    kill(client)                                                          # dead, with no life record: an old save
    assert counts(client)["lives"] == 0
    legacy_state(client)
    legacy_state(client)
    assert counts(client)["lives"] == 1 and legacy_state(client)["status"] == "game_over"


# ---------------------------------------------------------------- duplicates, retries and failures

def test_repeating_the_same_choice_changes_nothing_and_returns_the_same_character(client):
    kid, other = dead_game(client, ages=(5, 6))
    first = cont(client, kid).json()
    after = counts(client)
    for _ in range(3):
        again = cont(client, kid)
        assert again.status_code == 200 and again.json()["id"] == first["id"]
    assert cont(client).json()["id"] == first["id"]                       # even without naming the heir
    assert counts(client) == after
    assert counts(client)["records"] == 2 and counts(client)["lives"] == 1


def test_choosing_someone_else_after_the_life_went_on_is_refused(client):
    kid, other = dead_game(client, ages=(5, 6))
    cont(client, kid)
    r = cont(client, other)
    assert r.status_code == 409 and "already went on" in r.json()["detail"]
    with Session(client.engine) as s:
        assert load_npc(s, 1, other) is not None and len(s.exec(select(CharacterRecord)).all()) == 2


def test_a_second_request_that_loses_the_race_creates_nothing(client):
    [kid] = dead_game(client, ages=(5,))
    with Session(client.engine) as s:
        s.add(LifeRecord(campaign_id=1, character_id=latest(s).id, successor_npc_id="npc_someone_else"))
        s.commit()                                                        # as if another request had claimed it
    before = counts(client)
    r = cont(client, kid)
    assert r.status_code == 409 and counts(client) == before


def test_a_failed_transition_rolls_back_completely(client, monkeypatch):
    [kid] = dead_game(client, ages=(5,))
    legacy_state(client)
    before, links = counts(client), edges(client)

    def boom(*a, **k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(legacy, "rekey_character", boom)
    with pytest.raises(RuntimeError):
        cont(client, kid)
    assert counts(client) == before and edges(client) == links            # nothing half-done
    with Session(client.engine) as s:
        life = s.exec(select(LifeRecord)).one()
        assert life.successor_npc_id is None and life.successor_character_id is None
        assert load_npc(s, 1, kid) is not None
    monkeypatch.undo()
    assert cont(client, kid).status_code == 200                           # and it can still be done properly


def test_a_death_is_recorded_once_and_the_database_enforces_it(client):
    new_game(client)
    kill(client)
    with Session(client.engine) as s:
        rec = latest(s)
        first = legacy.complete_life(s, rec)
        assert legacy.complete_life(s, rec).id == first.id
        s.commit()
        s.add(LifeRecord(campaign_id=1, character_id=rec.id))
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()
        s.add(PlayableRole(campaign_id=1, character_id=rec.id))
        s.commit()
        s.add(PlayableRole(campaign_id=1, character_id=rec.id))
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()


def test_a_dead_character_still_cannot_act_and_no_second_life_record_appears(client):
    dead_game(client)
    assert client.post("/api/turn/advance").status_code == 409
    legacy_state(client)
    assert client.post("/api/turn/advance").status_code == 409
    assert counts(client)["lives"] == 1


# ---------------------------------------------------------------- the existing death paths

def test_dying_of_old_age_in_play_completes_the_life_at_once(client):
    new_game(client, seed=3)
    [kid] = make_family(client, ages=(4,))
    set_latest(client, age=190)
    for _ in range(40):
        ch = client.post("/api/turn/advance")
        if ch.status_code == 409 or not ch.json()["alive"]:
            if ch.status_code == 200:
                break
            # a pending event blocks the turn; resolve it and carry on
            pe = client.get("/api/character").json()["pending_event"]
            if pe:
                client.post(f"/api/events/{pe['id']}/choices/{next(o['id'] for o in pe['choices'] if o['available'])}")
    assert client.get("/api/character").json()["alive"] is False
    assert counts(client)["lives"] == 1                                   # recorded by the fatal request itself
    assert legacy_state(client)["status"] == "awaiting_heir"
    assert cont(client).json()["alive"] is True


def test_dying_in_battle_completes_the_life_and_the_summary_is_kept(client, monkeypatch):
    monkeypatch.setattr(combat_def("village_bully", "fight").opponent, "skills", {"martial_arts": 100000})
    new_game(client)
    [kid] = make_family(client, ages=(4,))
    ch = None
    for seed in range(1, 500):
        set_latest(client, pending_event="village_bully", queued_events=[], skills={"martial_arts": 1}, age=seed % 50 + 5,
                   alive=True, health=None, cause_of_death=None)
        ch = client.post("/api/events/village_bully/choices/fight").json()
        if ch["battle_summary"]["outcome"] == "defeat":
            break
    assert ch["alive"] is False and ch["battle_summary"]["died"]
    assert counts(client)["lives"] == 1
    with Session(client.engine) as s:
        assert len(s.exec(select(CombatRecord)).all()) == 1               # the fight stays in the dead life's record
    new = cont(client, kid).json()
    assert new["alive"] is True and new["battle_summary"] is None and new["pending_event"] is None


def test_open_story_encounters_of_the_dead_life_are_closed_and_the_next_life_starts_clean(client, monkeypatch):
    from tests.story_helpers import ev, library
    lib = library(ev("omen"))
    monkeypatch.setattr("app.story.service.get_story_library", lambda: lib)
    new_game(client)
    [kid] = make_family(client, ages=(4,))
    assert client.post("/api/story/events/omen/start").status_code == 201
    kill(client)
    legacy_state(client)
    with Session(client.engine) as s:
        inst = s.exec(select(StoryEventInstance)).one()
        assert (inst.status, inst.end_reason) == ("expired", "death")
    cont(client, kid)
    assert client.get("/api/story/events/pending").json() == []           # the heir has their own, empty, story state
    assert client.get("/api/story/state").json()["flags"] == {}
    assert client.post("/api/story/events/omen/start").status_code == 201  # and can meet the same encounter afresh


def test_normal_play_after_the_transition_works_for_several_years(client):
    [kid] = dead_game(client, ages=(2,))
    cont(client, kid)
    for _ in range(6):
        pe = client.get("/api/character").json()["pending_event"]
        if pe:
            client.post(f"/api/events/{pe['id']}/choices/{next(o['id'] for o in pe['choices'] if o['available'])}")
        r = client.post("/api/turn/advance")
        assert r.status_code in (200, 409)


def test_deleting_the_campaign_removes_the_legacy_rows(client):
    [kid] = dead_game(client)
    cont(client, kid)
    cid = client.get("/api/campaign").json()["id"]
    assert client.delete(f"/api/campaigns/{cid}").status_code == 204
    with Session(client.engine) as s:
        assert s.exec(select(LifeRecord)).all() == [] and s.exec(select(PlayableRole)).all() == []
        assert s.exec(select(CharacterRecord)).all() == []


def test_a_campaign_without_a_role_row_still_plays_its_newest_character(client):
    new_game(client)
    with Session(client.engine) as s:
        assert s.exec(select(PlayableRole)).all() == []                   # every older save looks like this
    assert client.get("/api/character").json()["name"] == "Aren"


# ---------------------------------------------------------------- frontend contract

def _ts_fields(source: str, name: str) -> set[str]:
    m = re.search(rf"export interface {name} \{{(.*?)\n\}}", source, re.S)
    assert m, f"interface {name} missing from frontend/src/types.ts"
    return set(re.findall(r"^\s+(\w+)\??:", m.group(1), re.M))


@pytest.mark.parametrize("ts_name, model", [("Legacy", legacy.LegacyRead), ("Heir", legacy.HeirRead),
                                            ("PastLife", legacy.PastLifeRead)])
def test_frontend_types_match_the_legacy_api(ts_name, model):
    ts = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "types.ts").read_text(encoding="utf-8")
    assert _ts_fields(ts, ts_name) == set(model.model_fields)
