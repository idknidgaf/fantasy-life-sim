"""Story engine wired into gameplay: triggers on action/turn, queueing, priority, blocking, history."""
import pytest
from sqlmodel import Session, select

from app.content import get_content
from app.models import CharacterRecord, StoryEventInstance
from app.story import service

from .story_helpers import ev, library, make_life, memory_engine
from .test_api import advance_to_stage, client, new_game  # noqa: F401  (fixture)


@pytest.fixture
def story(monkeypatch):
    def install(*events):
        lib = library(*events)
        monkeypatch.setattr("app.story.service.get_story_library", lambda: lib)
        return lib
    return install


def benign_legacy_event_id():
    """A legacy event whose first unrestricted choice cannot kill, check, or chain."""
    for e in get_content().events.values():
        for ch in e.choices:
            if not ch.requirements and not ch.check and not any(
                    {"die", "trigger_event"} & set(fx) for fx in ch.effects):
                return e.id, ch.id
    raise AssertionError("no benign legacy event")


def set_legacy_pending(client, event_id):
    with Session(client.engine) as s:
        rec = s.exec(select(CharacterRecord)).one()
        rec.pending_event = event_id
        s.add(rec)
        s.commit()


def story_pending(client):
    return client.get("/api/story/events/pending").json()


def clear_legacy_event(client):
    ch = client.get("/api/character").json()
    if ch["pending_event"]:
        ev_id = ch["pending_event"]["id"]
        choice = next(c["id"] for c in ch["pending_event"]["choices"] if c["available"])
        assert client.post(f"/api/events/{ev_id}/choices/{choice}").status_code == 200


def test_age_trigger_fires_on_advance_turn_and_blocks_play_until_resolved(client, story):
    story(ev("birthday", trigger={"type": "age", "min_age": 1},
             choices=[{"id": "ok", "text": "Fine", "result": "You felt older."}]))
    new_game(client)
    assert story_pending(client) == []
    client.post("/api/turn/advance")
    [event] = story_pending(client)
    assert event["event_id"] == "birthday"
    # open story events block actions and the turn, like a pending legacy event
    assert client.post("/api/turn/advance").status_code == 409
    clear_legacy_event(client)           # a legacy event may also have been rolled this year: it goes first
    assert client.post(f"/api/story/instances/{event['instance_id']}/choices/ok").status_code == 200
    assert client.post("/api/turn/advance").status_code == 200
    assert story_pending(client) == []                                    # one-time: never re-fires


def test_action_trigger_fires_on_matching_action_only(client, story):
    new_game(client)
    advance_to_stage(client, "early_childhood")                           # infants cannot act
    actions = [a["id"] for a in client.get("/api/actions").json()]
    assert len(actions) >= 1
    story(ev("on_action", trigger={"type": "action", "action": actions[0]}),
          ev("other", trigger={"type": "action", "action": "not_an_action"}))
    assert client.post(f"/api/actions/{actions[0]}").status_code == 200
    assert [e["event_id"] for e in story_pending(client)] == ["on_action"]
    # while it is open, further actions are refused and none are offered (as with a legacy event)
    assert client.get("/api/actions").json() == []
    blocked = client.post(f"/api/actions/{actions[0]}")
    assert blocked.status_code == 409 and "needs your decision" in blocked.json()["detail"]


def test_state_trigger_fires_after_story_state_changes(client, story):
    story(ev("setup", choices=[{"id": "go", "text": "Go",
                                "effects": [{"type": "set_flag", "flag": "ready", "value": True}]}]),
          ev("reaction", trigger={"type": "state"},
             conditions={"type": "flag_equals", "flag": "ready", "value": True}))
    new_game(client)
    advance_to_stage(client, "early_childhood")
    iid = client.post("/api/story/events/setup/start").json()["instance_id"]
    r = client.post(f"/api/story/instances/{iid}/choices/go").json()
    # the follow-up appears immediately, not at the player's next action
    assert r["triggered_events"] == ["reaction"]
    [shown] = story_pending(client)
    assert (shown["event_id"], shown["status"]) == ("reaction", "presented")


def test_legacy_event_goes_first_then_story_event(client, story):
    legacy_id, legacy_choice = benign_legacy_event_id()
    story(ev("tale", choices=[{"id": "ok", "text": "Fine", "result": "It ended well."}]))
    new_game(client)
    iid = client.post("/api/story/events/tale/start").json()["instance_id"]
    set_legacy_pending(client, legacy_id)

    [queued] = story_pending(client)
    assert queued["status"] == "pending"                                  # queued, NOT presented
    assert client.get("/api/character").json()["pending_event"]["id"] == legacy_id
    blocked = client.post(f"/api/story/instances/{iid}/choices/ok")
    assert blocked.status_code == 409 and "Another event" in blocked.json()["detail"]
    assert client.post("/api/turn/advance").status_code == 409

    assert client.post(f"/api/events/{legacy_id}/choices/{legacy_choice}").status_code == 200
    [shown] = story_pending(client)
    assert shown["status"] == "presented"                                 # now it is the story event's turn
    assert client.post(f"/api/story/instances/{iid}/choices/ok").status_code == 200
    assert client.post("/api/turn/advance").status_code == 200


