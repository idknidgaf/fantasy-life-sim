"""Loading and cross-checking event definitions from JSON files.

Per-event validation lives in schema.py. This module adds the checks that need the whole set:
unique ids, references between events, and references to game content (traits).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError

from ..content import DATA_DIR, Content, get_content
from . import conditions as cond
from .errors import StoryContentError
from .schema import EventDefinition, EventFile

STORY_DIR = DATA_DIR / "story"


class StoryLibrary:
    """An immutable, validated set of event definitions."""

    def __init__(self, events: dict[str, EventDefinition]):
        self.events = events

    def get(self, event_id: str) -> EventDefinition | None:
        return self.events.get(event_id)

    def __len__(self) -> int:
        return len(self.events)


def build_library(raw_events: list[tuple[str, dict]], content: Content | None = None) -> StoryLibrary:
    """Validate raw event dicts, given as (source name, event). Raises StoryContentError listing every problem."""
    errors: list[str] = []
    events: dict[str, EventDefinition] = {}
    sources: dict[str, str] = {}
    for source, raw in raw_events:
        label = f"{source}: {raw.get('id', '?') if isinstance(raw, dict) else '?'}"
        try:
            ev = EventDefinition.model_validate(raw)
        except ValidationError as e:
            for err in e.errors():
                loc = ".".join(str(p) for p in err["loc"])
                errors.append(f"{label}: {loc}: {err['msg']}")
            continue
        if ev.id in events:
            errors.append(f"{label}: duplicate event id (also defined in {sources[ev.id]})")
            continue
        events[ev.id], sources[ev.id] = ev, source
    errors += _cross_check(events, sources, content)
    if errors:
        raise StoryContentError(errors)
    return StoryLibrary(events)


def _cross_check(events: dict[str, EventDefinition], sources: dict[str, str], content: Content | None) -> list[str]:
    errors: list[str] = []

    def check_event_ref(where: str, ref: str) -> None:
        if ref not in events:
            errors.append(f"{where}: refers to unknown event '{ref}'")

    def check_conditions(where: str, tree: dict | None) -> None:
        for leaf in cond.walk(tree):
            t = leaf["type"]
            if t in ("event_completed", "choice_was_made"):
                check_event_ref(where, leaf["event"])
                if t == "choice_was_made" and leaf["event"] in events and events[leaf["event"]].choice(leaf["choice"]) is None:
                    errors.append(f"{where}: event '{leaf['event']}' has no choice '{leaf['choice']}'")
            if t in ("trait_owned", "trait_absent") and content is not None and leaf["trait"] not in content.traits:
                errors.append(f"{where}: unknown trait '{leaf['trait']}'")

    for ev in events.values():
        base = f"{sources[ev.id]}: {ev.id}"
        check_conditions(f"{base} conditions", ev.conditions)
        for ch in ev.choices:
            where = f"{base} choice '{ch.id}'"
            check_conditions(f"{where} conditions", ch.conditions)
            if ch.next_event is not None:
                check_event_ref(f"{where} next_event", ch.next_event)
            for fx in ch.effects:
                if fx["type"] in ("start_event", "complete_event"):
                    check_event_ref(f"{where} effect {fx['type']}", fx["event"])
                if fx["type"] == "grant_trait" and content is not None and fx["trait"] not in content.traits:
                    errors.append(f"{where}: grant_trait refers to unknown trait '{fx['trait']}'")
    return errors


def load_library(directory: Path = STORY_DIR, content: Content | None = None) -> StoryLibrary:
    """Load every *.json file in a directory (an empty or missing directory is an empty library)."""
    raw_events: list[tuple[str, dict]] = []
    errors: list[str] = []
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        if path.name.endswith(".schema.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            errors.append(f"{path.name}: cannot read file: {e}")
            continue
        if not isinstance(data, dict) or not isinstance(data.get("events", []), list):
            errors.append(f"{path.name}: expected an object with an 'events' list")
            continue
        if data.get("schema_version", 1) != 1:
            errors.append(f"{path.name}: unsupported schema_version {data.get('schema_version')!r}")
            continue
        extra = set(data) - {"schema_version", "events"}
        if extra:
            errors.append(f"{path.name}: unknown top-level key(s): {sorted(extra)}")
        raw_events += [(path.name, e) for e in data.get("events", [])]
    try:
        library = build_library(raw_events, content)
    except StoryContentError as e:
        errors += e.errors
        library = None
    if errors:
        raise StoryContentError(errors)
    return library


@lru_cache
def get_story_library() -> StoryLibrary:
    return load_library(content=get_content())
