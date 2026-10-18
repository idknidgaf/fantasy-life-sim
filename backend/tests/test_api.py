import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.db import get_session
from app.main import app


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    def override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def new_game(client, name="Aren", seed=42):
    r = client.post("/api/campaign", json={"character_name": name, "seed": seed})
    assert r.status_code == 201
    return r.json()


def play_one_year(client):
    """Resolve any event, take the first action, advance. Returns the character."""
    ch = client.get("/api/character").json()
    if ch["pending_event"]:
        ev = ch["pending_event"]
        ch = client.post(f"/api/events/{ev['id']}/choices/{ev['choices'][0]['id']}").json()
    actions = client.get("/api/actions").json()
    if actions:
        assert client.post(f"/api/actions/{actions[0]['id']}").status_code == 200
    return client.post("/api/turn/advance").json()


def play_until_event(client, limit=80):
    for _ in range(limit):
        ch = client.get("/api/character").json()
        if ch["pending_event"] or not ch["alive"]:
            return ch
        play_one_year(client)
    raise AssertionError("no event appeared")


# ---------- no campaign yet ----------

@pytest.mark.parametrize("path", ["/api/campaign", "/api/character", "/api/actions", "/api/history"])
def test_404_without_active_campaign(client, path):
    assert client.get(path).status_code == 404


def test_mutations_404_without_campaign(client):
    assert client.post("/api/turn/advance").status_code == 404
    assert client.post("/api/actions/rest").status_code == 404


# ---------- campaigns ----------

def test_create_and_retrieve_campaign(client):
    created = new_game(client)
    assert created["active"] and created["character"]["age"] == 0
    got = client.get("/api/campaign").json()
    assert got["id"] == created["id"] and got["character"]["name"] == "Aren"
    ch = client.get("/api/character").json()
    assert set(ch["stats"]) == {"constitution", "dexterity", "wisdom", "strength", "charisma"}
    assert ch["summary"] is None and ch["pending_event"] is None


def test_blank_name_rejected(client):
    assert client.post("/api/campaign", json={"character_name": ""}).status_code == 422


def test_new_campaign_deactivates_old_one(client):
    first = new_game(client, "A")
    second = new_game(client, "B")
    assert client.get("/api/campaign").json()["id"] == second["id"]
    lst = client.get("/api/campaigns").json()
    assert [c["active"] for c in lst] == [True, False]
    client.post(f"/api/campaigns/{first['id']}/activate")
    assert client.get("/api/character").json()["name"] == "A"


def test_delete_campaign(client):
    a = new_game(client, "A")["id"]
    new_game(client, "B")
    assert client.delete(f"/api/campaigns/{a}").status_code == 204
    assert len(client.get("/api/campaigns").json()) == 1
    assert client.delete(f"/api/campaigns/{a}").status_code == 404
    assert client.post(f"/api/campaigns/{a}/activate").status_code == 404


# ---------- actions and turns ----------

def test_actions_then_advance(client):
    new_game(client)
    actions = client.get("/api/actions").json()
    assert actions and {"id", "label"} <= set(actions[0])
    r = client.post(f"/api/actions/{actions[0]['id']}")
    assert r.status_code == 200 and r.json()["action_taken"] == actions[0]["id"]
    assert r.json()["action_taken_label"]
    assert client.get("/api/actions").json() == []                      # used up
    assert client.post(f"/api/actions/{actions[0]['id']}").status_code == 409
    after = client.post("/api/turn/advance").json()
    assert after["age"] == 1 and after["action_taken"] is None


def test_unavailable_action_is_400(client):
    new_game(client)
    assert client.post("/api/actions/train:not_a_skill").status_code == 400
    assert client.post("/api/actions/work").status_code == 400          # newborn can't work


def test_state_persists_between_requests(client):
    new_game(client)
    for _ in range(5):
        play_one_year(client)
    ch = client.get("/api/character").json()
    assert ch["age"] >= 1
    history = client.get("/api/history").json()
    assert history[0]["kind"] == "birth"
    assert all({"age", "kind", "text"} <= set(h) for h in history)
    assert len(client.get("/api/history", params={"limit": 2}).json()) == 2


# ---------- events ----------

def test_event_flow(client):
    new_game(client, seed=1)
    ch = play_until_event(client)
    assert ch["pending_event"], "expected an event within 80 years"
    ev = ch["pending_event"]
    assert ev["title"] and ev["text"] and len(ev["choices"]) >= 2
    assert "effects" not in ev["choices"][0]                            # no spoilers

    # While an event is pending, the year is frozen.
    assert client.post("/api/turn/advance").status_code == 409
    assert client.get("/api/actions").json() == []

    # Wrong event / invalid choice
    assert client.post(f"/api/events/not_this_one/choices/{ev['choices'][0]['id']}").status_code == 409
    assert client.post(f"/api/events/{ev['id']}/choices/nope").status_code == 400

    r = client.post(f"/api/events/{ev['id']}/choices/{ev['choices'][0]['id']}")
    assert r.status_code == 200 and r.json()["pending_event"] is None or not r.json()["alive"]
    assert any(h["kind"] == "event" for h in client.get("/api/history").json())
    # nothing pending any more
    assert client.post(f"/api/events/{ev['id']}/choices/{ev['choices'][0]['id']}").status_code == 409


# ---------- death ----------

def test_full_life_death_and_summary(client):
    new_game(client, seed=9)
    ch = client.get("/api/character").json()
    for _ in range(300):
        if not ch["alive"]:
            break
        ch = play_one_year(client)
    assert not ch["alive"] and ch["cause_of_death"]
    assert ch["summary"]["age"] == ch["age"]
    assert client.post("/api/turn/advance").status_code == 409
    assert client.get("/api/actions").json() == []
    assert client.get("/api/history").json()[-1]["kind"] == "death"


def test_skills_endpoint(client):
    skills = client.get("/api/skills").json()
    assert skills["swordsmanship"] == "combat" and len(skills) == 28