def test_story_events_queue_in_order_and_present_one_at_a_time(client, story):
    story(ev("first"), ev("second"), ev("third"))
    new_game(client)
    ids = {n: client.post(f"/api/story/events/{n}/start").json()["instance_id"] for n in ("first", "second", "third")}
    queue = story_pending(client)
    assert [(e["event_id"], e["status"]) for e in queue] == [
        ("first", "presented"), ("second", "pending"), ("third", "pending")]
    assert client.post(f"/api/story/instances/{ids['second']}/choices/ok").status_code == 409   # not its turn
    assert client.post(f"/api/story/instances/{ids['first']}/choices/ok").status_code == 200
    assert [(e["event_id"], e["status"]) for e in story_pending(client)] == [
        ("second", "presented"), ("third", "pending")]


def test_scan_queues_without_overwriting_a_pending_legacy_event(client):
    legacy_id, _ = benign_legacy_event_id()
    lib = library(ev("auto", trigger={"type": "state"}))
    with Session(memory_engine()) as session:
        rec = make_life(session)
        rec.pending_event = legacy_id
        session.add(rec)
        session.commit()
        assert len(service.scan_triggers(session, rec, "state", library=lib)) == 1
        session.refresh(rec)
        assert rec.pending_event == legacy_id
        assert [v["status"] for v in service.get_pending(session, rec, library=lib)] == ["pending"]


def test_queue_survives_restart(tmp_path):
    from sqlmodel import SQLModel, create_engine
    url = f"sqlite:///{tmp_path / 's.db'}"
    lib = library(ev("auto", trigger={"type": "state"}))
    eng = create_engine(url)
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        rec = make_life(s)
        rec_id = rec.id
        service.scan_triggers(s, rec, "state", library=lib)
    eng.dispose()
    with Session(create_engine(url)) as s:
        rec = s.get(CharacterRecord, rec_id)
        assert [v["event_id"] for v in service.get_pending(s, rec, library=lib)] == ["auto"]


def test_trigger_scan_and_turn_are_one_transaction(client, story, monkeypatch):
    story(ev("birthday", trigger={"type": "age", "min_age": 1}))
    new_game(client)
    before = client.get("/api/character").json()["age"]

    def boom(*a, **k):
        raise RuntimeError("story scan failed")
    monkeypatch.setattr("app.story.service.run_scans", boom)
    with pytest.raises(RuntimeError):
        client.post("/api/turn/advance")
    assert client.get("/api/character").json()["age"] == before           # the year did not pass
    assert story_pending(client) == []


def test_story_choice_is_recorded_in_history_log(client, story):
    story(ev("tale", title="The Tale", choices=[
        {"id": "with", "text": "Speak", "result": "You spoke up."},
        {"id": "without", "text": "Stay quiet"}]),
        ev("tale2", title="Second", choices=[{"id": "without", "text": "Stay quiet"}]))
    new_game(client)
    a = client.post("/api/story/events/tale/start").json()["instance_id"]
    client.post(f"/api/story/instances/{a}/choices/with")
    b = client.post("/api/story/events/tale2/start").json()["instance_id"]
    client.post(f"/api/story/instances/{b}/choices/without")
    entries = [h for h in client.get("/api/history").json() if h["kind"] == "story"]
    assert [(h["text"], h["event_id"]) for h in entries] == [
        ("The Tale: You spoke up.", "story:tale"), ("Second: Stay quiet", "story:tale2")]
    # replays do not write the log again
    client.post(f"/api/story/instances/{a}/choices/with")
    assert len([h for h in client.get("/api/history").json() if h["kind"] == "story"]) == 2


def test_choices_persist_in_choice_log_even_if_never_shown_in_ui(client, story):
    story(ev("quiet", choices=[{"id": "ok", "text": "Fine"}]))
    new_game(client)
    iid = client.post("/api/story/events/quiet/start").json()["instance_id"]
    client.post(f"/api/story/instances/{iid}/choices/ok")                 # without ever calling GET pending
    from app.models import StoryChoiceLog
    with Session(client.engine) as s:
        [row] = s.exec(select(StoryChoiceLog)).all()
        assert (row.event_id, row.choice_id, row.instance_id) == ("quiet", "ok", iid)


def test_legacy_game_is_unaffected_when_there_is_no_story_content(client):
    new_game(client)
    for _ in range(5):
        ch = client.get("/api/character").json()
        if ch["pending_event"]:
            clear_legacy_event(client)
        if not client.get("/api/character").json()["alive"]:
            break
        assert client.post("/api/turn/advance").status_code in (200, 409)


