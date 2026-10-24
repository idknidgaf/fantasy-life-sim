"""The Character tab's three groups: family, acquaintances, notable. Filtering, duplicates, empty states, saves."""
import pytest
from sqlmodel import Session, select

from app import named
from app import relationships as rel
from app.descendants import create_descendant, descendant_seed
from app.known import record_known
from app.models import CharacterRecord
from app.npc_store import save_npc
from tests.progress_helpers import person
from tests.test_api import client, new_game  # noqa: F401  (client is a fixture)
from tests.test_legacy import cont, kill, latest, set_latest
from tests.test_named_npcs import defs  # noqa: F401  (fixture)


def listing(client, category=None, **params):
    if category:
        params["category"] = category
    r = client.get("/api/npcs", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def ids(client, category=None, **params):
    return [i["id"] for i in listing(client, category, **params)["items"]]


@pytest.fixture
def game(client, defs, monkeypatch):  # noqa: F811
    """Player (60) with: two children, a friend who is also their child's other parent (family AND acquaintance),
    a plain friend, a rival, a met stranger with no relationship, a named mentor (notable) and a named friend."""
    new_game(client)
    monkeypatch.setattr("app.named.definitions", lambda: defs)
    set_latest(client, age=60)
    out = {}
    with Session(client.engine) as s:
        rec = latest(s)
        pr = player_ref = rel.player_ref(rec)
        out["player"] = pr
        out["kid1"] = create_descendant(s, 1, [pr], seed=descendant_seed(1, 0), age=10).npc.id
        out["kid2"] = create_descendant(s, 1, [pr], seed=descendant_seed(1, 1), age=5).npc.id
        for name, seed in (("spouse", 11), ("friend", 12), ("rival", 13), ("stranger", 14)):
            n = person(age=40, seed=seed)
            save_npc(s, 1, n)
            out[name] = n.id
        rel.add_parent(s, 1, out["kid1"], out["spouse"])                       # the spouse is kid1's second parent
        rel.add_relationship(s, 1, "friend", pr, out["spouse"])                # ... and the player's friend
        rel.add_relationship(s, 1, "friend", pr, out["friend"])
        rel.add_relationship(s, 1, "rival", out["rival"], pr)                  # either direction counts
        record_known(s, 1, out["stranger"], "encounter")                       # met, no relationship
        out["sage"] = named.instantiate(s, 1, "test_sage", defs=defs).npc.id   # introduced by a story
        s.commit()
    return out


# ---------- filtering ----------

def test_family_is_the_players_family_links_only(client, game):
    assert set(ids(client, "family")) == {game["kid1"], game["kid2"]}
    assert game["friend"] not in ids(client, "family") and game["stranger"] not in ids(client, "family")


def test_a_childs_other_parent_is_family_only_through_a_family_link_to_the_player(client, game):
    # the spouse is kid1's parent, so a step-relative of the player, not a parent/child/sibling of the PLAYER:
    # family_of(player) does not include them unless they are a parent, child, sibling, grandparent or cousin
    assert game["spouse"] not in ids(client, "family")
    assert game["spouse"] in ids(client, "acquaintances")                       # but they are the player's friend


def test_acquaintances_need_a_personal_relationship_and_a_persistent_npc(client, game):
    assert set(ids(client, "acquaintances")) == {game["spouse"], game["friend"], game["rival"]}
    assert game["stranger"] not in ids(client, "acquaintances")                 # met, but no established relationship
    assert game["kid1"] not in ids(client, "acquaintances")                     # a family link is not a friendship
    assert game["sage"] not in ids(client, "acquaintances")                     # named NPCs are notable, not acquaintances


def test_notable_is_named_npcs_the_player_knows_of_without_a_relationship(client, game):
    assert ids(client, "notable") == [game["sage"]]
    assert game["stranger"] not in ids(client, "notable") and game["kid1"] not in ids(client, "notable")


def test_an_unmet_named_npc_is_not_notable(client, game, defs):  # noqa: F811
    with Session(client.engine) as s:
        unmet = named.instantiate(s, 1, "test_plain", defs=defs).npc.id          # saved, then forgotten: not recorded as met
        from app.models import KnownNpc
        for row in s.exec(select(KnownNpc).where(KnownNpc.npc_id == unmet)):
            s.delete(row)
        s.commit()
    assert unmet not in ids(client, "notable") and ids(client, "notable") == [game["sage"]]


def test_no_category_returns_everyone_known_once(client, game):
    everyone = ids(client)
    assert len(everyone) == len(set(everyone)) and game["stranger"] in everyone
    assert {game["kid1"], game["kid2"], game["friend"], game["rival"], game["sage"], game["spouse"]} <= set(everyone)


def test_an_unknown_category_is_rejected(client, game):
    assert client.get("/api/npcs", params={"category": "enemies"}).status_code == 422


# ---------- overlap and duplicates ----------

def test_an_npc_can_be_in_several_groups_but_once_per_group(client, game):
    with Session(client.engine) as s:
        rel.add_relationship(s, 1, "friend", rel.player_ref(latest(s)), game["kid1"])      # a child who is also a friend
        rel.add_relationship(s, 1, "mentor", game["kid1"], rel.player_ref(latest(s)))      # ... and a mentor, a third link
        s.commit()
    fam, acq = ids(client, "family"), ids(client, "acquaintances")
    assert fam.count(game["kid1"]) == 1 and acq.count(game["kid1"]) == 1
    assert listing(client, "family")["total"] == len(fam) and listing(client, "acquaintances")["total"] == len(acq)
    item = next(i for i in listing(client, "family")["items"] if i["id"] == game["kid1"])
    assert item["categories"] == ["family", "acquaintances"]
    assert {"child", "friend", "mentor"} <= set(item["relations"]) and len(item["relations"]) == len(set(item["relations"]))


def test_a_named_npc_with_a_friend_link_is_notable_and_listed_once(client, game):
    with Session(client.engine) as s:
        rel.add_relationship(s, 1, "friend", rel.player_ref(latest(s)), game["sage"])
        s.commit()
    assert ids(client, "notable") == [game["sage"]]
    assert game["sage"] not in ids(client, "acquaintances")
    assert next(i for i in listing(client)["items"] if i["id"] == game["sage"])["categories"] == ["notable"]


def test_categories_are_reported_on_every_entry(client, game):
    by_id = {i["id"]: i for i in listing(client)["items"]}
    assert by_id[game["kid2"]]["categories"] == ["family"]
    assert by_id[game["friend"]]["categories"] == ["acquaintances"]
    assert by_id[game["stranger"]]["categories"] == []
    assert by_id[game["sage"]]["categories"] == ["notable"]


# ---------- family roles from existing links ----------

def test_family_roles_come_from_the_existing_relationship_links(client, game):
    with Session(client.engine) as s:
        pr = rel.player_ref(latest(s))
        gp, parent = person(age=90, seed=61), person(age=62, seed=62)
        for n in (gp, parent):
            save_npc(s, 1, n); record_known(s, 1, n.id)
        rel.add_parent(s, 1, pr, parent.id)
        rel.add_parent(s, 1, parent.id, gp.id)
        sib = create_descendant(s, 1, [parent.id], seed=63, age=30).npc                        # a full sibling
        adult_kid = create_descendant(s, 1, [pr], seed=65, age=30).npc
        grandkid = create_descendant(s, 1, [adult_kid.id], seed=64, age=1).npc
        s.commit()
    roles = {i["id"]: i["relations"] for i in listing(client, "family", limit=100)["items"]}
    assert "parent" in roles[parent.id] and "grandparent" in roles[gp.id]
    assert "sibling" in roles[sib.id] and "child" in roles[game["kid1"]]
    assert "descendant" in roles[grandkid.id]


def test_relatives_the_player_has_not_met_are_not_listed(client, game):
    with Session(client.engine) as s:
        hidden = person(age=20, seed=71)
        save_npc(s, 1, hidden)
        rel.add_parent(s, 1, hidden.id, game["kid2"])                      # a descendant, but never recorded as met
        s.commit()
    assert hidden.id not in ids(client, "family") and hidden.id not in ids(client)


def test_earlier_lives_are_not_listed_as_npcs(client, game):
    with Session(client.engine) as s:
        old = person(age=90, seed=72)
        save_npc(s, 1, old); record_known(s, 1, old.id)
        rel.add_parent(s, 1, rel.player_ref(latest(s)), old.id)
        s.commit()
    assert all(i.startswith(("npc_", "named_")) for i in ids(client, "family"))


# ---------- empty states, pagination, status ----------

def test_a_new_game_has_three_empty_groups(client):  # noqa: F811
    new_game(client)
    for category in ("family", "acquaintances", "notable"):
        assert listing(client, category) == {"total": 0, "limit": 25, "offset": 0, "items": []}


def test_each_group_paginates_and_reports_its_own_total(client, game):
    first = listing(client, "acquaintances", limit=2)
    second = listing(client, "acquaintances", limit=2, offset=2)
    assert first["total"] == 3 and len(first["items"]) == 2 and len(second["items"]) == 1
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}


