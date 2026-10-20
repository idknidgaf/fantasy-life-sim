from datetime import datetime, timezone

from sqlalchemy import Index, UniqueConstraint, event, text
from sqlmodel import JSON, Column, Field, SQLModel

from .character import Character


def _now() -> datetime:
    return datetime.now(timezone.utc)


JSON_FIELDS = ("stats", "skills", "axes", "traits", "flags", "counters", "items", "history",
               "queued_events", "actions_taken")
DICT_FIELDS = ("stats", "skills", "axes", "counters", "items")
PLAIN_FIELDS = ("name", "species_id", "age", "alive", "location", "cause_of_death",
                "health", "stress", "happiness",
                "pending_event", "hidden_talent", "talent_state")


class Campaign(SQLModel, table=True):
    """A saved game. Exactly one campaign is 'active' at a time."""
    id: int | None = Field(default=None, primary_key=True)
    name: str
    seed: int
    active: bool = True
    created_at: datetime = Field(default_factory=_now)


class CharacterRecord(SQLModel, table=True):
    """The character living inside a campaign.

    hidden_talent / talent_state are written once at creation and afterwards only change when
    the talent awakens. Loading a save never rolls anything.
    """
    id: int | None = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    name: str
    species_id: str = "human"
    age: int = 0
    alive: bool = True
    location: str = "village"
    stats: dict = Field(default_factory=dict, sa_column=Column(JSON))
    skills: dict = Field(default_factory=dict, sa_column=Column(JSON))
    axes: dict = Field(default_factory=dict, sa_column=Column(JSON))
    traits: list = Field(default_factory=list, sa_column=Column(JSON))
    flags: list = Field(default_factory=list, sa_column=Column(JSON))
    counters: dict = Field(default_factory=dict, sa_column=Column(JSON))
    items: dict = Field(default_factory=dict, sa_column=Column(JSON))
    history: list = Field(default_factory=list, sa_column=Column(JSON))
    queued_events: list = Field(default_factory=list, sa_column=Column(JSON))
    actions_taken: list = Field(default_factory=list, sa_column=Column(JSON))
    cause_of_death: str | None = None
    health: int | None = None
    stress: float = 0.0
    happiness: float = 50.0
    pending_event: str | None = None
    hidden_talent: str | None = None
    talent_state: str = "none"

    @classmethod
    def from_character(cls, campaign_id: int, c: Character) -> "CharacterRecord":
        rec = cls(campaign_id=campaign_id, name=c.name)
        rec.update_from(c)
        return rec

    def to_character(self) -> Character:
        data = {f: getattr(self, f) for f in PLAIN_FIELDS}
        for f in JSON_FIELDS:
            value = getattr(self, f)
            data[f] = value.copy() if value is not None else ({} if f in DICT_FIELDS else [])
        return Character(**data)

    def update_from(self, c: Character) -> None:
        for f in PLAIN_FIELDS:
            setattr(self, f, getattr(c, f))
        for f in JSON_FIELDS:                       # fresh objects, so SQLAlchemy sees the change
            value = getattr(c, f)
            setattr(self, f, value.copy())


# ---------------------------------------------------------------- story engine
# Generic, data-driven story state. Event *definitions* live in JSON content files (app/data/story),
# never in the database; these tables hold only what happened to one character life. The "world
# year" recorded on every row is the character's age, which is the game's only clock.

OPEN_STATUSES = ("pending", "presented")


class StoryEventInstance(SQLModel, table=True):
    """One occurrence of an event definition in one life.

    status: pending -> presented -> resolved, or pending/presented -> expired.
    event_version is the definition version the instance was created from (save compatibility).
    """
    __table_args__ = (
        # At most one open instance of an event per life: a backstop against duplicate creation.
        Index("uq_story_open_instance", "character_id", "event_id", unique=True,
              sqlite_where=text("status IN ('pending','presented')"),
              postgresql_where=text("status IN ('pending','presented')")),
    )
    id: int | None = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    character_id: int = Field(foreign_key="characterrecord.id", index=True)
    event_id: str = Field(index=True)
    event_version: int = 1
    status: str = Field(default="pending", index=True)
    origin: str = "manual"                       # manual | trigger:<type> | start_event:<id> | next:<id>
    parent_instance_id: int | None = Field(default=None, foreign_key="storyeventinstance.id")
    created_year: int = 0
    presented_year: int | None = None
    resolved_year: int | None = None
    expires_year: int | None = None
    end_reason: str | None = None                # expired: timeout | version_changed; resolved: choice | effect
    selected_choice_id: str | None = None
    outcome: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)
    presented_at: datetime | None = None
    resolved_at: datetime | None = None


class StoryChoiceLog(SQLModel, table=True):
    """Append-only record of every choice ever made. Rows are never updated or deleted by the engine."""
    __table_args__ = (UniqueConstraint("instance_id", name="uq_story_choice_instance"),)
    id: int | None = Field(default=None, primary_key=True)
    instance_id: int = Field(foreign_key="storyeventinstance.id", index=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    character_id: int = Field(foreign_key="characterrecord.id", index=True)
    event_id: str = Field(index=True)
    event_version: int = 1
    choice_id: str
    world_year: int = 0
    effects: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)


class StoryFlag(SQLModel, table=True):
    """A persistent story flag (bool / number / string) belonging to one character life."""
    __table_args__ = (UniqueConstraint("character_id", "key", name="uq_story_flag"),)
    id: int | None = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    character_id: int = Field(foreign_key="characterrecord.id", index=True)
    key: str
    value: bool | int | float | str | None = Field(default=None, sa_column=Column(JSON))
    updated_year: int = 0


@event.listens_for(StoryChoiceLog, "before_update")
@event.listens_for(StoryChoiceLog, "before_delete")
def _choice_log_is_append_only(mapper, connection, target):
    raise RuntimeError("StoryChoiceLog is append-only")
