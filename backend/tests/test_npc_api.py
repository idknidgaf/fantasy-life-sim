"""The read-only NPC API: context, validation, response shape, known filtering, bounds, privacy, no mutation."""
import pytest
from sqlalchemy import event
from sqlmodel import Session, select

from app import relationships as rel
from app.descendants import create_descendant
from app.known import is_known, known_ids, record_known
from app.models import CharacterRecord, KnownNpc, NpcRecord
from app.npc import NpcFactory, NpcSpec
from app.npc_store import keep_temporary_npc, save_npc
from tests.progress_helpers import person
from tests.test_api import client, new_game  # noqa: F401  (client is a fixture)


@pytest.fixture
def world(client):  # noqa: F811
    """A running game: the player (30), their child (known by relationship), a met stranger, an unmet stranger."""
    new_game(client)
    with Session(client.engine) as s:
        rec = s.exec(select(CharacterRecord)).first()
        rec.age = 30
        s.add(rec)
        pr = rel.player_ref(rec)
        kid = create_descendant(s, 1, [pr], seed=7).npc
        met, unmet = person(age=50, seed=21), person(age=50, seed=22)
        save_npc(s, 1, met)
        save_npc(s, 1, unmet)
        record_known(s, 1, met.id, "encounter")
        s.commit()
        return {"client": client, "kid": kid.id, "met": met.id, "unmet": unmet.id, "player": pr}


def get(world, path, **params):
    return world["client"].get(f"/api/npcs{path}", params=params)


# ---------- known NPCs ----------

def test_only_known_npcs_are_listed(world):
    body = get(world, "").json()
    ids = {i["id"] for i in body["items"]}
    assert ids == {world["kid"], world["met"]} and body["total"] == 2
    assert world["unmet"] not in ids


def test_known_means_a_record_or_a_link_to_the_player_not_merely_existing(client, world):
    with Session(client.engine) as s:
        assert is_known(s, 1, world["player"], world["met"]) and is_known(s, 1, world["player"], world["kid"])
        assert not is_known(s, 1, world["player"], world["unmet"])
        record_known(s, 1, world["unmet"], "manual")
        record_known(s, 1, world["unmet"], "story")                 # again: harmless, first source kept
        s.commit()
        assert s.exec(select(KnownNpc).where(KnownNpc.npc_id == world["unmet"])).one().source == "manual"
    assert world["unmet"] in {i["id"] for i in get(world, "").json()["items"]}


def test_temporary_npcs_are_never_known_or_exposed(client, world):
    with Session(client.engine) as s:
        light = NpcFactory().generate(NpcSpec(species="human"), seed=99)
        keep_temporary_npc(s, 1, light)
        record_known(s, 1, light.id)
        s.commit()
    assert light.id not in {i["id"] for i in get(world, "").json()["items"]}
    assert get(world, f"/{light.id}").status_code == 404


def test_the_met_record_is_reported(world):
    body = get(world, f"/{world['met']}").json()
    assert body["met"] == {"source": "encounter", "first_met_year": 0}
    assert get(world, f"/{world['kid']}").json()["met"]["source"] == "birth"


def test_a_child_born_to_a_known_npc_is_known_too(client, world):
    with Session(client.engine) as s:
        grand = create_descendant(s, 1, [world["met"]], seed=31, age=0).npc
        s.commit()
    assert get(world, f"/{grand.id}").status_code == 200


# ---------- detail and summary shape ----------

def test_summary_shape(world):
    item = next(i for i in get(world, "").json()["items"] if i["id"] == world["kid"])
    assert set(item) == {"id", "name", "category", "status", "species", "age", "stage", "power", "relations", "title",
                         "categories", "closeness", "contact"}
    assert item["relations"] == ["child"] and item["species"]["id"] == "human" and item["age"] == 0
    assert item["stage"]["id"] == "infancy" and isinstance(item["power"], float)


def test_detail_has_age_species_stats_traits_talent_and_power(world):
    body = get(world, f"/{world['met']}").json()
    assert body["age"] == 50 and body["species"]["id"] == "human"
    assert set(body["stats"]) == {"strength", "dexterity", "constitution", "wisdom", "charisma"}
    assert set(body["effective_stats"]) == set(body["stats"]) and body["stat_cap"] > 0
    assert set(body["specialised_stats"]) == {"magic", "swordsmanship", "marksmanship", "martial_arts"}
    assert isinstance(body["traits"], list) and body["talent"] is None
    assert body["power_breakdown"]["total"] == body["power"] and body["relations"] == []


def test_power_uses_the_existing_power_system(world):
    from app.npc import npc_power
    from app.npc_store import load_npc
    with Session(world["client"].engine) as s:
        n = load_npc(s, 1, world["met"])
    assert get(world, f"/{world['met']}").json()["power"] == npc_power(n).total


def test_a_dormant_talent_never_leaks(client, world):
    with Session(client.engine) as s:
        n = person(age=40, seed=23, talent="unbroken_will")
        n.talent_state = "dormant"
        save_npc(s, 1, n)
        record_known(s, 1, n.id)
        s.commit()
    assert get(world, f"/{n.id}").json()["talent"] is None
    with Session(client.engine) as s:
        from app.npc_store import load_npc
        loaded = load_npc(s, 1, n.id)
        loaded.talent_state = "awakened"
        save_npc(s, 1, loaded)
        s.commit()
    assert get(world, f"/{n.id}").json()["talent"]["id"] == "unbroken_will"


def test_no_internal_fields_are_exposed(world):
    text = get(world, f"/{world['met']}").text + get(world, "").text + get(world, f"/{world['kid']}/family").text
    for secret in ('"seed"', '"spec"', '"flags"', '"definition"', '"campaign_id"', '"data"', '"developed_age"', '"progress"'):
        assert secret not in text


