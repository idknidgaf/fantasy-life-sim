"""NPC awareness and personal relationships: the explicit rules for when an NPC becomes Notable or an Acquaintance.

Three separate things, never inferred from one another:

  AWARENESS   the family line has heard of or met the NPC: a KnownNpc row exists (known.record_known). Notable Figures
              are the named NPCs that are aware. Survives everything below.
  FAMILY      parent links (relationships.py, family.py). Not touched here: nothing in this module adds or removes one.
  PERSONAL    an established relationship between the playable character and the NPC (an Acquaintance), with a closeness
              and a contact stance. Stored on the same KnownNpc row, for the playable life named in `holder`.

Nothing here happens by itself. An NPC is registered only when a caller (a story effect, later a social action) invokes
one of these functions, so merely mentioning an NPC creates nothing. Each function is idempotent and returns a
`Registration` saying what changed and why not, instead of raising for an ordinary refusal.

  reveal                 awareness only ("you have heard of them")
  establish_acquaintance awareness + an acquaintance, unless they are hostile / refuse contact / have died
  change_closeness       improve or damage an EXISTING relationship (clamped 0-100; never ends it by itself)
  set_stance             hostile / refused: ends any acquaintance and blocks a new one; open restores contact
  end_relationship       ends the acquaintance and the friend/partner/rival links; keeps the character, family, awareness

Personal state belongs to one playable life: when an heir takes over, their parent's acquaintances are still known of,
but not acquaintances, until the heir establishes them. No closeness decay, no budget of interactions: those are
separate systems.

Callers own the transaction (nothing commits), like npc_store.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlmodel import Session, select

from .known import SOURCES, record_known
from .models import KnownNpc, NpcRecord
from .npc import CharacterCategory, LifecycleStatus
from .npc_store import world_year_of
from .relationships import PLAYER_PREFIX, RelationshipKind, add_relationship, remove_relationship

Stance = Literal["open", "hostile", "refused"]
STANCES: tuple[str, ...] = ("open", "hostile", "refused")

# Link kinds that make up a personal relationship. Ending a relationship removes these and never a parent link.
PERSONAL_KINDS = (RelationshipKind.FRIEND, RelationshipKind.PARTNER, RelationshipKind.RIVAL, RelationshipKind.MENTOR)
# Kinds an acquaintance may be recorded with (symmetric, so no direction to choose). Mentor links stay with link_npc.
ACQUAINTANCE_KINDS = ("friend", "partner", "rival")

INITIAL_CLOSENESS = 10          # a new acquaintance: someone you have met, not yet someone close
MIN_CLOSENESS, MAX_CLOSENESS = 0, 100


class AwarenessError(ValueError):
    """A request that cannot be meaningful (not a player ref, not a kept NPC, bad value)."""


@dataclass(frozen=True)
class PersonalState:
    acquainted: bool = False
    closeness: int | None = None
    stance: str = "open"


@dataclass(frozen=True)
class Registration:
    npc_id: str
    changed: bool                       # did this call change anything
    reason: str | None = None           # why nothing changed (or what was refused)
    acquainted: bool = False            # the state after the call
    closeness: int | None = None
    stance: str = "open"
    aware: bool = False


# ---------------------------------------------------------------- reading

def _row(session: Session, campaign_id: int, npc_id: str) -> KnownNpc | None:
    return session.exec(select(KnownNpc).where(KnownNpc.campaign_id == campaign_id, KnownNpc.npc_id == npc_id)).first()


def personal_state(row: KnownNpc | None, player: str) -> PersonalState:
    """The player's personal relationship as stored; for another life (or no row) it is the default: none."""
    if row is None or row.holder != player:
        return PersonalState()
    return PersonalState(row.acquainted, row.closeness if row.acquainted else None, row.stance)


def state_of(session: Session, campaign_id: int, player: str, npc_id: str) -> PersonalState:
    return personal_state(_row(session, campaign_id, npc_id), player)


def acquainted_ids(session: Session, campaign_id: int, player: str) -> list[str]:
    """Every NPC the player (this life) is an acquaintance of, oldest first."""
    return list(session.exec(select(KnownNpc.npc_id).where(
        KnownNpc.campaign_id == campaign_id, KnownNpc.holder == player, KnownNpc.acquainted == True)  # noqa: E712
        .order_by(KnownNpc.id)))


# ---------------------------------------------------------------- writing

def _check(session: Session, campaign_id: int, player: str, npc_id: str) -> NpcRecord:
    if not player.startswith(PLAYER_PREFIX):
        raise AwarenessError(f"'{player}' is not a playable character")
    row = session.exec(select(NpcRecord).where(NpcRecord.campaign_id == campaign_id, NpcRecord.npc_id == npc_id)).first()
    if row is None or row.category == CharacterCategory.GENERIC.value:
        raise AwarenessError(f"'{npc_id}' is not a kept NPC of this campaign")
    return row


def _mine(session: Session, campaign_id: int, player: str, npc_id: str, source: str) -> KnownNpc:
    """The NPC's awareness row (made if needed), with its personal fields reset if they belonged to another life."""
    row = record_known(session, campaign_id, npc_id, source)
    if row.holder != player:
        row.holder, row.acquainted, row.closeness, row.stance, row.acquainted_year = player, False, None, "open", None
    return row