# ---------------------------------------------------------------- timing: encounters appear immediately

def test_trigger_sees_the_new_age_and_updated_character_state(client, story):
    """Age is updated first; triggers are then evaluated against the resulting state."""
    story(ev("grown_up", trigger={"type": "state"},
             conditions={"type": "age_at_least", "value": 1}))
    new_game(client)
    assert story_pending(client) == []                          # age 0: not yet eligible
    client.post("/api/turn/advance")
    ch = client.get("/api/character").json()
    assert ch["age"] == 1
    assert [e["event_id"] for e in story_pending(client)] == ["grown_up"]


def test_event_is_presented_in_the_age_up_response_cycle(client, story):
    """After Age Up the very next fetch shows the encounter as presented: no extra action needed."""
    story(ev("encounter", trigger={"type": "age", "min_age": 1}))
    new_game(client)
    assert client.post("/api/turn/advance").status_code == 200
    clear_legacy_event(client)
    [e] = story_pending(client)
    assert (e["event_id"], e["status"]) == ("encounter", "presented")


def test_age_up_and_actions_stay_blocked_until_the_encounter_is_resolved(client, story):
    story(ev("encounter", trigger={"type": "age", "min_age": 1}))
    new_game(client)
    client.post("/api/turn/advance")
    clear_legacy_event(client)
    for _ in range(3):                                          # it does not go away by itself
        assert client.post("/api/turn/advance").status_code == 409
        assert [e["event_id"] for e in story_pending(client)] == ["encounter"]
    iid = story_pending(client)[0]["instance_id"]
    client.post(f"/api/story/instances/{iid}/choices/ok")
    assert story_pending(client) == []
    assert client.post("/api/turn/advance").status_code == 200


def test_every_gameplay_hook_runs_the_right_scans(client, story, monkeypatch):
    """Birth, action, Age Up and a legacy decision each evaluate triggers in the same request."""
    legacy_id, legacy_choice = benign_legacy_event_id()
    story(ev("unused"))
    calls = []
    real = service.run_scans

    def spy(ctx, scans):
        calls.append([channel for channel, _ in scans])
        return real(ctx, scans)
    monkeypatch.setattr("app.story.service.run_scans", spy)
    monkeypatch.setattr("app.main.story_service.run_scans", spy)

    new_game(client)
    assert calls == [["age", "state"]]                                  # birth
    calls.clear()
    set_legacy_pending(client, legacy_id)
    client.post(f"/api/events/{legacy_id}/choices/{legacy_choice}")
    assert calls == [["state"]]                                         # after a legacy decision
    calls.clear()
    client.post("/api/turn/advance")
    assert calls == [["age", "state"]]                                  # Age Up


def test_birth_can_open_with_an_encounter(client, story):
    story(ev("first_breath", trigger={"type": "age", "min_age": 0}))
    new_game(client)
    [e] = story_pending(client)
    assert e["event_id"] == "first_breath"


def test_skippable_event_lets_the_player_age_up_and_then_expires(client, story):
    story(ev("fleeting", trigger={"type": "age", "min_age": 1},
             lifecycle={"skippable": True, "expires_after_years": 2}))
    new_game(client)
    client.post("/api/turn/advance")
    clear_legacy_event(client)
    [e] = story_pending(client)
    assert e["skippable"] is True and e["status"] == "presented"      # the window shows...
    assert client.post("/api/turn/advance").status_code == 200        # ...but does not block aging
    clear_legacy_event(client)
    assert [x["event_id"] for x in story_pending(client)] == ["fleeting"]
    client.post("/api/turn/advance")
    clear_legacy_event(client)
    assert story_pending(client) == []                                # withdrawn after 2 years
    with Session(client.engine) as s:
        inst = s.exec(select(StoryEventInstance)).one()
        assert (inst.status, inst.end_reason) == ("expired", "timeout")


def test_blocking_events_queue_ahead_of_skippable_ones(client, story):
    story(ev("optional", lifecycle={"skippable": True}), ev("mandatory"))
    new_game(client)
    optional = client.post("/api/story/events/optional/start").json()["instance_id"]
    mandatory = client.post("/api/story/events/mandatory/start").json()["instance_id"]
    queue = story_pending(client)
    assert [(e["event_id"], e["status"]) for e in queue] == [("mandatory", "presented"), ("optional", "pending")]
    assert client.post("/api/turn/advance").status_code == 409          # the mandatory one blocks
    assert client.post(f"/api/story/instances/{optional}/choices/ok").status_code == 409   # not its turn
    assert client.post(f"/api/story/instances/{mandatory}/choices/ok").status_code == 200
    assert client.post("/api/turn/advance").status_code == 200          # only a skippable one remains
