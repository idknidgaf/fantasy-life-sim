"""Shapes of the JSON the API sends and receives."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .battle import BattleSummary, CombatPreview


class CampaignCreate(BaseModel):
    character_name: str = Field(min_length=1, max_length=40)
    species: str = "human"
    campaign_name: str | None = Field(default=None, max_length=60)
    seed: int | None = None


class OptionRead(BaseModel):
    id: str
    label: str
    available: bool = True
    requirement: str | None = None        # why it is locked, if it is
    kind: Literal["normal", "fight", "flee"] = "normal"
    combat: CombatPreview | None = None   # the resolver's pre-battle preview, on Fight and Flee options


class PendingEventRead(BaseModel):
    id: str
    title: str
    text: str
    choices: list[OptionRead]


class TraitRead(BaseModel):
    id: str
    name: str
    description: str
    tier: str
    effects: str = ""               # e.g. "+6 Constitution, ×0.8 mortality"


class SpeciesBrief(BaseModel):
    id: str
    name: str
    category: str


class SpeciesRead(SpeciesBrief):
    description: str
    lifespan: int
    stat_cap: int
    stat_modifiers: dict[str, int]
    tags: list[str]
    traits: list[TraitRead]
    habitats: list[str]


class StageRead(BaseModel):
    id: str
    name: str
    description: str


class AxisRead(BaseModel):
    id: str
    name: str
    group: str
    low: str
    high: str
    value: int


class ItemRead(BaseModel):
    id: str
    name: str
    description: str
    count: int


class TalentRead(BaseModel):
    id: str
    name: str
    description: str


class CharacterRead(BaseModel):
    id: int
    campaign_id: int
    name: str
    species: SpeciesBrief
    species_description: str
    species_stat_modifiers: dict[str, int]
    stage: StageRead
    location: str
    age: int
    alive: bool
    stats: dict[str, int]                 # base stats
    effective_stats: dict[str, int]       # base + trait bonuses
    stat_cap: int
    # Vitals
    health: int
    max_health: int
    stress: float
    happiness: float
    # Action budget for the current year
    action_budget: int
    actions_remaining: int
    actions_taken_labels: list[str]
    skills: dict[str, float]
    traits: list[TraitRead]
    axes: list[AxisRead]
    alignment: str
    items: list[ItemRead]
    talent: TalentRead | None             # ONLY once awakened; a dormant talent is never exposed
    cause_of_death: str | None
    pending_event: PendingEventRead | None
    battle_summary: BattleSummary | None = None   # the latest fight the player has not dismissed yet
    summary: dict | None                  # filled in once the character has died


class CampaignRead(BaseModel):
    id: int
    name: str
    seed: int
    active: bool
    created_at: datetime
    character: CharacterRead | None


class HistoryEntryRead(BaseModel):
    age: int
    kind: str
    text: str
    event_id: str | None = None
