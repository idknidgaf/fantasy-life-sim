"""The execution pipeline: lifecycle, triggers, resolution, idempotency, rollback, persistence."""
import pytest
from sqlalchemy import text
from sqlmodel import Session, create_engine, select

from app.models import StoryChoiceLog, StoryEventInstance, StoryFlag
from app.story import service, store
from app.story.errors import StoryEffectError, StoryInvalidChoice, StoryNotAllowed, StoryNotFound

from .story_helpers import ctx_for, ev, free_trait, library, make_life, memory_engine


@pytest.fixture
def env():
    with Session(memory_engine()) as session:
        yield session, make_life(session, age=10)


def flag(session, rec, key):
    ctx = ctx_for(session, rec)
    return store.get_flag(ctx, key)


def pending(session, rec, lib):
    return service.get_pending(session, rec, library=lib)


# ---------------------------------------------------------------- start and present

def test_start_present_and_view(env):
    session, rec = env
    lib = library(ev("a", choices=[
        {"id": "open", "text": "Open"},
        {"id": "locked", "text": "Locked", "locked_hint": "Too young",
         "conditions": {"type": "age_at_least", "value": 50}}]))
    view = service.start_event(session, rec, "a", library=lib)
    assert view["status"] == "pending" and view["event_version"] == 1
    shown = pending(session, rec, lib)
    assert [v["status"] for v in shown] == ["presented"]
    by_id = {c["id"]: c for c in shown[0]["choices"]}
    assert by_id["open"]["available"] and not by_id["locked"]["available"]
    assert by_id["locked"]["locked_hint"] == "Too young"
    assert pending(session, rec, lib)[0]["status"] == "presented"        # fetching again is harmless
    inst = session.get(StoryEventInstance, view["instance_id"])
    assert inst.presented_year == 10 and inst.presented_at is not None


def test_start_errors(env):
    session, rec = env
    lib = library(ev("a"), ev("auto", trigger={"type": "state"}),
                  ev("gated", conditions={"type": "age_at_least", "value": 99}))
    with pytest.raises(StoryNotFound):
        service.start_event(session, rec, "ghost", library=lib)
    with pytest.raises(StoryNotAllowed):                                  # not a manual event
        service.start_event(session, rec, "auto", library=lib)
    with pytest.raises(StoryNotAllowed):                                  # ineligible
        service.start_event(session, rec, "gated", library=lib)
    service.start_event(session, rec, "a", library=lib)
    with pytest.raises(StoryNotAllowed):                                  # already open
        service.start_event(session, rec, "a", library=lib)


# ---------------------------------------------------------------- resolution

def test_successful_resolution_applies_effects_logs_and_persists(env):
    session, rec = env
    t = free_trait(rec)
    lib = library(ev("a", choices=[{"id": "go", "text": "Go", "effects": [
        {"type": "set_flag", "flag": "seen", "value": True},
        {"type": "increment_flag", "flag": "n", "amount": 2},
        {"type": "grant_trait", "trait": t}]}]))
    iid = service.start_event(session, rec, "a", library=lib)["instance_id"]
    res = service.resolve_choice(session, rec, iid, "go", library=lib)
    assert res["replayed"] is False and len(res["effects"]) == 3 and res["next_instance_id"] is None
    session.refresh(rec)
    assert t in rec.traits and flag(session, rec, "seen") is True and flag(session, rec, "n") == 2
    inst = session.get(StoryEventInstance, iid)
    assert (inst.status, inst.selected_choice_id, inst.resolved_year, inst.end_reason) == ("resolved", "go", 10, "choice")
    assert inst.resolved_at is not None
    logs = session.exec(select(StoryChoiceLog)).all()
    assert [(l.event_id, l.choice_id, l.world_year, l.event_version) for l in logs] == [("a", "go", 10, 1)]


