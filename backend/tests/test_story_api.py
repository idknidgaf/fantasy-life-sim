"""The story HTTP API, end to end, with synthetic events."""
import pytest
from sqlmodel import Session, select

from app.models import StoryChoiceLog, StoryEventInstance, StoryFlag

from .story_helpers import ev, library
from .test_api import client, new_game  # noqa: F401  (fixture)


@pytest.fixture
def story(monkeypatch):
    def install(*events):
        lib = library(*events)
        monkeypatch.setattr("app.story.service.get_story_library", lambda: lib)
        return lib
    return install


def test_no_story_endpoints_work_without_a_campaign(client, story):
    story(ev("a"))
    assert client.get("/api/story/events/pending").status_code == 404


def test_frontend_flow_start_fetch_choose(client, story):
    story(ev("a", title="The Fork", narrative="A path divides.", choices=[
        {"id": "go", "text": "Go", "effects": [{"type": "set_flag", "flag": "went", "value": True}],
         "next_event": "b"}]), ev("b", event_type="node"))
    new_game(client)
    assert client.get("/api/story/events/pending").json() == []
    started = client.post("/api/story/events/a/start")
    assert started.status_code == 201

    [event] = client.get("/api/story/events/pending").json()
    assert (event["title"], event["narrative"], event["status"]) == ("The Fork", "A path divides.", "presented")
    assert event["choices"] == [{"id": "go", "text": "Go", "available": True, "locked_hint": None}]

    r = client.post(f"/api/story/instances/{event['instance_id']}/choices/go")
    assert r.status_code == 200
    body = r.json()
    assert body["replayed"] is False and body["next_event"] == "b" and body["next_instance_id"]

    assert [e["event_id"] for e in client.get("/api/story/events/pending").json()] == ["b"]
    state = client.get("/api/story/state").json()
    assert state["flags"] == {"went": True}


def test_error_statuses(client, story):
    story(ev("a", choices=[{"id": "go", "text": "Go"},
                           {"id": "no", "text": "No", "locked_hint": "Nope",
                            "conditions": {"type": "age_at_least", "value": 99}}]),
          ev("auto", trigger={"type": "age", "min_age": 50}))
    new_game(client)
    assert client.post("/api/story/events/ghost/start").status_code == 404
    assert client.post("/api/story/events/auto/start").status_code == 409
    iid = client.post("/api/story/events/a/start").json()["instance_id"]
    assert client.post("/api/story/events/a/start").status_code == 409            # already open
    assert client.post(f"/api/story/instances/{iid}/choices/ghost").status_code == 400
    locked = client.post(f"/api/story/instances/{iid}/choices/no")
    assert locked.status_code == 409 and "Nope" in locked.json()["detail"]
    assert client.post("/api/story/instances/9999/choices/go").status_code == 404


def test_duplicate_requests_do_not_duplicate_rewards(client, story):
    story(ev("a", choices=[{"id": "go", "text": "Go", "effects": [{"type": "increment_flag", "flag": "gold", "amount": 50}]}]))
    new_game(client)
    iid = client.post("/api/story/events/a/start").json()["instance_id"]
    replies = [client.post(f"/api/story/instances/{iid}/choices/go") for _ in range(3)]
    assert [r.status_code for r in replies] == [200, 200, 200]
    assert [r.json()["replayed"] for r in replies] == [False, True, True]
    assert client.get("/api/story/state").json()["flags"] == {"gold": 50}
    assert client.post(f"/api/story/instances/{iid}/choices/other").status_code in (400, 409)


def test_effect_failure_is_422_and_changes_nothing(client, story):
    story(ev("a", choices=[{"id": "go", "text": "Go", "effects": [
        {"type": "set_flag", "flag": "w", "value": "words"}, {"type": "increment_flag", "flag": "w"}]}]))
    new_game(client)
    iid = client.post("/api/story/events/a/start").json()["instance_id"]
    r = client.post(f"/api/story/instances/{iid}/choices/go")
    assert r.status_code == 422
    assert client.get("/api/story/state").json()["flags"] == {}
    [event] = client.get("/api/story/events/pending").json()
    assert event["status"] == "presented"                                            # still waiting


def test_story_state_is_per_life_and_removed_with_the_campaign(client, story):
    story(ev("a", choices=[{"id": "go", "text": "Go", "effects": [{"type": "set_flag", "flag": "f", "value": 1}]}]))
    first = new_game(client, name="One", seed=1)
    iid = client.post("/api/story/events/a/start").json()["instance_id"]
    client.post(f"/api/story/instances/{iid}/choices/go")
    new_game(client, name="Two", seed=2)                                              # a fresh life starts clean
    assert client.get("/api/story/state").json() == {"flags": {}, "instances": []}
    assert client.post("/api/story/events/a/start").status_code == 201                # one-time is per life
    assert client.delete(f"/api/campaigns/{first['id']}").status_code == 204
    with Session(client.engine) as s:
        assert s.exec(select(StoryFlag)).all() == []
        assert len(s.exec(select(StoryChoiceLog)).all()) == 0
        assert [i.campaign_id for i in s.exec(select(StoryEventInstance)).all()] == [first["id"] + 1]


def test_existing_event_api_is_unaffected(client, story):
    story(ev("a"))
    new_game(client)
    assert client.get("/api/character").status_code == 200
    assert client.get("/api/actions").status_code == 200
