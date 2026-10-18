import random
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select

from . import engine as sim
from .db import get_session, init_db
from .models import Campaign, CharacterRecord
from .schemas import (
    CampaignCreate, CampaignRead, CharacterRead, HistoryEntryRead, OptionRead,
    PendingEventRead,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Fantasy Life Sim", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
api = APIRouter(prefix="/api")


# ---------- helpers ----------

def _record_for(session: Session, campaign_id: int) -> CharacterRecord | None:
    return session.exec(
        select(CharacterRecord)
        .where(CharacterRecord.campaign_id == campaign_id)
        .order_by(CharacterRecord.id.desc())
    ).first()


def _active(session: Session) -> tuple[Campaign, CharacterRecord]:
    campaign = session.exec(
        select(Campaign).where(Campaign.active == True).order_by(Campaign.id.desc())  # noqa: E712
    ).first()
    if campaign is None:
        raise HTTPException(404, "No active campaign")
    record = _record_for(session, campaign.id)
    if record is None:
        raise HTTPException(404, "This campaign has no character")
    return campaign, record


def _character_read(rec: CharacterRecord) -> CharacterRead:
    c = rec.to_character()
    pending = None
    if c.pending_event:
        ev = sim.load_events()[c.pending_event]
        pending = PendingEventRead(
            id=ev["id"], title=ev["title"], text=ev["text"],
            choices=[OptionRead(id=ch["id"], label=ch["label"]) for ch in ev["choices"]],
        )
    return CharacterRead(
        id=rec.id, campaign_id=rec.campaign_id, name=c.name, age=c.age, alive=c.alive,
        stats=c.stats, skills=c.skills, cause_of_death=c.cause_of_death,
        action_taken=c.action_taken,
        action_taken_label=sim.action_label(c.action_taken) if c.action_taken else None,
        pending_event=pending,
        summary=None if c.alive else sim.summarize(c),
    )


def _campaign_read(session: Session, campaign: Campaign) -> CampaignRead:
    rec = _record_for(session, campaign.id)
    return CampaignRead(
        id=campaign.id, name=campaign.name, seed=campaign.seed, active=campaign.active,
        created_at=campaign.created_at,
        character=_character_read(rec) if rec else None,
    )


def _mutate(session: Session, change: Callable[[sim.Character, int], None]) -> CharacterRead:
    """Load the active character, apply a rule from the engine, save, return it."""
    campaign, rec = _active(session)
    c = rec.to_character()
    try:
        change(c, campaign.seed)
    except sim.InvalidChoice as e:
        raise HTTPException(400, str(e))
    except sim.NotAllowed as e:
        raise HTTPException(409, str(e))
    rec.update_from(c)
    session.add(rec)
    session.commit()
    session.refresh(rec)
    return _character_read(rec)


# ---------- campaigns ----------

@api.post("/campaign", response_model=CampaignRead, status_code=201)
def create_campaign(body: CampaignCreate, session: Session = Depends(get_session)):
    seed = body.seed if body.seed is not None else random.randrange(2**31)
    name = body.character_name.strip()
    for other in session.exec(select(Campaign).where(Campaign.active == True)):  # noqa: E712
        other.active = False
        session.add(other)

    campaign = Campaign(name=(body.campaign_name or name).strip(), seed=seed, active=True)
    session.add(campaign)
    session.flush()
    character = sim.new_character(name, random.Random(seed))
    session.add(CharacterRecord.from_character(campaign.id, character))
    session.commit()
    session.refresh(campaign)
    return _campaign_read(session, campaign)


@api.get("/campaign", response_model=CampaignRead)
def get_campaign(session: Session = Depends(get_session)):
    campaign, _ = _active(session)
    return _campaign_read(session, campaign)


@api.get("/campaigns", response_model=list[CampaignRead])
def list_campaigns(session: Session = Depends(get_session)):
    campaigns = session.exec(select(Campaign).order_by(Campaign.id.desc())).all()
    return [_campaign_read(session, c) for c in campaigns]


@api.post("/campaigns/{campaign_id}/activate", response_model=CampaignRead)
def activate_campaign(campaign_id: int, session: Session = Depends(get_session)):
    target = session.get(Campaign, campaign_id)
    if target is None:
        raise HTTPException(404, "Campaign not found")
    for c in session.exec(select(Campaign)).all():
        c.active = c.id == campaign_id
        session.add(c)
    session.commit()
    session.refresh(target)
    return _campaign_read(session, target)


@api.delete("/campaigns/{campaign_id}", status_code=204)
def delete_campaign(campaign_id: int, session: Session = Depends(get_session)):
    campaign = session.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaign not found")
    for rec in session.exec(select(CharacterRecord).where(CharacterRecord.campaign_id == campaign_id)):
        session.delete(rec)
    session.delete(campaign)
    session.commit()


# ---------- character, actions, turns, events ----------

@api.get("/character", response_model=CharacterRead)
def get_character(session: Session = Depends(get_session)):
    _, rec = _active(session)
    return _character_read(rec)


@api.get("/actions", response_model=list[OptionRead])
def list_actions(session: Session = Depends(get_session)):
    campaign, rec = _active(session)
    return sim.offer_actions(rec.to_character(), campaign.seed)


@api.post("/actions/{action_id}", response_model=CharacterRead)
def perform_action(action_id: str, session: Session = Depends(get_session)):
    return _mutate(session, lambda c, seed: sim.perform_action(c, seed, action_id))


@api.post("/turn/advance", response_model=CharacterRead)
def advance_turn(session: Session = Depends(get_session)):
    return _mutate(session, sim.advance_turn)


@api.post("/events/{event_id}/choices/{choice_id}", response_model=CharacterRead)
def resolve_event(event_id: str, choice_id: str, session: Session = Depends(get_session)):
    return _mutate(session, lambda c, seed: sim.resolve_event(c, seed, event_id, choice_id))


@api.get("/history", response_model=list[HistoryEntryRead])
def get_history(
    limit: int | None = Query(default=None, ge=1, le=1000),
    session: Session = Depends(get_session),
):
    _, rec = _active(session)
    history = list(rec.history)
    return history[-limit:] if limit else history


@api.get("/skills")
def get_skills():
    """{skill_id: category}, straight from skills.json."""
    return sim.load_skills()


app.include_router(api)