def test_status_and_species_filters_work_within_a_category(client, game):
    with Session(client.engine) as s:
        from app.npc_store import load_npc
        n = load_npc(s, 1, game["rival"])
        n.mark_deceased("a duel")
        save_npc(s, 1, n); s.commit()
    assert ids(client, "acquaintances", status="deceased") == [game["rival"]]
    row = next(i for i in listing(client, "acquaintances")["items"] if i["id"] == game["rival"])
    assert row["status"] == "deceased"


# ---------- what an entry shows: only what exists ----------

def test_entries_carry_name_age_species_status_title_and_no_invented_fields(client, game):
    sage = next(i for i in listing(client, "notable")["items"])
    assert sage["name"] and isinstance(sage["age"], int) and sage["species"]["name"] and sage["status"] == "active"
    assert sage["title"] is not None                                           # authored on the named character
    assert "gender" not in sage and sage["closeness"] is None                  # nothing stores either, so neither is invented
    plain = next(i for i in listing(client, "family")["items"])
    assert plain["title"] is None                                              # persistent NPCs have no job to show


# ---------- the heir, and existing saves ----------

def test_after_an_heir_takes_over_their_family_is_still_the_family(client, game):
    set_latest(client, age=60)
    with Session(client.engine) as s:
        old_ref = rel.player_ref(latest(s))
    kill(client)
    assert cont(client, game["kid1"]).status_code == 200
    with Session(client.engine) as s:
        new_ref = rel.player_ref(latest(s))
    assert new_ref != old_ref
    fam = ids(client, "family")
    assert game["kid2"] in fam                                                  # the heir's sibling
    assert game["kid1"] not in fam                                              # the heir is the player now, not an NPC
    assert game["spouse"] in ids(client, "family") or game["spouse"] in ids(client, "acquaintances")