def test_choice_ids_are_validated_against_the_instance(env):
    session, rec = env
    lib = library(ev("a"), ev("b", choices=[{"id": "other", "text": "Other"}]))
    iid = service.start_event(session, rec, "a", library=lib)["instance_id"]
    with pytest.raises(StoryInvalidChoice):
        service.resolve_choice(session, rec, iid, "other", library=lib)       # belongs to a different event
    with pytest.raises(StoryInvalidChoice):
        service.resolve_choice(session, rec, iid, "nonsense", library=lib)
    with pytest.raises(StoryNotFound):
        service.resolve_choice(session, rec, 9999, "ok", library=lib)
    assert session.get(StoryEventInstance, iid).status == "pending"           # nothing changed


def test_locked_choice_cannot_be_submitted_and_changes_nothing(env):
    session, rec = env
    lib = library(ev("a", choices=[{"id": "x", "text": "X", "locked_hint": "Not yet",
                                    "conditions": {"type": "flag_at_least", "flag": "k", "value": 1},
                                    "effects": [{"type": "set_flag", "flag": "bad", "value": 1}]}]))
    iid = service.start_event(session, rec, "a", library=lib)["instance_id"]
    with pytest.raises(StoryNotAllowed, match="Not yet"):
        service.resolve_choice(session, rec, iid, "x", library=lib)
    assert flag(session, rec, "bad") is None and session.get(StoryEventInstance, iid).status == "pending"


def test_instance_of_another_life_is_not_visible(env):
    session, rec = env
    other = make_life(session, seed=2)
    lib = library(ev("a"))
    iid = service.start_event(session, rec, "a", library=lib)["instance_id"]
    with pytest.raises(StoryNotFound):
        service.resolve_choice(session, other, iid, "ok", library=lib)


# ---------------------------------------------------------------- idempotency

def test_retries_never_reapply_effects(env):
    session, rec = env
    lib = library(ev("a", choices=[{"id": "go", "text": "Go", "effects": [
        {"type": "increment_flag", "flag": "reward", "amount": 100}]}]))
    iid = service.start_event(session, rec, "a", library=lib)["instance_id"]
    first = service.resolve_choice(session, rec, iid, "go", library=lib)
    for _ in range(3):
        again = service.resolve_choice(session, rec, iid, "go", library=lib)
        assert again["replayed"] is True and again["effects"] == first["effects"]
    assert flag(session, rec, "reward") == 100
    assert len(session.exec(select(StoryChoiceLog)).all()) == 1
    with pytest.raises(StoryNotAllowed):                                    # a different choice is refused
        service.resolve_choice(session, rec, iid, "other", library=lib)


def test_resolved_one_time_event_cannot_be_restarted_or_rewarded(env):
    session, rec = env
    lib = library(ev("a", choices=[{"id": "go", "text": "Go", "effects": [
        {"type": "increment_flag", "flag": "reward"}]}]))
    iid = service.start_event(session, rec, "a", library=lib)["instance_id"]
    service.resolve_choice(session, rec, iid, "go", library=lib)
    with pytest.raises(StoryNotAllowed, match="already happened"):
        service.start_event(session, rec, "a", library=lib)
    assert service.scan_triggers(session, rec, "state", library=lib) == []
    assert flag(session, rec, "reward") == 1


def test_database_refuses_a_second_resolution_log_for_one_instance(env):
    session, rec = env
    lib = library(ev("a"))
    iid = service.start_event(session, rec, "a", library=lib)["instance_id"]
    service.resolve_choice(session, rec, iid, "ok", library=lib)
    session.add(StoryChoiceLog(instance_id=iid, campaign_id=rec.campaign_id, character_id=rec.id,
                               event_id="a", choice_id="ok"))
    with pytest.raises(Exception):
        session.commit()
    session.rollback()


def test_database_refuses_two_open_instances_of_one_event(env):
    session, rec = env
    for _ in range(2):
        session.add(StoryEventInstance(campaign_id=rec.campaign_id, character_id=rec.id, event_id="a"))
    with pytest.raises(Exception):
        session.commit()
    session.rollback()


# ---------------------------------------------------------------- rollback