# ---------- validation and missing records ----------

def test_missing_unknown_foreign_and_malformed_ids(client, world):
    assert get(world, "/npc_0000").status_code == 404
    assert get(world, f"/{world['unmet']}").status_code == 404
    assert get(world, f"/{world['unmet']}/family").status_code == 404
    assert get(world, "/pc_1").status_code == 400 and get(world, "/whatever").status_code == 400
    assert get(world, "/npc_" + "x" * 200).status_code == 400
    with Session(client.engine) as s:                               # an NPC of ANOTHER campaign, even one marked as met
        other = person(age=30, seed=24)
        save_npc(s, 2, other)
        record_known(s, 2, other.id)
        s.commit()
    assert get(world, f"/{other.id}").status_code == 404


def test_invalid_query_parameters_are_rejected(world):
    for params in ({"limit": 0}, {"limit": 101}, {"limit": "x"}, {"offset": -1}, {"status": "missing"}):
        assert get(world, "", **params).status_code == 422


def test_no_campaign_is_a_404_like_the_other_routes(client):  # noqa: F811
    assert client.get("/api/npcs").status_code == 404


# ---------- bounded, paginated, filtered ----------

def test_results_are_paginated_and_bounded(client, world):
    with Session(client.engine) as s:
        for i in range(30):
            n = person(age=30, seed=1000 + i)
            save_npc(s, 1, n)
            record_known(s, 1, n.id)
        s.commit()
    page1 = get(world, "", limit=10).json()
    page2 = get(world, "", limit=10, offset=10).json()
    assert page1["total"] == 32 and len(page1["items"]) == 10 and len(page2["items"]) == 10
    assert not {i["id"] for i in page1["items"]} & {i["id"] for i in page2["items"]}
    assert len(get(world, "").json()["items"]) == 25                                # default
    assert len(get(world, "", limit=100).json()["items"]) == 32
    assert get(world, "", offset=500).json()["items"] == []


def test_filters_by_status_and_species(client, world):
    with Session(client.engine) as s:
        from app.npc_store import load_npc
        n = load_npc(s, 1, world["met"])
        n.mark_deceased("illness")
        save_npc(s, 1, n)
        s.commit()
    assert [i["id"] for i in get(world, "", status="deceased").json()["items"]] == [world["met"]]
    assert {i["status"] for i in get(world, "", status="active").json()["items"]} == {"active"}
    assert get(world, "", species="elf").json()["total"] == 0


def test_listing_uses_a_constant_number_of_queries(client, world):
    def count(url):
        n = []
        listener = lambda *a: n.append(1)
        event.listen(client.engine, "before_cursor_execute", listener)
        try:
            client.get(url)
        finally:
            event.remove(client.engine, "before_cursor_execute", listener)
        return len(n)
    base = count("/api/npcs?limit=100")
    with Session(client.engine) as s:
        for i in range(40):
            n = person(age=30, seed=2000 + i)
            save_npc(s, 1, n)
            record_known(s, 1, n.id)
        s.commit()
    assert count("/api/npcs?limit=100") == base


# ---------- family ----------

def test_family_endpoint_shape_and_unmet_relatives_are_anonymous(client, world):
    with Session(client.engine) as s:
        parent = person(age=45, seed=41)
        save_npc(s, 1, parent)
        sib = create_descendant(s, 1, [world["player"]], seed=8).npc          # half-sibling of nobody yet: a sibling of kid
        s.commit()
    body = get(world, f"/{world['kid']}/family").json()
    assert set(body) == {"id", "parents", "children", "siblings", "grandparents", "cousins", "truncated"}
    assert body["parents"] == [{"id": world["player"], "kind": "player", "name": body["parents"][0]["name"], "known": True, "status": "active"}]
    assert [s_["id"] for s_ in body["siblings"]] == [sib.id] and body["siblings"][0]["relation"] == "full"
    with Session(client.engine) as s:                                          # a parent the family has never met
        rel.add_parent(s, 1, world["met"], parent.id)
        s.commit()
    unmet_parent = get(world, f"/{world['met']}/family").json()["parents"][0]
    assert unmet_parent == {"id": parent.id, "kind": "npc", "name": None, "known": False, "status": None}


def test_half_siblings_and_cousins_are_reported(client, world):
    with Session(client.engine) as s:
        gp = [person(age=70, seed=50), person(age=70, seed=51)]
        for g in gp:
            save_npc(s, 1, g); record_known(s, 1, g.id)
        a = create_descendant(s, 1, [g.id for g in gp], seed=52, age=30).npc
        b = create_descendant(s, 1, [g.id for g in gp], seed=53, age=30).npc
        ca = create_descendant(s, 1, [a.id], seed=54, age=5).npc
        cb = create_descendant(s, 1, [b.id], seed=55, age=5).npc
        for n in (a, b, ca, cb):
            record_known(s, 1, n.id)
        s.commit()
    body = get(world, f"/{ca.id}/family").json()
    assert [c["id"] for c in body["cousins"]] == [cb.id] and len(body["cousins"][0]["shared_grandparents"]) == 2
    assert {g["id"] for g in body["grandparents"]} == {g.id for g in gp}


# ---------- reads never change anything ----------

def test_reading_changes_no_rows(client, world):
    def snapshot():
        with Session(client.engine) as s:
            return ([(r.npc_id, r.data) for r in s.exec(select(NpcRecord).order_by(NpcRecord.id))],
                    [(k.npc_id, k.source) for k in s.exec(select(KnownNpc))])
    before = snapshot()
    for url in ("/api/npcs", f"/api/npcs/{world['met']}", f"/api/npcs/{world['kid']}/family", "/api/npcs/npc_nope"):
        client.get(url)
    assert snapshot() == before