def test_listing_never_changes_anything_and_works_on_a_save_without_progress_data(client, game):
    with Session(client.engine) as s:
        from app.models import NpcRecord
        row = s.exec(select(NpcRecord).where(NpcRecord.npc_id == game["friend"])).one()
        data = dict(row.data)
        data.pop("progress"); data.pop("surname")
        row.data = data                                                         # an NPC saved before this feature existed
        s.add(row); s.commit()
        before = [r.data for r in s.exec(select(NpcRecord).order_by(NpcRecord.id))]
    assert game["friend"] in ids(client, "acquaintances")
    with Session(client.engine) as s:
        assert [r.data for r in s.exec(select(NpcRecord).order_by(NpcRecord.id))] == before


def test_the_group_queries_use_a_bounded_number_of_queries(client, game):
    from sqlalchemy import event
    def count():
        n = []
        listener = lambda *a: n.append(1)
        event.listen(client.engine, "before_cursor_execute", listener)
        try:
            for c in ("family", "acquaintances", "notable"):
                client.get("/api/npcs", params={"category": c, "limit": 100})
        finally:
            event.remove(client.engine, "before_cursor_execute", listener)
        return len(n)
    base = count()
    with Session(client.engine) as s:
        for i in range(15):
            n = person(age=30, seed=3000 + i)
            save_npc(s, 1, n)
            rel.add_relationship(s, 1, "friend", rel.player_ref(latest(s)), n.id)
        s.commit()
    assert count() == base


# ---------- frontend contract (the project's convention: types.ts mirrors the API models) ----------

def _ts_fields(source: str, name: str) -> set[str]:
    import re
    m = re.search(rf"export interface {name} \{{(.*?)\n\}}", source, re.S)
    assert m, f"interface {name} missing from frontend/src/types.ts"
    return set(re.findall(r"^\s+(\w+)\??:", m.group(1), re.M))


@pytest.mark.parametrize("ts_name, model_name", [("NpcSummary", "NpcSummaryRead"), ("NpcList", "NpcListRead")])
def test_frontend_types_match_the_npc_api(ts_name, model_name):
    from pathlib import Path
    from app import npc_api
    ts = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "types.ts").read_text(encoding="utf-8")
    assert _ts_fields(ts, ts_name) == set(getattr(npc_api, model_name).model_fields)


def test_the_frontend_group_names_are_the_backends():
    import re
    from pathlib import Path
    from app.character_groups import CATEGORIES
    ts = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "types.ts").read_text(encoding="utf-8")
    m = re.search(r"export type NpcGroup = (.*)", ts)
    assert set(re.findall(r"'(\w+)'", m.group(1))) == set(CATEGORIES)
