import random
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlmodel import Session, delete, select

from . import battle_store, legacy, named, npc_world
from . import engine as sim
from .combat import get_combat_config
from .content import STATS, get_content
from .db import get_session, init_db
from .models import (
    Campaign, CharacterRecord, CombatRecord, LifeRecord, PlayableRole, StoryChoiceLog, StoryEventInstance, StoryFlag,
)
from .npc_store import delete_campaign_npcs
from .rules import (
    alignment_label, describe_trait_effects, effective_stat, max_health, pretty, species_of,
    stage_name, stage_of,
)
from .power import PowerBreakdown, calculate_power
from .story import service as story_service
from .npc_api import make_router as make_npc_router
from .story.api import make_router as make_story_router
from .story.library import get_story_library
from .schemas import (
    AxisRead, CampaignCreate, CampaignRead, CharacterRead, HistoryEntryRead, ItemRead,
    OptionRead, PendingEventRead, SpeciesBrief, SpeciesRead, StageRead, TalentRead, TraitRead,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_content()          # fail fast if any content file is invalid
    get_combat_config()
    get_story_library()
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
    """The character the player controls: the holder of the campaign's playable role (see legacy.py)."""
    return legacy.player_record(session, campaign_id)


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
    return TraitRead(id=t.id, name=t.name, description=t.description, tier=t.tier,
                     effects=describe_trait_effects(t, ct))


def _character_read(rec: CharacterRecord, session: Session | None = None) -> CharacterRead:
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
        species_description=sp.description,
        species_stat_modifiers=sp.stat_modifiers,
        stage=StageRead(id=stage.id, name=stage_name(sp, stage), description=stage.description),
        location=c.location, age=c.age, alive=c.alive,
        stats=c.stats, effective_stats={s: effective_stat(c, ct, s) for s in STATS},
        stat_cap=sp.stat_cap,
        health=c.health if c.health is not None else max_health(c, ct),
        max_health=max_health(c, ct),
        stress=c.stress, happiness=c.happiness,
        action_budget=sim.action_budget(c, ct),
        actions_remaining=sim.actions_remaining(c, ct),
        actions_taken_labels=[sim.action_label(a, c.species_id, ct) for a in c.actions_taken],
        skills=c.skills,
        traits=[_trait_read(ct, t) for t in c.traits if t in ct.traits],
        axes=[AxisRead(id=a.id, name=a.name, group=a.group, low=a.low, high=a.high, value=c.axes.get(a.id, 0))
              for a in ct.axes.values()],
        alignment=alignment_label(c),
        items=[ItemRead(id=i, name=ct.items[i].name, description=ct.items[i].description, count=n)
               for i, n in sorted(c.items.items()) if i in ct.items and n > 0],
        talent=talent,
        cause_of_death=c.cause_of_death,
        pending_event=pending,
        battle_summary=battle_store.pending_battle(session, rec) if session is not None else None,
        summary=None if c.alive else sim.summarize(c),
    )


def _campaign_read(session: Session, campaign: Campaign) -> CampaignRead:
    rec = _record_for(session, campaign.id)
    return CampaignRead(
        id=campaign.id, name=campaign.name, seed=campaign.seed, active=campaign.active,
        created_at=campaign.created_at,
        character=_character_read(rec, session) if rec else None,
    )


def _mutate(session: Session, change: Callable[[sim.Character, int], object], *,
            story_scans: Callable[[], list[tuple[str, dict]]] | None = None,
            blocked_by_story: bool = False,
            world_years: int = 0,
            record_battle: Callable[[CharacterRecord, object], None] | None = None) -> CharacterRead:
    """Load the active character, apply a rule from the engine, save, return it.

    blocked_by_story: refuse while a story event waits for a decision (as for a legacy pending event).
    story_scans: story trigger scans to run after the change, in the same transaction as the save.
    world_years: the world's clock moved on this many years: named characters already introduced age by their own
    lifecycle rule (named.advance_all), and persistent NPCs are brought up to the new world year (age and growth,
    once per year, npc_world.advance_world), in this same transaction and before the story scans see them.
    record_battle: called with the saved record and whatever `change` returned; it stores a battle summary in the
    same transaction as the consequences, so a fight and its record are saved together or not at all.
    Any open (undismissed) summary is closed first: acting again means the player has moved past it.
    """
    campaign, rec = _active(session)
    if blocked_by_story and rec.alive and story_service.has_open_events(session, rec):
        raise HTTPException(409, "An event needs your decision first")
    c = sim.repair(rec.to_character())
    try:
        result = change(c, campaign.seed)
    except sim.InvalidChoice as e:
        raise HTTPException(400, str(e))
    except sim.NotAllowed as e:
        raise HTTPException(409, str(e))
    rec.update_from(c)
    session.add(rec)
    battle_store.acknowledge_all(session, rec)
    if record_battle and result is not None:
        record_battle(rec, result)
    if not rec.alive:                                # however they died, the life is completed, once, right here
        legacy.complete_life(session, rec)
    if world_years and c.alive:
        named.advance_all(session, campaign.id, world_years)
        campaign.world_year += world_years           # the world's clock: NPCs follow it exactly once per year
        session.add(campaign)
        npc_world.advance_world(session, campaign.id, campaign.world_year)
    if story_scans and c.alive:                      # queue eligible story events; never touches pending_event
        story_service.run_scans(story_service.make_context(session, rec), story_scans())
    session.commit()
    session.refresh(rec)
    return _character_read(rec, session)


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
    record = CharacterRecord.from_character(campaign.id, character)
    session.add(record)
    session.flush()
    # The story can begin at birth: offer anything already eligible, in the same transaction.
    story_service.run_scans(story_service.make_context(session, record), [("age", {}), ("state", {})])
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
    for story_table in (CombatRecord, StoryChoiceLog, StoryEventInstance, StoryFlag, LifeRecord, PlayableRole):      # story state belongs to the life
        session.exec(delete(story_table).where(story_table.campaign_id == campaign_id))
    delete_campaign_npcs(session, campaign_id)             # its NPCs and their relationships belong to the life too
    for rec in session.exec(select(CharacterRecord).where(CharacterRecord.campaign_id == campaign_id)):
        session.delete(rec)
    session.delete(campaign)
    session.commit()


