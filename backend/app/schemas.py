"""Shapes of the JSON the API sends and receives."""
from datetime import datetime

from pydantic import BaseModel, Field


class CampaignCreate(BaseModel):
    character_name: str = Field(min_length=1, max_length=40)
    campaign_name: str | None = Field(default=None, max_length=60)
    seed: int | None = None


class OptionRead(BaseModel):
    id: str
    label: str


class PendingEventRead(BaseModel):
    id: str
    title: str
    text: str
    choices: list[OptionRead]


class CharacterRead(BaseModel):
    id: int
    campaign_id: int
    name: str
    age: int
    alive: bool
    stats: dict[str, int]
    skills: dict[str, float]
    cause_of_death: str | None
    action_taken: str | None
    action_taken_label: str | None
    pending_event: PendingEventRead | None
    summary: dict | None          # filled in once the character has died


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
