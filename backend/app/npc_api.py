"""Read-only HTTP routes for NPCs, mounted under /api/npcs by main.py. The contracts are in docs/NPC_API.md.

Context: like every other route, these act on the ACTIVE campaign and its playable character (`active_record`, the same
dependency the story routes use). The app has no accounts, so there is no separate authentication: the campaign is the
boundary. An NPC of another campaign, a temporary (generic) NPC, and an NPC the family has not met (see known.py) are all
the same plain 404, so ids cannot be probed.

Read endpoints never change anything: they do not commit, do not apply growth or ageing (that is npc_world.advance_world,
called from Age Up), and do not store sections generated on demand. What is returned is built for the response only:
no database ids, seeds, generation specs, story flags or authored definition internals, and a talent that has not
awakened is never shown (a dormant talent must not leak, as for the playable character).

Bounded: lists take `limit` (1..100, default 25) and `offset`. Every endpoint loads what it needs in a handful of queries
whatever the number of NPCs: one for the page, one for its links to the player, and batched lookups for family.
"""
from __future__ import annotations

from typing import Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlmodel import Session, col, select

from . import family as fam
from .character import Character
from .character_groups import CATEGORIES, Category, family_roles, personal_link_ids
from .content import STATS, Content, get_content
from .db import get_session
from .known import is_known, known_ids, met_sources
from .models import CharacterRecord, CharacterRelationship, NpcRecord
from . import named
from .npc import CharacterCategory, Npc, to_character
from .power import PowerBreakdown, PowerConfig, calculate_power, get_power_config
from .relationships import PLAYER_PREFIX, RelationshipKind, player_ref
from .rules import describe_trait_effects, effective_stat, stage_at, stage_name
from .schemas import SpeciesBrief, StageRead, TalentRead, TraitRead

DEFAULT_LIMIT = 25
MAX_LIMIT = 100
MAX_FAMILY = 50                     # members listed per group in the family response
GENERIC = CharacterCategory.GENERIC.value


# ---------------------------------------------------------------- response shapes

class NpcSummaryRead(BaseModel):
    id: str                                   # the stable character id, e.g. "npc_3f2a..." or "named_sword_saint_kael"
    name: str
    category: Literal["persistent", "named"]
    status: Literal["active", "deceased", "retired"]
    species: SpeciesBrief
    age: int
    stage: StageRead
    power: float
    relations: list[str]                      # how they relate to the playable character: direct links ("child", "friend",
                                              # "mentor"...) and family roles ("sibling", "half_sibling", "cousin"...)
    title: str | None = None                  # authored title of a named character
    categories: list[Category] = []           # the Character tab groups they belong to: family, acquaintances, notable
    closeness: int | None = None              # 0-100 for a personal relationship. The relationship data stores none yet,
                                              # so this is always null today; the field is the contract for when it does


class NpcListRead(BaseModel):
    total: int                                # all known NPCs matching the filters
    limit: int
    offset: int
    items: list[NpcSummaryRead]


class MetRead(BaseModel):
    source: str                               # how they were first met (encounter, story, birth, manual)
    first_met_year: int                       # the campaign's world year


class NpcDetailRead(NpcSummaryRead):
    surname: str | None = None
    description: str = ""                     # authored text of a named character
    cause_of_death: str | None = None
    stats: dict[str, int] | None = None       # base stats; null if never generated
    effective_stats: dict[str, int] | None = None     # with trait bonuses
    stat_cap: int
    specialised_stats: dict[str, float]       # Magic, Swordsmanship, Marksmanship, Martial Arts (0 when untrained)
    traits: list[TraitRead]
    talent: TalentRead | None = None          # only once awakened
    power_breakdown: PowerBreakdown
    met: MetRead | None = None                # null when known only through a relationship


class FamilyMemberRead(BaseModel):
    id: str
    kind: Literal["npc", "player"]
    name: str | None                          # null for an NPC the family has not met
    known: bool
    status: str | None = None


class SiblingRead(FamilyMemberRead):
    relation: Literal["full", "half"]
    shared_parents: list[str]


