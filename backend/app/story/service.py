"""The execution pipeline: eligibility -> instance -> present -> validate choice -> effects -> persist.

Public functions take a Session and the character's CharacterRecord, own their transaction
(commit on success, rollback on any error) and return plain dicts. Helpers prefixed `ctx`-style
(try_start, complete_without_choice) run inside a caller's transaction and never commit.

"World year" is the character's age.

Presentation policy (one event at a time):
  * A pending legacy event (CharacterRecord.pending_event) always goes first.
  * Story events queue persistently as pending instances: blocking events oldest first, then
    skippable ones. Only the head of the queue is ever presented, and only when no legacy event is pending.
  * Any open legacy event or open blocking story event blocks actions and the turn (enforced in main.py).
    Skippable story events (lifecycle.skippable) do not.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import update
from sqlmodel import Session

from .. import named
from ..content import Content, get_content
from ..models import OPEN_STATUSES, CharacterRecord, StoryChoiceLog, StoryEventInstance
from . import store
from .conditions import evaluate
from .context import StoryContext
from .effects import apply_effect
from .errors import StoryEffectError, StoryInvalidChoice, StoryNotAllowed, StoryNotFound
from .library import StoryLibrary, get_story_library
from .schema import EventDefinition
from .triggers import triggers


def _now() -> datetime:
    return datetime.now(timezone.utc)


def make_context(session: Session, record: CharacterRecord, library: StoryLibrary | None = None,
                 content: Content | None = None) -> StoryContext:
    return StoryContext(session=session, record=record, library=library or get_story_library(),
                        content=content or get_content())


def _transaction(ctx: StoryContext, fn):
    """Run fn() and commit; on any error roll back everything it did and re-raise."""
    try:
        result = fn()
        ctx.session.commit()
        return result
    except Exception:
        ctx.session.rollback()
        raise


# ---------------------------------------------------------------- eligibility

def startable_reason(ctx: StoryContext, d: EventDefinition) -> str | None:
    """None if a new instance of the event may be created now, else the reason it may not."""
    if store.open_instance(ctx, d.id):
        return "already open"
    lc = d.lifecycle
    if lc.mode == "once_per_life" and store.occurrences(ctx, d.id) >= 1:
        return "already happened in this life"
    if lc.mode == "campaign_unique" and store.occurrences(ctx, d.id, whole_campaign=True) >= 1:
        return "already happened in this campaign"
    if lc.mode == "repeatable":
        if store.occurrences(ctx, d.id) >= lc.max_occurrences:
            return "occurrence limit reached"
        last = store.last_occurrence_year(ctx, d.id)
        if last is not None and ctx.world_year - last < lc.cooldown_years:
            return "still cooling down"
    if not evaluate(d.conditions, ctx):
        return "not eligible"
    return None


def try_start(ctx: StoryContext, event_id: str, *, origin: str,
              parent_instance_id: int | None = None) -> tuple[StoryEventInstance | None, str | None]:
    """Create a pending instance if allowed. Returns (instance, None) or (None, reason). No commit."""
    d = ctx.library.get(event_id)
    if d is None:
        raise StoryEffectError(f"unknown event '{event_id}'")
    reason = startable_reason(ctx, d)
    if reason:
        return None, reason
    return store.create_instance(ctx, d, origin=origin, parent_instance_id=parent_instance_id), None


def complete_without_choice(ctx: StoryContext, event_id: str) -> bool:
    """Backs the complete_event effect. Returns whether anything changed. No commit."""
    d = ctx.library.get(event_id)
    if d is None:
        raise StoryEffectError(f"unknown event '{event_id}'")
    open_inst = store.open_instance(ctx, event_id)
    if open_inst is not None:
        open_inst.status, open_inst.end_reason = "resolved", "effect"
        open_inst.resolved_year, open_inst.resolved_at = ctx.world_year, _now()
        ctx.session.add(open_inst)
        ctx.session.flush()
        return True
    if store.event_completed(ctx, event_id):
        return False
    inst = store.create_instance(ctx, d, origin="effect", parent_instance_id=ctx.current_instance_id)
    inst.status, inst.end_reason = "resolved", "effect"
    inst.resolved_year, inst.resolved_at = ctx.world_year, _now()
    ctx.session.add(inst)
    ctx.session.flush()
    return True


# ---------------------------------------------------------------- the queue

def _is_skippable(ctx: StoryContext, inst: StoryEventInstance) -> bool:
    d = ctx.library.get(inst.event_id)
    return bool(d and d.lifecycle.skippable)


def ordered_open(ctx: StoryContext) -> list[StoryEventInstance]:
    """The queue: blocking events first, then skippable ones, each oldest first. Index 0 is the head."""
    return sorted(store.list_open(ctx), key=lambda i: (_is_skippable(ctx, i), i.id))


# ---------------------------------------------------------------- views

def _npc_view(ctx: StoryContext, definition_id: str) -> dict:
    """The named character an encounter is with: authored title and description, and their runtime name and status."""
    d = named.definitions().get(definition_id)
    npc = named.get_instance(ctx.session, ctx.campaign_id, definition_id)
    return {"id": definition_id, "name": npc.name if npc else (d.name if d else definition_id),
            "title": d.title if d else None, "description": d.description if d else "",
            "status": npc.status.value if npc else None}


def instance_view(ctx: StoryContext, inst: StoryEventInstance) -> dict:
    d = ctx.library.get(inst.event_id)
    choices = []
    if d is not None:
        for ch in d.choices:
            ok = evaluate(ch.conditions, ctx)
            choices.append({"id": ch.id, "text": ch.text, "available": ok,
                            "locked_hint": None if ok else ch.locked_hint})
    return {
        "instance_id": inst.id, "event_id": inst.event_id, "event_version": inst.event_version,
        "event_type": d.event_type if d else None, "skippable": bool(d and d.lifecycle.skippable),
        "title": d.title if d else inst.event_id,
        "narrative": d.narrative if d else "", "status": inst.status,
        "created_year": inst.created_year, "expires_year": inst.expires_year,
        "selected_choice_id": inst.selected_choice_id, "choices": choices,
        "npc": _npc_view(ctx, d.npc) if d and d.npc else None,
    }


# ---------------------------------------------------------------- lifecycle housekeeping

def expire_due(ctx: StoryContext) -> int:
    """Expire open instances that timed out, or whose definition was removed or changed incompatibly."""
    n = 0
    for inst in store.list_open(ctx):
        d = ctx.library.get(inst.event_id)
        reason = None
        if d is None or (inst.event_version != d.version and inst.event_version not in d.compatible_versions):
            reason = "version_changed"
        elif inst.expires_year is not None and ctx.world_year >= inst.expires_year:
            reason = "timeout"
        if reason:
            inst.status, inst.end_reason = "expired", reason
            inst.resolved_year, inst.resolved_at = ctx.world_year, _now()
            ctx.session.add(inst)
            n += 1
    ctx.session.flush()
    return n


# ---------------------------------------------------------------- public pipeline

def start_event(session: Session, record: CharacterRecord, event_id: str, *,
                library: StoryLibrary | None = None, content: Content | None = None) -> dict:
    """Explicitly start a 'manual' event (steps 1-3 of the pipeline)."""
    ctx = make_context(session, record, library, content)
    d = ctx.library.get(event_id)
    if d is None:
        raise StoryNotFound(f"Unknown event: {event_id}")
    if d.trigger["type"] != "manual":
        raise StoryNotAllowed(f"'{event_id}' is started by its '{d.trigger['type']}' trigger, not directly")

    def run():
        expire_due(ctx)
        inst, reason = try_start(ctx, event_id, origin="manual")
        if inst is None:
            raise StoryNotAllowed(f"Cannot start '{event_id}': {reason}")
        return instance_view(ctx, inst)
    return _transaction(ctx, run)


def scan_in_context(ctx: StoryContext, channel: str, payload: dict | None = None) -> list[dict]:
    """Offer every event whose trigger type is `channel`, matches `payload`, and is eligible. No commit."""
    payload = payload or {}
    started = []
    for d in sorted(ctx.library.events.values(), key=lambda e: e.id):
        if d.trigger["type"] != channel or channel == "manual":
            continue
        spec, params = triggers.parse(d.trigger)
        if not spec.handler(params, ctx, payload):
            continue
        inst, _ = try_start(ctx, d.id, origin=f"trigger:{channel}")
        if inst is not None:
            started.append(instance_view(ctx, inst))
    return started


def run_scans(ctx: StoryContext, scans: list[tuple[str, dict]]) -> list[dict]:
    """Settle expiries, then run several trigger scans in order. No commit: the gameplay hooks in
    main.py call this inside the same transaction that saves the character, so both succeed or neither."""
    expire_due(ctx)
    started: list[dict] = []
    for channel, payload in scans:
        started += scan_in_context(ctx, channel, payload)
    return started


def scan_triggers(session: Session, record: CharacterRecord, channel: str, payload: dict | None = None, *,
                  library: StoryLibrary | None = None, content: Content | None = None) -> list[dict]:
    """Standalone, self-committing scan of one channel."""
    ctx = make_context(session, record, library, content)
    return _transaction(ctx, lambda: run_scans(ctx, [(channel, payload or {})]))


def has_open_events(session: Session, record: CharacterRecord, *,
                    library: StoryLibrary | None = None, content: Content | None = None) -> bool:
    """True if a *blocking* story event is waiting for a decision (skippable ones never block).
    Timed-out events are expired first (and saved)."""
    ctx = make_context(session, record, library, content)
    _transaction(ctx, lambda: expire_due(ctx))
    return any(not _is_skippable(ctx, i) for i in store.list_open(ctx))


def legacy_event_pending(ctx: StoryContext) -> bool:
    pending = ctx.record.pending_event
    return bool(pending) and pending in ctx.content.events


def get_pending(session: Session, record: CharacterRecord, *,
                library: StoryLibrary | None = None, content: Content | None = None) -> list[dict]:
    """The open story events of this life, oldest first (the queue).

    Only the head of the queue is presented (pending -> presented, idempotent), and only while no legacy
    event is pending; every other event stays 'pending'. The frontend shows the one that is 'presented'.
    """
    ctx = make_context(session, record, library, content)

    def run():
        expire_due(ctx)
        open_events = ordered_open(ctx)
        if open_events and not legacy_event_pending(ctx):
            store.mark_presented(ctx, open_events[0])
        return [instance_view(ctx, i) for i in open_events]
    return _transaction(ctx, run)


def get_state(session: Session, record: CharacterRecord, *,
              library: StoryLibrary | None = None, content: Content | None = None) -> dict:
    ctx = make_context(session, record, library, content)
    return {
        "flags": store.all_flags(ctx),
        "instances": [{"instance_id": i.id, "event_id": i.event_id, "event_version": i.event_version,
                       "status": i.status, "selected_choice_id": i.selected_choice_id,
                       "created_year": i.created_year, "resolved_year": i.resolved_year}
                      for i in store.list_instances(ctx)],
    }


def resolve_choice(session: Session, record: CharacterRecord, instance_id: int, choice_id: str, *,
                   library: StoryLibrary | None = None, content: Content | None = None) -> dict:
    """Validate a choice, apply its effects, log it and advance, all in one transaction.

    Idempotent: repeating the choice that already resolved an instance returns the stored result
    ('replayed': True) and changes nothing. Any failure rolls the whole resolution back.
    """
    ctx = make_context(session, record, library, content)
    _transaction(ctx, lambda: expire_due(ctx))          # settle timeouts first, durably
    return _transaction(ctx, lambda: _resolve(ctx, instance_id, choice_id))


def _log_history(ctx: StoryContext, title: str, text: str, event_id: str) -> None:
    """Append to the character's history log (kind 'story'; event_id is namespaced so it can never be
    mistaken for a legacy event id by the legacy 'once' check)."""
    ctx.record.history = [*(ctx.record.history or []), {
        "age": ctx.world_year, "kind": "story", "text": f"{title}: {text}", "event_id": f"story:{event_id}"}]
    ctx.session.add(ctx.record)


def _resolve(ctx: StoryContext, instance_id: int, choice_id: str) -> dict:
    session = ctx.session
    inst = session.get(StoryEventInstance, instance_id)
    if inst is None or inst.character_id != ctx.character_id:
        raise StoryNotFound(f"No such event instance: {instance_id}")

    if inst.status == "resolved":
        if inst.selected_choice_id == choice_id:
            return {**inst.outcome, "instance_id": inst.id, "event_id": inst.event_id,
                    "choice_id": choice_id, "replayed": True}
        raise StoryNotAllowed("This event was already resolved")
    if inst.status == "expired":
        raise StoryNotAllowed("This event has expired")

    d = ctx.library.get(inst.event_id)
    if d is None or (inst.event_version != d.version and inst.event_version not in d.compatible_versions):
        raise StoryNotAllowed("This event's content has changed and it can no longer be resolved")
    if legacy_event_pending(ctx):
        raise StoryNotAllowed("Another event needs your decision first")
    if ordered_open(ctx)[0].id != inst.id:
        raise StoryNotAllowed("Another event needs your decision first")
    choice = d.choice(choice_id)
    if choice is None:
        raise StoryInvalidChoice(f"Unknown choice '{choice_id}' for event '{d.id}'")
    if not evaluate(choice.conditions, ctx):
        raise StoryNotAllowed(choice.locked_hint or "That choice is not available")

    # Claim the instance atomically. Of two concurrent requests only one can flip open -> resolved.
    claimed = session.execute(
        update(StoryEventInstance)
        .where(StoryEventInstance.id == inst.id, StoryEventInstance.status.in_(OPEN_STATUSES))
        .values(status="resolved", end_reason="choice", selected_choice_id=choice_id,
                resolved_year=ctx.world_year, resolved_at=_now())
    )
    if claimed.rowcount != 1:
        raise StoryNotAllowed("This event was already resolved")
    session.refresh(inst)

    ctx.current_event_id, ctx.current_instance_id = d.id, inst.id
    for effect in choice.effects:
        apply_effect(effect, ctx)
    effect_results = list(ctx.effect_log)

    session.add(StoryChoiceLog(
        instance_id=inst.id, campaign_id=ctx.campaign_id, character_id=ctx.character_id,
        event_id=d.id, event_version=inst.event_version, choice_id=choice_id,
        world_year=ctx.world_year, effects=effect_results,
    ))

    _log_history(ctx, d.title, choice.result or choice.text, d.id)

    next_instance_id, skipped = None, None
    if choice.next_event:
        nxt, skipped = try_start(ctx, choice.next_event, origin=f"next:{d.id}", parent_instance_id=inst.id)
        next_instance_id = nxt.id if nxt else None
    # State may have changed: offer follow-ups right now, so the next encounter appears without waiting
    # for the player's next action.
    triggered = [v["event_id"] for v in scan_in_context(ctx, "state", {})]
    outcome = {"effects": effect_results, "next_event": choice.next_event,
               "next_instance_id": next_instance_id, "next_skipped_reason": skipped,
               "triggered_events": triggered}
    inst.outcome = outcome
    session.add(inst)
    session.flush()
    return {**outcome, "instance_id": inst.id, "event_id": d.id, "choice_id": choice_id, "replayed": False}
