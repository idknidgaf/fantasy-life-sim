"""Everything a condition, effect or trigger may look at or change, for one character life."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlmodel import Session

from ..content import Content
from ..models import CharacterRecord

if TYPE_CHECKING:
    from .library import StoryLibrary


@dataclass
class StoryContext:
    session: Session
    record: CharacterRecord
    library: "StoryLibrary"
    content: Content
    # Filled in by effects while a choice resolves; stored with the choice log.
    effect_log: list[dict] = field(default_factory=list)
    # The event instance being resolved, so effects can record lineage.
    current_event_id: str | None = None
    current_instance_id: int | None = None

    @property
    def character_id(self) -> int:
        return self.record.id

    @property
    def campaign_id(self) -> int:
        return self.record.campaign_id

    @property
    def world_year(self) -> int:
        """The game's only clock is the character's age."""
        return self.record.age