def test_failed_resolution_rolls_back_everything(env):
    session, rec = env
    t = free_trait(rec)
    lib = library(ev("a", choices=[{"id": "go", "text": "Go", "effects": [
        {"type": "set_flag", "flag": "text", "value": "not a number"},
        {"type": "grant_trait", "trait": t},
        {"type": "set_flag", "flag": "early", "value": 1},
        {"type": "increment_flag", "flag": "text"}]}]))                      # fails: non-numeric flag
    iid = service.start_event(session, rec, "a", library=lib)["instance_id"]
    with pytest.raises(StoryEffectError):
        service.resolve_choice(session, rec, iid, "go", library=lib)
    session.refresh(rec)
    assert t not in rec.traits
    assert session.exec(select(StoryFlag)).all() == []
    assert session.exec(select(StoryChoiceLog)).all() == []
    inst = session.get(StoryEventInstance, iid)
    assert inst.status == "pending" and inst.selected_choice_id is None and inst.resolved_at is None
    # and the event can still be resolved properly afterwards
    ok_lib = library(ev("a", choices=[{"id": "go", "text": "Go"}]))
    assert service.resolve_choice(session, rec, iid, "go", library=ok_lib)["replayed"] is False


def test_failure_after_earlier_effects_rolls_back_even_for_unknown_event_reference(env):
    session, rec = env
    lib = library(ev("a", choices=[{"id": "go", "text": "Go", "effects": [{"type": "set_flag", "flag": "x", "value": 1}]}]))
    iid = service.start_event(session, rec, "a", library=lib)["instance_id"]
    # Content is validated at load time; simulate a definition corrupted afterwards.
    lib.events["a"].choices[0].effects.append({"type": "start_event", "event": "ghost"})
    with pytest.raises(StoryEffectError):
        service.resolve_choice(session, rec, iid, "go", library=lib)
    assert flag(session, rec, "x") is None and session.get(StoryEventInstance, iid).status == "pending"


def test_choice_log_is_append_only(env):
    session, rec = env
    lib = library(ev("a"))
    iid = service.start_event(session, rec, "a", library=lib)["instance_id"]
    service.resolve_choice(session, rec, iid, "ok", library=lib)
    row = session.exec(select(StoryChoiceLog)).one()
    row.choice_id = "tampered"
    with pytest.raises(RuntimeError, match="append-only"):
        session.commit()
    session.rollback()
    session.delete(session.exec(select(StoryChoiceLog)).one())
    with pytest.raises(RuntimeError, match="append-only"):
        session.commit()
    session.rollback()


# ---------------------------------------------------------------- branching

def test_branching_converging_and_terminating(env):
    session, rec = env
    lib = library(
        ev("start", choices=[{"id": "left", "text": "L", "next_event": "join",
                              "effects": [{"type": "set_flag", "flag": "path", "value": "left"}]},
                             {"id": "right", "text": "R", "next_event": "join",
                              "effects": [{"type": "set_flag", "flag": "path", "value": "right"}]},
                             {"id": "stop", "text": "S"}]),                     # terminates
        ev("join", event_type="node", choices=[{"id": "end", "text": "End"}]),
    )
    iid = service.start_event(session, rec, "start", library=lib)["instance_id"]
    res = service.resolve_choice(session, rec, iid, "right", library=lib)
    assert res["next_event"] == "join" and res["next_instance_id"] is not None
    shown = pending(session, rec, lib)
    assert [v["event_id"] for v in shown] == ["join"] and shown[0]["event_type"] == "node"
    child = session.get(StoryEventInstance, res["next_instance_id"])
    assert child.parent_instance_id == iid and child.origin == "next:start"
    end = service.resolve_choice(session, rec, child.id, "end", library=lib)
    assert end["next_event"] is None and pending(session, rec, lib) == []
    assert flag(session, rec, "path") == "right"


def test_next_event_blocked_by_lifecycle_ends_sequence_gracefully(env):
    session, rec = env
    lib = library(ev("a", choices=[{"id": "go", "text": "Go", "next_event": "gated"}]),
                  ev("gated", event_type="node", conditions={"type": "age_at_least", "value": 99}))
    iid = service.start_event(session, rec, "a", library=lib)["instance_id"]
    res = service.resolve_choice(session, rec, iid, "go", library=lib)
    assert res["next_instance_id"] is None and res["next_skipped_reason"] == "not eligible"


