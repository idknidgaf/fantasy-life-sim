import random
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select

from . import engine as sim
from .content import STATS, get_content
from .db import get_session, init_db
from .models import Campaign, CharacterRecord
from .rules import alignment_label, effective_stat, species_of, stage_of, pretty
from .schemas import (
    AxisRead, CampaignCreate, CampaignRead, CharacterRead, HistoryEntryRead, ItemRead,
    OptionRead, PendingEventRead, SpeciesBrief, SpeciesRead, StageRead, TalentRead, TraitRead,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_content()          # fail fast if any content file is invalid
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


def _trait_read(ct, trait_id: str) -> TraitRead:
    t = ct.traits[trait_id]
    return TraitRead(id=t.id, name=t.name, description=t.description, tier=t.tier)


def _character_read(rec: CharacterRecord) -> CharacterRead:
    ct = get_content()
    c = sim.repair(rec.to_character())
    sp = species_of(c, ct)
    stage = stage_of(c, ct)

    pending = None
    if c.pending_event:
        ev = ct.events[c.pending_event]
        pending = PendingEventRead(
            id=ev.id, title=ev.title, text=ev.narrative.strip(),
            choices=[OptionRead(**o) for o in sim.event_choice_state(c, ct, ev.id)],
        )
    talent = None
    if c.talent_state == "awakened" and c.hidden_talent:        # a dormant talent must never leak
        t = ct.talents[c.hidden_talent]
        talent = TalentRead(id=t.id, name=t.name, description=t.description)

    return CharacterRead(
        id=rec.id, campaign_id=rec.campaign_id, name=c.name,
        species=SpeciesBrief(id=sp.id, name=sp.name, category=sp.category),
        stage=StageRead(id=stage.id, name=stage.name, description=stage.description),
        location=c.location, age=c.age, alive=c.alive,
        stats=c.stats, effective_stats={s: effective_stat(c, ct, s) for s in STATS},
        stat_cap=sp.stat_cap, skills=c.skills,
        traits=[_trait_read(ct, t) for t in c.traits if t in ct.traits],
        axes=[AxisRead(id=a.id, name=a.name, group=a.group, low=a.low, high=a.high, value=c.axes.get(a.id, 0))
              for a in ct.axes.values()],
        alignment=alignment_label(c),
        items=[ItemRead(id=i, name=ct.items[i].name, description=ct.items[i].description, count=n)
               for i, n in sorted(c.items.items()) if i in ct.items and n > 0],
        talent=talent,
        cause_of_death=c.cause_of_death,
        action_taken=c.action_taken,
        action_taken_label=sim.action_label(c.action_taken, c.species_id, ct) if c.action_taken else None,
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
    c = sim.repair(rec.to_character())
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


# ---------- reference data ----------

@api.get("/species", response_model=list[SpeciesRead])
def list_species():
    ct = get_content()
    return [
        SpeciesRead(
            id=s.id, name=s.name, category=s.category, description=s.description,
            lifespan=s.lifespan, stat_cap=s.stat_cap, stat_modifiers=s.stat_modifiers,
            tags=[pretty(t) for t in s.tags], habitats=s.habitats,
            traits=[_trait_read(ct, t) for t in s.innate_traits],
        )
        for s in ct.species.values()
    ]


@api.get("/skills")
def get_skills():
    """{skill_id: category}, straight from skills.json."""
    return get_content().skills


# ---------- campaigns ----------

@api.post("/campaign", response_model=CampaignRead, status_code=201)
def create_campaign(body: CampaignCreate, session: Session = Depends(get_session)):
    seed = body.seed if body.seed is not None else random.randrange(2**31)
    name = body.character_name.strip()
    try:
        # The hidden-talent roll happens here, once. The result is stored with the character.
        character = sim.new_character(name, body.species, seed)
    except sim.InvalidChoice as e:
        raise HTTPException(400, str(e))

    for other in session.exec(select(Campaign).where(Campaign.active == True)):  # noqa: E712
        other.active = False
        session.add(other)
    campaign = Campaign(name=(body.campaign_name or name).strip(), seed=seed, active=True)
    session.add(campaign)
    session.flush()
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
    return sim.offer_actions(sim.repair(rec.to_character()), campaign.seed)


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


app.include_router(api)