class CousinRead(FamilyMemberRead):
    shared_grandparents: list[str]


class FamilyRead(BaseModel):
    id: str
    parents: list[FamilyMemberRead]
    children: list[FamilyMemberRead]
    siblings: list[SiblingRead]               # full and half, told apart by `relation`
    grandparents: list[FamilyMemberRead]
    cousins: list[CousinRead]
    truncated: bool = False                   # some group had more than MAX_FAMILY members


# ---------------------------------------------------------------- building them

def _species(ct: Content, species_id: str) -> SpeciesBrief:
    sp = ct.species.get(species_id)
    return SpeciesBrief(id=species_id, name=sp.name if sp else species_id, category=sp.category if sp else "unknown")


def _stage(ct: Content, npc: Npc) -> StageRead:
    sp = ct.species.get(npc.species_id)
    if sp is None:
        return StageRead(id="unknown", name="Unknown", description="")
    st = stage_at(sp, npc.age, ct)
    return StageRead(id=st.id, name=stage_name(sp, st), description=st.description)


def _relation_words(player: str, links: list[tuple[str, str, str]], npc_id: str) -> list[str]:
    """The player's relation to one NPC, from the direct links between them: parent/child read from the player's side."""
    out: list[str] = []
    for kind, source, target in links:
        if {source, target} != {player, npc_id}:
            continue
        if kind == RelationshipKind.PARENT.value:
            word = "child" if source == player else "parent"
        elif kind == RelationshipKind.MENTOR.value:
            word = "mentee" if source == player else "mentor"
        else:
            word = kind
        if word not in out:
            out.append(word)
    return out


def _summary(npc: Npc, relations: list[str], ct: Content, cfg: PowerConfig,
             categories: list[Category] | None = None) -> NpcSummaryRead:
    d = named.definitions().get(npc.definition or "")
    return NpcSummaryRead(
        id=npc.id, name=npc.name, category=npc.category.value, status=npc.status.value,
        species=_species(ct, npc.species_id), age=npc.age, stage=_stage(ct, npc),
        power=calculate_power(to_character(npc, ct, cfg), ct, cfg).total, relations=relations,
        title=d.title if d else None, categories=categories or [])


def _detail(npc: Npc, relations: list[str], met, ct: Content, cfg: PowerConfig,
            categories: list[Category] | None = None) -> NpcDetailRead:
    c: Character = to_character(npc, ct, cfg)             # sections missing from the save come from its seed, unsaved
    d = named.definitions().get(npc.definition or "")
    has_stats = bool(c.stats)
    sp = ct.species.get(npc.species_id)
    traits = [ct.traits[t] for t in c.traits if t in ct.traits]
    talent = (ct.talents[c.hidden_talent] if c.talent_state == "awakened" and c.hidden_talent in ct.talents else None)
    breakdown = calculate_power(c, ct, cfg)
    base = _summary(npc, relations, ct, cfg, categories)
    return NpcDetailRead(
        **base.model_dump(), surname=npc.surname, description=d.description if d else "",
        cause_of_death=npc.cause_of_death,
        stats=dict(c.stats) if has_stats else None,
        effective_stats={s: effective_stat(c, ct, s) for s in STATS} if has_stats else None,
        stat_cap=sp.stat_cap if sp else 0,
        specialised_stats={stat: round(sum(c.skills.get(k, 0.0) for k in dfn.skills), 2)
                           for stat, dfn in cfg.specialised_stats.items()},
        traits=[TraitRead(id=t.id, name=t.name, description=t.description, tier=t.tier,
                          effects=describe_trait_effects(t, ct)) for t in traits],
        talent=TalentRead(id=talent.id, name=talent.name, description=talent.description) if talent else None,
        power_breakdown=breakdown,
        met=MetRead(source=met.source, first_met_year=met.first_met_year) if met else None)


def _links_with_player(session: Session, campaign_id: int, player: str, ids: list[str]) -> list[tuple[str, str, str]]:
    if not ids:
        return []
    rows = session.exec(select(CharacterRelationship.kind, CharacterRelationship.source_id, CharacterRelationship.target_id).where(
        CharacterRelationship.campaign_id == campaign_id,
        or_((CharacterRelationship.source_id == player) & col(CharacterRelationship.target_id).in_(ids),
            (CharacterRelationship.target_id == player) & col(CharacterRelationship.source_id).in_(ids))))
    return [tuple(r) for r in rows]