def test_choice_history_drives_later_branches(env):
    session, rec = env
    lib = library(
        ev("first", choices=[{"id": "brave", "text": "B", "next_event": "second"}, {"id": "meek", "text": "M", "next_event": "second"}]),
        ev("second", event_type="node", choices=[
            {"id": "praise", "text": "P", "conditions": {"type": "choice_was_made", "event": "first", "choice": "brave"}},
            {"id": "pity", "text": "Y", "conditions": {"not": {"type": "choice_was_made", "event": "first", "choice": "brave"}}}]))
    iid = service.start_event(session, rec, "first", library=lib)["instance_id"]
    service.resolve_choice(session, rec, iid, "meek", library=lib)
    avail = {c["id"]: c["available"] for c in pending(session, rec, lib)[0]["choices"]}
    assert avail == {"praise": False, "pity": True}


# ---------------------------------------------------------------- lifecycle policies

def test_repeatable_limit_and_cooldown(env):
    session, rec = env
    lib = library(ev("r", lifecycle={"mode": "repeatable", "max_occurrences": 2, "cooldown_years": 3}))

    def run():
        iid = service.start_event(session, rec, "r", library=lib)["instance_id"]
        service.resolve_choice(session, rec, iid, "ok", library=lib)

    run()
    with pytest.raises(StoryNotAllowed, match="cooling"):
        service.start_event(session, rec, "r", library=lib)
    rec.age += 3
    run()
    rec.age += 10
    with pytest.raises(StoryNotAllowed, match="limit"):
        service.start_event(session, rec, "r", library=lib)


def test_campaign_unique_spans_lives(env):
    session, rec = env
    from app.models import Campaign
    second = make_life(session, seed=3, campaign=session.get(Campaign, rec.campaign_id))
    lib = library(ev("c", lifecycle={"mode": "campaign_unique"}), ev("p"))
    iid = service.start_event(session, rec, "c", library=lib)["instance_id"]
    service.resolve_choice(session, rec, iid, "ok", library=lib)
    with pytest.raises(StoryNotAllowed, match="campaign"):
        service.start_event(session, second, "c", library=lib)
    other_campaign_life = make_life(session, seed=4)                       # a different campaign is unaffected
    service.start_event(session, other_campaign_life, "c", library=lib)
    # once_per_life is per life, not per campaign
    service.start_event(session, rec, "p", library=lib)
    service.start_event(session, second, "p", library=lib)


def test_expiry_by_timeout(env):
    session, rec = env
    lib = library(ev("t", lifecycle={"skippable": True, "expires_after_years": 2}))
    iid = service.start_event(session, rec, "t", library=lib)["instance_id"]
    rec.age += 1
    assert len(pending(session, rec, lib)) == 1
    rec.age += 1
    assert pending(session, rec, lib) == []
    inst = session.get(StoryEventInstance, iid)
    assert (inst.status, inst.end_reason, inst.resolved_year) == ("expired", "timeout", 12)
    with pytest.raises(StoryNotAllowed, match="expired"):
        service.resolve_choice(session, rec, iid, "ok", library=lib)
    with pytest.raises(StoryNotAllowed):                                    # the chance is gone for good
        service.start_event(session, rec, "t", library=lib)


# ---------------------------------------------------------------- versions and triggers

def test_event_version_tracking_and_save_compatibility(env):
    session, rec = env
    v1 = library(ev("a", version=1), ev("b", version=1))
    a = service.start_event(session, rec, "a", library=v1)["instance_id"]
    b = service.start_event(session, rec, "b", library=v1)["instance_id"]
    assert session.get(StoryEventInstance, a).event_version == 1
    v2 = library(ev("a", version=2, compatible_versions=[1]), ev("b", version=2))
    assert service.resolve_choice(session, rec, a, "ok", library=v2)["replayed"] is False   # declared compatible
    assert session.get(StoryChoiceLog, 1).event_version == 1
    assert pending(session, rec, v2) == []                                  # incompatible one was retired
    inst_b = session.get(StoryEventInstance, b)
    assert (inst_b.status, inst_b.end_reason) == ("expired", "version_changed")
    started = service.start_event(session, rec, "b", library=v2)            # not penalised: can be offered again
    assert started["event_version"] == 2


