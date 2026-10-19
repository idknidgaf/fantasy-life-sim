from datetime import datetime, timezone

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