def _roles(direct: list[str], role: str | None) -> list[str]:
    return [*direct, role] if role and role not in direct else direct


def _categories_of(npc: Npc, family: dict[str, str], personal: set[str]) -> list[Category]:
    out: list[Category] = []
    if npc.id in family:
        out.append("family")
    if npc.category == CharacterCategory.PERSISTENT and npc.id in personal:
        out.append("acquaintances")
    if npc.category == CharacterCategory.NAMED:
        out.append("notable")
    return out


def _load_known_row(session: Session, campaign_id: int, player: str, npc_id: str) -> NpcRecord:
    """The saved NPC, only if it is a kept NPC of this campaign that the family knows. Anything else is a plain 404."""
    if npc_id.startswith(PLAYER_PREFIX) or not npc_id.startswith(("npc_", "named_")) or len(npc_id) > 128:
        raise HTTPException(400, f"'{npc_id}' is not an NPC id")
    row = session.exec(select(NpcRecord).where(NpcRecord.campaign_id == campaign_id, NpcRecord.npc_id == npc_id)).first()
    if row is None or row.category == GENERIC or not is_known(session, campaign_id, player, npc_id):
        raise HTTPException(404, "No such NPC")
    return row


# ---------------------------------------------------------------- the router

def make_router(active_record: Callable[..., CharacterRecord]) -> APIRouter:
    """`active_record` is a FastAPI dependency returning the active campaign's playable character."""
    router = APIRouter(prefix="/npcs", tags=["npcs"])

    @router.get("", response_model=NpcListRead)
    def list_known(limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT), offset: int = Query(default=0, ge=0),
                   category: Category | None = None,
                   status: Literal["active", "deceased", "retired"] | None = None, species: str | None = None,
                   session: Session = Depends(get_session), rec: CharacterRecord = Depends(active_record)):
        """The NPCs the family knows: recorded as met, or linked to the playable character. Oldest first.
        `category` narrows it to one Character tab group (see character_groups.py): family, acquaintances (persistent
        NPCs with a friend/partner/rival/mentor link) or notable (named NPCs). Each NPC is listed once."""
        ct, cfg, player = get_content(), get_power_config(), player_ref(rec)
        ids = known_ids(session, rec.campaign_id, player)
        if not ids:
            return NpcListRead(total=0, limit=limit, offset=offset, items=[])
        known = set(ids)
        family = {i: r for i, r in family_roles(session, rec.campaign_id, player).items() if i in known}
        personal = {i for i in personal_link_ids(session, rec.campaign_id, player) if i in known}
        where = [NpcRecord.campaign_id == rec.campaign_id, NpcRecord.category != GENERIC]
        if category == "family":
            where.append(col(NpcRecord.npc_id).in_(list(family)))
        elif category == "acquaintances":
            where += [col(NpcRecord.npc_id).in_(list(personal)), NpcRecord.category == CharacterCategory.PERSISTENT.value]
        elif category == "notable":
            where += [col(NpcRecord.npc_id).in_(ids), NpcRecord.category == CharacterCategory.NAMED.value]
        else:
            where.append(col(NpcRecord.npc_id).in_(ids))
        if status:
            where.append(NpcRecord.status == status)
        if species:
            where.append(NpcRecord.species_id == species)
        total = session.exec(select(func.count()).select_from(NpcRecord).where(*where)).one()
        rows = session.exec(select(NpcRecord).where(*where).order_by(NpcRecord.id).offset(offset).limit(limit)).all()
        npcs = [Npc.model_validate(r.data) for r in rows]
        links = _links_with_player(session, rec.campaign_id, player, [n.id for n in npcs])
        return NpcListRead(total=total, limit=limit, offset=offset, items=[
            _summary(n, _roles(_relation_words(player, links, n.id), family.get(n.id)), ct, cfg,
                     _categories_of(n, family, personal)) for n in npcs])

    @router.get("/{npc_id}", response_model=NpcDetailRead)
    def get_npc(npc_id: str, session: Session = Depends(get_session), rec: CharacterRecord = Depends(active_record)):
        """One known NPC by stable id: age, species, stats, traits, awakened talent and calculated Power."""
        ct, cfg, player = get_content(), get_power_config(), player_ref(rec)
        row = _load_known_row(session, rec.campaign_id, player, npc_id)
        npc = Npc.model_validate(row.data)
        links = _links_with_player(session, rec.campaign_id, player, [npc_id])
        family = family_roles(session, rec.campaign_id, player)
        personal = set(personal_link_ids(session, rec.campaign_id, player))
        return _detail(npc, _roles(_relation_words(player, links, npc_id), family.get(npc_id)),
                       met_sources(session, rec.campaign_id, [npc_id]).get(npc_id), ct, cfg,
                       _categories_of(npc, family, personal))

    @router.get("/{npc_id}/family", response_model=FamilyRead)
    def get_family(npc_id: str, session: Session = Depends(get_session), rec: CharacterRecord = Depends(active_record)):
        """Parents, children, siblings (full and half), grandparents and cousins. Relatives the family has not met are
        listed by id only, with no name."""
        player = player_ref(rec)
        _load_known_row(session, rec.campaign_id, player, npc_id)
        tree = fam.family_of(session, rec.campaign_id, npc_id, max_depth=2)
        truncated = False

        def cap(items):
            nonlocal truncated
            truncated = truncated or len(items) > MAX_FAMILY
            return items[:MAX_FAMILY]

        parents, children = cap(tree.parents), cap(tree.children)
        siblings, grandparents, cousins = cap(tree.siblings), cap(tree.grandparents), cap(tree.cousins)
        wanted = {*parents, *children, *grandparents, *(s.ref for s in siblings), *(c.ref for c in cousins)}
        members = _members(session, rec, player, wanted)
        return FamilyRead(
            id=npc_id, parents=[members[r] for r in parents if r in members],
            children=[members[r] for r in children if r in members],
            siblings=[SiblingRead(**members[s.ref].model_dump(), relation=s.kind, shared_parents=list(s.shared_parents))
                      for s in siblings if s.ref in members],
            grandparents=[members[r] for r in grandparents if r in members],
            cousins=[CousinRead(**members[c.ref].model_dump(), shared_grandparents=list(c.shared_grandparents))
                     for c in cousins if c.ref in members],
            truncated=truncated)

    return router


