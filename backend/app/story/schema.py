"""The event definition schema. Events are data: validated here, executed by service.py.

A content file is {"schema_version": 1, "events": [ <EventDefinition>, ... ]}.
See docs/STORY_ENGINE.md for the full reference.
"""
from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from .conditions import parse_condition
from .effects import parse_effect
from .triggers import parse_trigger

SCHEMA_VERSION = 1
ID_PATTERN = r"^[a-z0-9][a-z0-9_.\-]{0,63}$"
_ID_RE = re.compile(ID_PATTERN)

Id = Annotated[str, Field(pattern=ID_PATTERN)]
Condition = Annotated[dict, AfterValidator(parse_condition)]
Effect = Annotated[dict, AfterValidator(parse_effect)]
Trigger = Annotated[dict, AfterValidator(parse_trigger)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Lifecycle(Strict):
    """How often an event may occur.

    once_per_life    at most one occurrence per character life (default)
    repeatable       up to max_occurrences per life, at least cooldown_years apart
    campaign_unique  at most one occurrence in the whole campaign, across lives
    An open (pending/presented) instance blocks another of the same event in every mode.

    An event is an encounter: once triggered its window stays up, and the player cannot Age Up or act,
    until it is resolved. The one explicit exception is `skippable: true`: the player may Age Up past
    it, and it is withdrawn once `expires_after_years` have passed (it stays until resolved if that is
    omitted). Skippable events queue behind blocking ones.
    """
    mode: Literal["once_per_life", "repeatable", "campaign_unique"] = "once_per_life"
    max_occurrences: int | None = Field(default=None, ge=1)
    cooldown_years: int = Field(default=0, ge=0)
    skippable: bool = False
    expires_after_years: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _rules(self):
        if self.expires_after_years is not None and not self.skippable:
            raise ValueError("expires_after_years needs skippable: true "
                             "(a blocking event stops time, so it could never expire)")
        if self.mode == "repeatable" and self.max_occurrences is None:
            raise ValueError("a repeatable event needs max_occurrences")
        if self.mode != "repeatable" and (self.max_occurrences is not None or self.cooldown_years):
            raise ValueError("max_occurrences and cooldown_years only apply to mode 'repeatable'")
        return self


class Choice(Strict):
    id: Id
    text: str = Field(min_length=1)
    conditions: Condition | None = None
    locked_hint: str | None = None            # shown to the player when conditions are not met
    result: str | None = None                 # outcome text recorded in the history log
    effects: list[Effect] = Field(default_factory=list)
    next_event: Id | None = None              # null ends the sequence


class EventDefinition(Strict):
    id: Id
    version: int = Field(default=1, ge=1)
    event_type: Literal["event", "node"] = "event"      # a node is a sequence step reached only from other events
    title: str = Field(min_length=1)
    narrative: str = Field(min_length=1)
    trigger: Trigger = Field(default_factory=lambda: {"type": "manual"})
    conditions: Condition | None = None                 # eligibility
    lifecycle: Lifecycle = Field(default_factory=Lifecycle)
    choices: list[Choice] = Field(min_length=1)
    # Older versions whose in-flight instances this version can still resolve (choice ids unchanged).
    compatible_versions: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _rules(self):
        ids = [c.id for c in self.choices]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate choice id(s): {', '.join(dupes)}")
        if self.event_type == "node" and self.trigger["type"] != "manual":
            raise ValueError("a node event is only reached from other events, so its trigger must be 'manual'")
        if any(v >= self.version or v < 1 for v in self.compatible_versions):
            raise ValueError("compatible_versions must be earlier versions (>= 1 and < version)")
        if any(c.next_event == self.id for c in self.choices) and self.lifecycle.mode != "repeatable":
            raise ValueError("a choice may point back at its own event only if the event is 'repeatable'")
        return self

    def choice(self, choice_id: str) -> Choice | None:
        return next((c for c in self.choices if c.id == choice_id), None)


class EventFile(Strict):
    schema_version: Literal[1] = SCHEMA_VERSION
    events: list[EventDefinition] = Field(default_factory=list)
