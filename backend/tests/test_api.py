import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app import engine as sim
from app.db import get_session
from app.main import app
from app.models import CharacterRecord


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    def override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override
    tc = TestClient(app)
    tc.engine = engine
    yield tc
    app.dependency_overrides.clear()


def new_game(client, name="Aren", seed=42, species="human"):
    r = client.post("/api/campaign", json={"character_name": name, "seed": seed, "species": species})
    assert r.status_code == 201, r.text
    return r.json()


def first_open(options):
    return next(o["id"] for o in options if o["available"])


def play_one_year(client):
    ch = client.get("/api/character").json()
    if ch["pending_event"]:
        ev = ch["pending_event"]
        ch = client.post(f"/api/events/{ev['id']}/choices/{first_open(ev['choices'])}").json()
        if not ch["alive"]:
            return ch
    actions = client.get("/api/actions").json()
    if actions:
        assert client.post(f"/api/actions/{actions[0]['id']}").status_code == 200
    return client.post("/api/turn/advance").json()


def advance_to_stage(client, stage_id):
    for _ in range(400):
        ch = client.get("/api/character").json()
        if ch["stage"]["id"] == stage_id and not ch["pending_event"]:
            return ch
        assert ch["alive"], "died before reaching the stage"
        play_one_year(client)
    raise AssertionError("never reached stage")


# ---------- reference data ----------

def test_species_list(client):
    species = client.get("/api/species").json()
    assert {s["id"] for s in species} == {"human", "elf", "dwarf", "wolf", "slime", "dragonkin"}
    wolf = next(s for s in species if s["id"] == "wolf")
    assert wolf["category"] == "beast" and wolf["lifespan"] == 14 and wolf["stat_modifiers"]["dexterity"] == 10
    assert wolf["traits"][0]["name"] == "Fleet Footwork"
    assert next(s for s in species if s["id"] == "dragonkin")["stat_cap"] == 120


def test_skills_endpoint(client):
    skills = client.get("/api/skills").json()
    assert skills["swordsmanship"] == "combat" and skills["magic"] == "magic" and len(skills) == 25
    assert not {"elemental_magic", "spirit_magic", "necromancy", "enchantment"} & set(skills)


# ---------- no campaign yet ----------

@pytest.mark.parametrize("path", ["/api/campaign", "/api/character", "/api/actions", "/api/history"])
def test_404_without_active_campaign(client, path):
    assert client.get(path).status_code == 404


def test_mutations_404_without_campaign(client):
    assert client.post("/api/turn/advance").status_code == 404
    assert client.post("/api/actions/rest").status_code == 404


# ---------- campaigns & species ----------

@pytest.mark.parametrize("species", ["human", "elf", "dwarf", "wolf", "slime", "dragonkin"])
def test_create_a_campaign_for_every_species(client, species):
    created = new_game(client, species=species)
    ch = created["character"]
    assert ch["species"]["id"] == species and ch["age"] == 0 and ch["alive"]
    assert ch["stage"]["id"] == "infancy" and ch["location"]
    assert set(ch["stats"]) == {"constitution", "dexterity", "wisdom", "strength", "charisma"}
    assert len(ch["axes"]) == 6 and ch["alignment"]
    assert client.get("/api/character").json()["species"]["name"] == ch["species"]["name"]


def test_trait_bonuses_show_in_effective_stats(client):
    ch = new_game(client, species="dwarf")["character"]                    # born with Iron Constitution (+6)
    assert [t["id"] for t in ch["traits"]] == ["iron_constitution"] or "iron_constitution" in [t["id"] for t in ch["traits"]]
    assert ch["effective_stats"]["constitution"] == ch["stats"]["constitution"] + 6


def test_unknown_species_is_400(client):
    r = client.post("/api/campaign", json={"character_name": "X", "species": "unicorn"})
    assert r.status_code == 400 and "unicorn" in r.json()["detail"]


def test_blank_name_rejected(client):
    assert client.post("/api/campaign", json={"character_name": ""}).status_code == 422


