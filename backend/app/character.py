from pydantic import BaseModel, Field


class Character(BaseModel):
    """One individual. The rules for its kind live in a SpeciesDef; this is its own state."""
    name: str
    species_id: str = "human"
    age: int = 0
    alive: bool = True
    stats: dict[str, int] = Field(default_factory=dict)        # base stats (species bonus baked in at birth)
    skills: dict[str, float] = Field(default_factory=dict)     # skill_id -> level
    axes: dict[str, int] = Field(default_factory=dict)         # personality + alignment, -100..100
    traits: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)             # story facts ("guild_member")
    counters: dict[str, int] = Field(default_factory=dict)     # tallies ("selfless_acts")
    items: dict[str, int] = Field(default_factory=dict)        # item_id -> count
    location: str = "village"
    history: list[dict] = Field(default_factory=list)          # {age, kind, text, event_id}
    cause_of_death: str | None = None
    action_taken: str | None = None        # action performed this year, if any
    pending_event: str | None = None       # event id waiting for a decision
    queued_events: list[str] = Field(default_factory=list)
    # Hidden talent. Rolled ONCE at birth and then only ever read, never rerolled.
    hidden_talent: str | None = None
    talent_state: str = "none"             # none | dormant | awakened