def test_removed_definition_retires_open_instance(env):
    session, rec = env
    iid = service.start_event(session, rec, "a", library=library(ev("a")))["instance_id"]
    assert pending(session, rec, library(ev("other"))) == []
    assert session.get(StoryEventInstance, iid).status == "expired"


def test_trigger_scans(env):
    session, rec = env
    lib = library(
        ev("by_age", trigger={"type": "age", "min_age": 8, "max_age": 12}),
        ev("too_old", trigger={"type": "age", "min_age": 20}),
        ev("by_action", trigger={"type": "action", "action": "work"}),
        ev("by_state", trigger={"type": "state"}, conditions={"type": "flag_at_least", "flag": "k", "value": 1}),
        ev("by_hand"),
    )
    assert [v["event_id"] for v in service.scan_triggers(session, rec, "age", library=lib)] == ["by_age"]
    assert service.scan_triggers(session, rec, "age", library=lib) == []              # already open
    assert service.scan_triggers(session, rec, "action", {"action": "rest"}, library=lib) == []
    assert [v["event_id"] for v in service.scan_triggers(session, rec, "action", {"action": "work"}, library=lib)] == ["by_action"]
    assert service.scan_triggers(session, rec, "state", library=lib) == []            # conditions unmet
    store.set_flag(ctx_for(session, rec, lib), "k", 1)
    assert [v["event_id"] for v in service.scan_triggers(session, rec, "state", library=lib)] == ["by_state"]
    assert service.scan_triggers(session, rec, "manual", library=lib) == []           # manual never auto-fires
    origins = {i.event_id: i.origin for i in session.exec(select(StoryEventInstance))}
    assert origins["by_age"] == "trigger:age"


# ---------------------------------------------------------------- persistence across save/load

def test_story_state_survives_restart(tmp_path):
    url = f"sqlite:///{tmp_path / 'save.db'}"
    lib = library(
        ev("a", choices=[{"id": "go", "text": "Go", "next_event": "b",
                          "effects": [{"type": "increment_flag", "flag": "reward", "amount": 5}]}]),
        ev("b", event_type="node"))
    eng = create_engine(url)
    from sqlmodel import SQLModel
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        rec = make_life(s, age=7)
        rec_id = rec.id
        iid = service.start_event(s, rec, "a", library=lib)["instance_id"]
        service.resolve_choice(s, rec, iid, "go", library=lib)
    eng.dispose()

    eng2 = create_engine(url)                                              # "application restart"
    with Session(eng2) as s:
        from app.models import CharacterRecord
        rec = s.get(CharacterRecord, rec_id)
        assert flag(s, rec, "reward") == 5
        state = service.get_state(s, rec, library=lib)
        assert state["flags"] == {"reward": 5}
        assert {(i["event_id"], i["status"]) for i in state["instances"]} == {("a", "resolved"), ("b", "pending")}
        assert [v["event_id"] for v in pending(s, rec, lib)] == ["b"]      # the follow-up is still waiting
        with pytest.raises(StoryNotAllowed):
            service.start_event(s, rec, "a", library=lib)                   # reload cannot re-offer it
        assert service.resolve_choice(s, rec, iid, "go", library=lib)["replayed"] is True
        assert flag(s, rec, "reward") == 5
        assert s.exec(text("select count(*) from storychoicelog")).one()[0] == 1


def test_new_event_is_just_a_json_file(env, tmp_path):
    """Criterion: authoring = dropping a valid JSON file in; no per-story handler code."""
    import json
    from app.story.library import load_library
    session, rec = env
    (tmp_path / "anything.json").write_text(json.dumps({"schema_version": 1, "events": [
        ev("door", trigger={"type": "age", "min_age": 5},
           choices=[{"id": "knock", "text": "Knock", "next_event": "inside",
                     "effects": [{"type": "increment_flag", "flag": "knocks"}]}]),
        ev("inside", event_type="node", conditions={"type": "flag_at_least", "flag": "knocks", "value": 1}),
    ]}))
    lib = load_library(tmp_path)
    [view] = service.scan_triggers(session, rec, "age", library=lib)
    res = service.resolve_choice(session, rec, view["instance_id"], "knock", library=lib)
    assert res["next_instance_id"] is not None and flag(session, rec, "knocks") == 1