def test_new_campaign_deactivates_old_one(client):
    first = new_game(client, "A")
    second = new_game(client, "B", species="elf")
    assert client.get("/api/campaign").json()["id"] == second["id"]
    assert [c["active"] for c in client.get("/api/campaigns").json()] == [True, False]
    client.post(f"/api/campaigns/{first['id']}/activate")
    assert client.get("/api/character").json()["name"] == "A"


def test_delete_campaign(client):
    a = new_game(client, "A")["id"]
    new_game(client, "B")
    assert client.delete(f"/api/campaigns/{a}").status_code == 204
    assert len(client.get("/api/campaigns").json()) == 1
    assert client.delete(f"/api/campaigns/{a}").status_code == 404


# ---------- life stages through the API ----------

def test_infants_have_nothing_to_choose_but_can_age(client):
    new_game(client, seed=3)
    ch = client.get("/api/character").json()
    if ch["pending_event"]:                       # the story may open with an event
        ev = ch["pending_event"]
        assert len(ev["choices"]) == 1            # infants cannot really decide anything
        client.post(f"/api/events/{ev['id']}/choices/{ev['choices'][0]['id']}")
        ch = client.get("/api/character").json()
    assert ch["stage"]["id"] == "infancy" and "too young" in ch["stage"]["description"].lower()
    assert client.get("/api/actions").json() == []
    assert client.post("/api/actions/play").status_code == 400
    assert client.post("/api/turn/advance").status_code in (200,)


def test_actions_unlock_with_age(client):
    new_game(client, seed=3)
    ch = advance_to_stage(client, "early_childhood")
    assert [a["id"] for a in client.get("/api/actions").json()] == ["play"]
    ch = advance_to_stage(client, "adolescence")
    ids = [a["id"] for a in client.get("/api/actions").json()]
    assert "work" in ids and any(i.startswith("train:") for i in ids)


def test_one_action_per_year_then_advance(client):
    new_game(client, seed=3)
    advance_to_stage(client, "adolescence")
    actions = client.get("/api/actions").json()
    r = client.post(f"/api/actions/{actions[0]['id']}")
    assert r.status_code == 200 and r.json()["actions_taken_labels"]
    # Adolescence allows three actions per year, so more are still on offer.
    assert client.get("/api/actions").json()
    age = r.json()["age"]
    assert client.post("/api/turn/advance").json()["age"] == age + 1


def test_species_flavoured_actions(client):
    new_game(client, species="wolf", seed=5)
    advance_to_stage(client, "young_adult")
    labels = {a["id"]: a["label"] for a in client.get("/api/actions").json()}
    assert labels["work"] == "Hunt and forage"


# ---------- events ----------

def test_event_flow_with_locked_choices_visible(client):
    new_game(client, seed=1, species="elf")
    for _ in range(300):
        ch = client.get("/api/character").json()
        if ch["pending_event"] or not ch["alive"]:
            break
        play_one_year(client)
    ev = ch["pending_event"]
    assert ev and ev["title"] and ev["text"]
    assert all({"id", "label", "available", "requirement"} <= set(c) for c in ev["choices"])
    assert any(c["available"] for c in ev["choices"])

    assert client.post("/api/turn/advance").status_code == 409
    assert client.get("/api/actions").json() == []
    assert client.post(f"/api/events/not_this_one/choices/{ev['choices'][0]['id']}").status_code == 409
    assert client.post(f"/api/events/{ev['id']}/choices/nope").status_code == 400

    open_choice = first_open(ev["choices"])
    r = client.post(f"/api/events/{ev['id']}/choices/{open_choice}")
    assert r.status_code == 200
    assert any(h["kind"] == "event" for h in client.get("/api/history").json())


