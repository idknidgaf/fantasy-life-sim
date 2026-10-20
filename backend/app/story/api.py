"""HTTP routes for the story engine, mounted under /api/story by main.py."""
from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from ..db import get_session
from ..models import CharacterRecord
from . import service
from .errors import StoryEffectError, StoryInvalidChoice, StoryNotAllowed, StoryNotFound


class StoryChoiceRead(BaseModel):
    id: str
    text: str
    available: bool
    locked_hint: str | None = None


class StoryEventRead(BaseModel):
    instance_id: int
    event_id: str
    event_version: int
    event_type: str | None
    skippable: bool = False
    title: str
    narrative: str
    status: str
    created_year: int
    expires_year: int | None
    selected_choice_id: str | None
    choices: list[StoryChoiceRead]


class StoryResolutionRead(BaseModel):
    instance_id: int
    event_id: str
    choice_id: str
    replayed: bool
    effects: list[dict]
    next_event: str | None
    next_instance_id: int | None
    next_skipped_reason: str | None
    triggered_events: list[str] = []


class StoryInstanceSummary(BaseModel):
    instance_id: int
    event_id: str
    event_version: int
    status: str
    selected_choice_id: str | None
    created_year: int
    resolved_year: int | None


class StoryStateRead(BaseModel):
    flags: dict[str, bool | int | float | str]
    instances: list[StoryInstanceSummary]


def _http(fn: Callable, *args, **kwargs):
    """Run a service call, translating story errors into HTTP errors."""
    try:
        return fn(*args, **kwargs)
    except StoryNotFound as e:
        raise HTTPException(404, str(e))
    except StoryInvalidChoice as e:
        raise HTTPException(400, str(e))
    except StoryNotAllowed as e:
        raise HTTPException(409, str(e))
    except StoryEffectError as e:
        raise HTTPException(422, f"Nothing was changed: {e}")


def make_router(active_record: Callable[..., CharacterRecord]) -> APIRouter:
    """`active_record` is a FastAPI dependency returning the active campaign's character."""
    router = APIRouter(prefix="/story", tags=["story"])

    @router.get("/state", response_model=StoryStateRead)
    def state(session: Session = Depends(get_session), rec: CharacterRecord = Depends(active_record)):
        return service.get_state(session, rec)

    @router.get("/events/pending", response_model=list[StoryEventRead])
    def pending(session: Session = Depends(get_session), rec: CharacterRecord = Depends(active_record)):
        """Open story events. Fetching them marks them as presented (idempotent)."""
        return _http(service.get_pending, session, rec)

    @router.post("/events/{event_id}/start", response_model=StoryEventRead, status_code=201)
    def start(event_id: str, session: Session = Depends(get_session),
              rec: CharacterRecord = Depends(active_record)):
        return _http(service.start_event, session, rec, event_id)

    @router.post("/instances/{instance_id}/choices/{choice_id}", response_model=StoryResolutionRead)
    def choose(instance_id: int, choice_id: str, session: Session = Depends(get_session),
               rec: CharacterRecord = Depends(active_record)):
        return _http(service.resolve_choice, session, rec, instance_id, choice_id)

    return router