# ---------- character, actions, turns, events ----------

@api.get("/character", response_model=CharacterRead)
def get_character(session: Session = Depends(get_session)):
    _, rec = _active(session)
    return _character_read(rec, session)


@api.get("/actions", response_model=list[OptionRead])
def list_actions(session: Session = Depends(get_session)):
    campaign, rec = _active(session)
    if rec.alive and story_service.has_open_events(session, rec):
        return []                                    # like a pending legacy event: decide first
    return sim.offer_actions(sim.repair(rec.to_character()), campaign.seed)


@api.post("/actions/{action_id}", response_model=CharacterRead)
def perform_action(action_id: str, session: Session = Depends(get_session)):
    return _mutate(session, lambda c, seed: sim.perform_action(c, seed, action_id),
                   blocked_by_story=True,
                   story_scans=lambda: [("action", {"action": action_id}), ("state", {})])


@api.post("/turn/advance", response_model=CharacterRead)
def advance_turn(session: Session = Depends(get_session)):
    return _mutate(session, sim.advance_turn, blocked_by_story=True, world_years=1,
                   story_scans=lambda: [("age", {}), ("state", {})])


@api.post("/events/{event_id}/choices/{choice_id}", response_model=CharacterRead)
def resolve_event(event_id: str, choice_id: str, session: Session = Depends(get_session)):
    # A decision can change the character's state, so story encounters it unlocks appear right away.
    # A fight (or a flee attempt) returns its battle summary; it is stored with the consequences and shown
    # until the player dismisses it. Any other choice returns nothing.
    return _mutate(session, lambda c, seed: sim.resolve_event(c, seed, event_id, choice_id),
                   story_scans=lambda: [("state", {})],
                   record_battle=lambda rec, summary: battle_store.store_battle(
                       session, rec, summary, choice_id=choice_id))


@api.post("/battles/{battle_id}/acknowledge", response_model=CharacterRead)
def acknowledge_battle(battle_id: int, session: Session = Depends(get_session)):
    """Dismiss a battle summary (Continue). Idempotent. The encounter queue is untouched: the next pending
    encounter simply becomes visible."""
    _, rec = _active(session)
    if not battle_store.acknowledge_battle(session, rec, battle_id):
        raise HTTPException(404, "No such battle")
    session.commit()
    session.refresh(rec)
    return _character_read(rec, session)


class ContinueRequest(BaseModel):
    npc_id: str | None = None          # may be left out when exactly one descendant is eligible


@api.get("/legacy", response_model=legacy.LegacyRead)
def get_legacy(session: Session = Depends(get_session)):
    """Where the family line stands: alive, waiting for an heir to be chosen, or ended. Lists the eligible heirs and
    the lives completed so far."""
    _, rec = _active(session)
    state = legacy.legacy_state(session, rec)
    session.commit()                               # a dead character from an older save gets their life recorded
    return state


@api.post("/legacy/continue", response_model=CharacterRead)
def continue_legacy(body: ContinueRequest | None = None, session: Session = Depends(get_session)):
    """Carry on as an eligible descendant of the dead character. Retrying the same request is harmless."""
    _, rec = _active(session)
    try:
        new = legacy.continue_as(session, rec, body.npc_id if body else None)
    except legacy.LegacyError as e:
        raise HTTPException(e.status_code, str(e))
    session.refresh(new)
    return _character_read(new, session)


@api.get("/power", response_model=PowerBreakdown)
def get_power(session: Session = Depends(get_session)):
    """The active character's Power and how it was derived. Calculated fresh from current state each call."""
    _, rec = _active(session)
    return calculate_power(sim.repair(rec.to_character()), get_content())


@api.get("/history", response_model=list[HistoryEntryRead])
def get_history(
    limit: int | None = Query(default=None, ge=1, le=1000),
    session: Session = Depends(get_session),
):
    _, rec = _active(session)
    history = list(rec.history)
    return history[-limit:] if limit else history


def _active_record(session: Session = Depends(get_session)) -> CharacterRecord:
    return _active(session)[1]


api.include_router(make_story_router(_active_record))
api.include_router(make_npc_router(_active_record))
app.include_router(api)