def test_locked_choice_is_refused_with_a_reason(client):
    new_game(client, species="elf", seed=2)
    with Session(client.engine) as s:                              # set up the altar scene directly
        rec = s.exec(select(CharacterRecord)).first()
        rec.age, rec.location, rec.pending_event = 30, "ancient_forest", "forest_ancient_altar"
        rec.stats = {**rec.stats, "wisdom": 5}
        s.add(rec)
        s.commit()
    ev = client.get("/api/character").json()["pending_event"]
    study = next(c for c in ev["choices"] if c["id"] == "study")
    assert not study["available"] and study["requirement"] == "Requires Wisdom 20+"
    r = client.post("/api/events/forest_ancient_altar/choices/study")
    assert r.status_code == 409 and "Wisdom" in r.json()["detail"]
    assert client.post("/api/events/forest_ancient_altar/choices/leave").status_code == 200


# ---------- the hidden talent ----------

def talent_seed(species="human"):
    for seed in range(100_000):
        c = sim.new_character("x", species, seed)
        if c.hidden_talent:
            return seed, c.hidden_talent
    raise AssertionError


def test_dormant_talent_is_stored_but_never_exposed(client):
    seed, talent = talent_seed()
    ch = new_game(client, seed=seed)["character"]
    assert ch["talent"] is None
    for _ in range(12):
        client.post("/api/turn/advance") if not client.get("/api/character").json()["pending_event"] else None
    text = client.get("/api/character").text + client.get("/api/history").text + client.get("/api/campaigns").text
    assert talent not in text and "hidden_talent" not in text and "dormant" not in text
    with Session(client.engine) as s:                              # ...but it IS saved in the database
        rec = s.exec(select(CharacterRecord)).first()
        assert rec.hidden_talent == talent and rec.talent_state in ("dormant", "awakened")


def test_talent_survives_reloads_unchanged(client):
    seed, talent = talent_seed()
    new_game(client, seed=seed)
    for _ in range(20):
        client.get("/api/character")
        client.get("/api/campaign")
        client.get("/api/campaigns")
    with Session(client.engine) as s:
        assert s.exec(select(CharacterRecord)).first().hidden_talent == talent
    # Restarting the campaign with the same seed gives the same talent, but loading never rerolls.
    assert sim.new_character("x", "human", seed).hidden_talent == talent


def test_awakened_talent_becomes_visible(client):
    seed, talent = talent_seed()
    new_game(client, seed=seed)
    with Session(client.engine) as s:
        rec = s.exec(select(CharacterRecord)).first()
        rec.talent_state = "awakened"
        s.add(rec)
        s.commit()
    shown = client.get("/api/character").json()["talent"]
    assert shown and shown["id"] == talent and shown["name"]


# ---------- persistence and death ----------

def test_state_persists_between_requests(client):
    new_game(client)
    for _ in range(8):
        play_one_year(client)
    ch = client.get("/api/character").json()
    history = client.get("/api/history").json()
    assert ch["age"] >= 1 and history[0]["kind"] == "birth"
    assert all({"age", "kind", "text"} <= set(h) for h in history)
    assert len(client.get("/api/history", params={"limit": 2}).json()) == 2


def test_full_life_death_and_summary(client):
    new_game(client, seed=9, species="wolf")
    ch = client.get("/api/character").json()
    for _ in range(300):
        if not ch["alive"]:
            break
        ch = play_one_year(client)
    assert not ch["alive"] and ch["cause_of_death"]
    summary = ch["summary"]
    assert summary["species"] == "Wolf" and summary["age"] == ch["age"]
    assert {"traits", "alignment", "unawakened_talent", "awakened_talent"} <= set(summary)
    assert client.post("/api/turn/advance").status_code == 409
    assert client.get("/api/actions").json() == []
    assert client.get("/api/history").json()[-1]["kind"] == "death"


def test_character_json_never_contains_effects(client):
    """Event choices must not leak their outcomes."""
    new_game(client, seed=1, species="elf")
    for _ in range(300):
        ch = client.get("/api/character").json()
        if ch["pending_event"]:
            assert "effects" not in json.dumps(ch["pending_event"])
            break
        play_one_year(client)
