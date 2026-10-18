from datetime import datetime, timezone

from sqlmodel import JSON, Column, Field, SQLModel

from .engine import Character


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Campaign(SQLModel, table=True):
    """A saved game. Exactly one campaign is 'active' at a time."""
    id: int | None = Field(default=None, primary_key=True)
    name: str
    seed: int
    active: bool = True
    created_at: datetime = Field(default_factory=_now)


class CharacterRecord(SQLModel, table=True):
    """The character living inside a campaign."""
    id: int | None = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    name: str
    age: int = 0
    alive: bool = True
    stats: dict = Field(default_factory=dict, sa_column=Column(JSON))
    skills: dict = Field(default_factory=dict, sa_column=Column(JSON))
    history: list = Field(default_factory=list, sa_column=Column(JSON))
    cause_of_death: str | None = None
    action_taken: str | None = None
    pending_event: str | None = None

    @classmethod
    def from_character(cls, campaign_id: int, c: Character) -> "CharacterRecord":
        rec = cls(campaign_id=campaign_id, name=c.name)
        rec.update_from(c)
        return rec

    def to_character(self) -> Character:
        return Character(
            name=self.name, age=self.age, alive=self.alive,
            stats=dict(self.stats), skills=dict(self.skills), history=list(self.history),
            cause_of_death=self.cause_of_death, action_taken=self.action_taken,
            pending_event=self.pending_event,
        )

    def update_from(self, c: Character) -> None:
        # Assign fresh objects so SQLAlchemy notices the JSON changed.
        self.age = c.age
        self.alive = c.alive
        self.stats = dict(c.stats)
        self.skills = dict(c.skills)
        self.history = list(c.history)
        self.cause_of_death = c.cause_of_death
        self.action_taken = c.action_taken
        self.pending_event = c.pending_event