def _members(session: Session, rec: CharacterRecord, player: str, refs: set[str]) -> dict[str, FamilyMemberRead]:
    """Family members as the player may see them, in two lookups. Names of NPCs the family has not met are withheld."""
    known = set(known_ids(session, rec.campaign_id, player))
    npc_refs = [r for r in refs if not r.startswith(PLAYER_PREFIX)]
    pc_numbers = [int(r[len(PLAYER_PREFIX):]) for r in refs if r.startswith(PLAYER_PREFIX) and r[len(PLAYER_PREFIX):].isdigit()]
    out: dict[str, FamilyMemberRead] = {}
    if npc_refs:
        for row in session.exec(select(NpcRecord).where(NpcRecord.campaign_id == rec.campaign_id, col(NpcRecord.npc_id).in_(npc_refs))):
            if row.category == GENERIC:
                continue
            seen = row.npc_id in known
            out[row.npc_id] = FamilyMemberRead(id=row.npc_id, kind="npc", name=row.name if seen else None, known=seen,
                                               status=row.status if seen else None)
    if pc_numbers:
        for pc in session.exec(select(CharacterRecord).where(CharacterRecord.campaign_id == rec.campaign_id,
                                                              col(CharacterRecord.id).in_(pc_numbers))):
            out[f"{PLAYER_PREFIX}{pc.id}"] = FamilyMemberRead(id=f"{PLAYER_PREFIX}{pc.id}", kind="player", name=pc.name,
                                                              known=True, status="active" if pc.alive else "deceased")
    return out