def _result(npc_id: str, row: KnownNpc | None, player: str, changed: bool, reason: str | None = None) -> Registration:
    st = personal_state(row, player)
    return Registration(npc_id, changed, reason, st.acquainted, st.closeness, st.stance, aware=row is not None)


def _drop_personal_links(session: Session, campaign_id: int, player: str, npc_id: str) -> int:
    n = 0
    for kind in PERSONAL_KINDS:
        n += remove_relationship(session, campaign_id, kind, player, npc_id)
        if kind not in (RelationshipKind.FRIEND, RelationshipKind.PARTNER, RelationshipKind.RIVAL):
            n += remove_relationship(session, campaign_id, kind, npc_id, player)       # mentor is directed
    return n


def reveal(session: Session, campaign_id: int, player: str, npc_id: str, source: str = "story") -> Registration:
    """The player has heard of / encountered this NPC. Awareness only: no relationship of any kind."""
    _check(session, campaign_id, player, npc_id)
    if source not in SOURCES:
        raise AwarenessError(f"unknown source '{source}'")
    existed = _row(session, campaign_id, npc_id) is not None
    row = record_known(session, campaign_id, npc_id, source)
    return _result(npc_id, row, player, not existed, None if not existed else "already aware")


def establish_acquaintance(session: Session, campaign_id: int, player: str, npc_id: str, *, kind: str | None = None,
                           closeness: int | None = None, source: str = "encounter") -> Registration:
    """Record that the player has personally met this NPC and has a relationship with them. Refused (nothing changes)
    if they are hostile, refuse contact, or are dead or gone. Repeating it never resets an existing closeness."""
    npc = _check(session, campaign_id, player, npc_id)
    if kind is not None and kind not in ACQUAINTANCE_KINDS:
        raise AwarenessError(f"an acquaintance kind must be one of {list(ACQUAINTANCE_KINDS)}")
    if closeness is not None and not MIN_CLOSENESS <= closeness <= MAX_CLOSENESS:
        raise AwarenessError(f"closeness must be {MIN_CLOSENESS}-{MAX_CLOSENESS}")
    current = personal_state(_row(session, campaign_id, npc_id), player)
    if current.stance != "open":
        return _result(npc_id, _row(session, campaign_id, npc_id), player, False,
                       "they are hostile" if current.stance == "hostile" else "they refuse further contact")
    if npc.status != LifecycleStatus.ACTIVE.value:
        return _result(npc_id, _row(session, campaign_id, npc_id), player, False, f"they are {npc.status}")
    row = _mine(session, campaign_id, player, npc_id, source)
    changed = not row.acquainted
    if changed:
        row.acquainted, row.acquainted_year = True, world_year_of(session, campaign_id)
        row.closeness = INITIAL_CLOSENESS if closeness is None else closeness
    if kind is not None:
        add_relationship(session, campaign_id, kind, player, npc_id)               # idempotent: one link however often
    session.add(row)
    session.flush()
    return _result(npc_id, row, player, changed, None if changed else "already an acquaintance")


def change_closeness(session: Session, campaign_id: int, player: str, npc_id: str, delta: int) -> Registration:
    """Improve (positive) or damage (negative) an existing relationship. Clamped to 0-100; reaching 0 does not end it."""
    _check(session, campaign_id, player, npc_id)
    row = _row(session, campaign_id, npc_id)
    if not personal_state(row, player).acquainted:
        return _result(npc_id, row, player, False, "not an acquaintance")
    new = max(MIN_CLOSENESS, min(MAX_CLOSENESS, (row.closeness or 0) + delta))
    changed = new != row.closeness
    row.closeness = new
    session.add(row)
    session.flush()
    return _result(npc_id, row, player, changed, None if changed else "closeness is already at its limit")


def end_relationship(session: Session, campaign_id: int, player: str, npc_id: str) -> Registration:
    """End the active acquaintance: closeness and friend/partner/rival/mentor links go. The character, their family
    links, and the player's awareness of them stay."""
    _check(session, campaign_id, player, npc_id)
    row = _row(session, campaign_id, npc_id)
    if not personal_state(row, player).acquainted:
        return _result(npc_id, row, player, False, "not an acquaintance")
    row.acquainted, row.closeness, row.acquainted_year = False, None, None
    _drop_personal_links(session, campaign_id, player, npc_id)
    session.add(row)
    session.flush()
    return _result(npc_id, row, player, True)


def set_stance(session: Session, campaign_id: int, player: str, npc_id: str, stance: str) -> Registration:
    """`hostile` or `refused`: the NPC becomes aware of the player on those terms (awareness is kept), any acquaintance
    ends, and a new one is refused until `open` restores contact. This never makes anyone an acquaintance."""
    _check(session, campaign_id, player, npc_id)
    if stance not in STANCES:
        raise AwarenessError(f"stance must be one of {list(STANCES)}")
    existing = _row(session, campaign_id, npc_id)
    current = personal_state(existing, player)
    if stance == current.stance:
        return _result(npc_id, existing, player, False, f"already {stance}")
    if stance == "open" and existing is None:
        return _result(npc_id, None, player, False, "already open")
    row = _mine(session, campaign_id, player, npc_id, "encounter")
    row.stance = stance
    if stance != "open":
        row.acquainted, row.closeness, row.acquainted_year = False, None, None
        _drop_personal_links(session, campaign_id, player, npc_id)
    session.add(row)
    session.flush()
    return _result(npc_id, row, player, True)
