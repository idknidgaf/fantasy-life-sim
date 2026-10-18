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


def play_until_death(client, life_id):
    while True:
        choices = client.get(f"/lives/{life_id}/choices").json()
        if not choices:
            return
        r = client.post(f"/lives/{life_id}/advance", json={"choice": choices[0]["id"]})
        assert r.status_code == 200
        if not r.json()["alive"]:
            return


def test_create_and_load(client):
    r = client.post("/lives", json={"name": "Aren", "seed": 42})
    assert r.status_code == 201
    life = r.json()
    assert life["age"] == 0 and life["alive"] and len(life["stats"]) == 5
    assert client.get(f"/lives/{life['id']}").json()["name"] == "Aren"


def test_blank_name_rejected(client):
    assert client.post("/lives", json={"name": ""}).status_code == 422


def test_advance_saves_progress(client):
    life_id = client.post("/lives", json={"name": "Aren", "seed": 1}).json()["id"]
    for _ in range(8):
        choice = client.get(f"/lives/{life_id}/choices").json()[0]["id"]
        client.post(f"/lives/{life_id}/advance", json={"choice": choice})
    saved = client.get(f"/lives/{life_id}").json()
    assert saved["age"] == 8 and len(saved["history"]) == 8


def test_invalid_choice_rejected(client):
    life_id = client.post("/lives", json={"name": "Aren"}).json()["id"]
    r = client.post(f"/lives/{life_id}/advance", json={"choice": "train:necromancy"})
    assert r.status_code == 400


def test_full_life_death_and_summary(client):
    life_id = client.post("/lives", json={"name": "Aren", "seed": 9}).json()["id"]
    assert client.get(f"/lives/{life_id}/summary").status_code == 409   # still alive
    play_until_death(client, life_id)
    life = client.get(f"/lives/{life_id}").json()
    assert not life["alive"] and life["cause_of_death"]
    assert client.post(f"/lives/{life_id}/advance", json={}).status_code == 409
    assert client.get(f"/lives/{life_id}/choices").json() == []
    summary = client.get(f"/lives/{life_id}/summary").json()
    assert summary["age"] == life["age"]


def test_list_and_delete(client):
    a = client.post("/lives", json={"name": "A"}).json()["id"]
    client.post("/lives", json={"name": "B"})
    assert len(client.get("/lives").json()) == 2
    assert client.delete(f"/lives/{a}").status_code == 204
    assert client.get(f"/lives/{a}").status_code == 404


def test_skills_endpoint(client):
    skills = client.get("/skills").json()
    assert skills["swordsmanship"] == "combat"
    assert len(skills) == 28
