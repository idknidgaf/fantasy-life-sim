"""Story-engine types for named (authored) characters, registered on the existing condition and effect registries.

Events refer to a character by their DEFINITION id (the `id` in named_characters.yaml, e.g. `sword_saint_kael`), never
by a runtime id or a name. `other` may also be the word `player`: the character the player is playing.

Conditions (read state, change nothing)
  npc_exists        {npc}                       the character has been introduced in this game
  npc_status        {npc, status}               their lifecycle status: active | deceased | retired
  npc_related       {npc, kind, other, role?}   they are linked to `other`; role: source | target | either (default)
  npc_flag_equals   {npc, flag, value}          one of their story flags equals value (type-aware)
  npc_flag_at_least {npc, flag, value}          a numeric flag is at least value (unset counts as 0)

Effects (applied inside the choice's transaction; a failure rolls the whole choice back)
  spawn_npc         {npc}                       introduce the character. Nothing is spawned by itself: only a story
                                                rule does this, and only if the definition's `encounter.conditions`
                                                hold. Already introduced: nothing is created.
  set_npc_flag      {npc, flag, value}
  increment_npc_flag {npc, flag, amount?}
  set_npc_status    {npc, status, cause?}       an immortal character cannot be made deceased
  link_npc          {npc, kind, other, role?}   role: source (default) | target; for parent or mentor links

Awareness and personal relationships (awareness.py). These are the ONLY ways an NPC becomes an acquaintance: being
mentioned, spawned or linked never does. The character must already be introduced (spawn_npc or reveal_npc earlier in
the same outcome), like every other effect here. A refusal (they are hostile, dead...) is not an error: the effect
reports `changed: false` and a `reason`, so one outcome can be written for every case.
  reveal_npc            {npc}                       the player has heard of / met them. Introduces them if needed (the
                                                    same rules as spawn_npc) and makes them a Notable Figure. No relationship
  establish_acquaintance {npc, kind?, closeness?}   they have personally met and now have a relationship. kind: friend |
                                                    partner | rival (optional link). Refused if hostile, refusing contact or dead
  change_relationship   {npc, amount}               improve (+) or damage (-) an existing acquaintance, 0-100
  set_npc_stance        {npc, stance}               hostile | refused: ends any acquaintance, blocks a new one. open: restores
  end_relationship      {npc}                       ends the acquaintance and its friend/partner/rival/mentor links. The
                                                    character, family links and Notable status stay

Conditions to branch on them:  npc_acquainted {npc}, npc_closeness_at_least {npc, value}, npc_stance {npc, stance}.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .. import named
from .. import awareness
from ..known import is_recorded
from ..relationships import SYMMETRIC, RelationshipError, RelationshipKind, player_ref, relationships_of
from .conditions import conditions, evaluate
from .context import StoryContext
from .effects import effects
from .errors import StoryEffectError
from .registry import StrictParams
from .scalars import Scalar, is_number, same_value

PLAYER = "player"
STATUS = Literal["active", "deceased", "retired"]

# type name -> the params that hold definition ids, so the content validator can check every reference
NPC_REFERENCES: dict[str, tuple[str, ...]] = {
    "npc_exists": ("npc",), "npc_status": ("npc",), "npc_flag_equals": ("npc",), "npc_flag_at_least": ("npc",),
    "npc_related": ("npc", "other"), "spawn_npc": ("npc",), "set_npc_flag": ("npc",),
    "increment_npc_flag": ("npc",), "set_npc_status": ("npc",), "link_npc": ("npc", "other"),
    "npc_acquainted": ("npc",), "npc_closeness_at_least": ("npc",), "npc_stance": ("npc",),
    "reveal_npc": ("npc",), "establish_acquaintance": ("npc",), "change_relationship": ("npc",),
    "set_npc_stance": ("npc",), "end_relationship": ("npc",),
}


class NpcParams(StrictParams):
    npc: str = Field(min_length=1)


class NpcStatusParams(NpcParams):
    status: STATUS


class NpcRelatedParams(NpcParams):
    kind: RelationshipKind
    other: str = Field(min_length=1)
    role: Literal["source", "target", "either"] = "either"


class NpcFlagEqualsParams(NpcParams):
    flag: str = Field(min_length=1)
    value: Scalar


class NpcFlagAtLeastParams(NpcParams):
    flag: str = Field(min_length=1)
    value: int | float


class SetNpcFlagParams(NpcParams):
    flag: str = Field(min_length=1, max_length=64)
    value: Scalar


class IncrementNpcFlagParams(NpcParams):
    flag: str = Field(min_length=1, max_length=64)
    amount: int | float = 1


class SetNpcStatusParams(NpcStatusParams):
    cause: str | None = None


class LinkNpcParams(NpcParams):
    kind: RelationshipKind
    other: str = Field(min_length=1)
    role: Literal["source", "target"] = "source"


class EstablishParams(NpcParams):
    kind: Literal["friend", "partner", "rival"] | None = None
    closeness: int | None = Field(default=None, ge=awareness.MIN_CLOSENESS, le=awareness.MAX_CLOSENESS)


class ChangeRelationshipParams(NpcParams):
    amount: int = Field(ge=-awareness.MAX_CLOSENESS, le=awareness.MAX_CLOSENESS)


class StanceParams(NpcParams):
    stance: Literal["open", "hostile", "refused"]


class ClosenessParams(NpcParams):
    value: int = Field(ge=awareness.MIN_CLOSENESS, le=awareness.MAX_CLOSENESS)


def _ref(other: str, ctx: StoryContext) -> str:
    """`player` is whoever the player is playing now; anything else is another definition's character."""
    return player_ref(ctx.record) if other == PLAYER else named.runtime_id(other)


def _npc(ctx: StoryContext, definition_id: str):
    return named.get_instance(ctx.session, ctx.campaign_id, definition_id)


# ---------------------------------------------------------------- conditions

@conditions.register("npc_exists", NpcParams)
def _npc_exists(p: NpcParams, ctx: StoryContext) -> bool:
    """The named character has been introduced in this game (alive or not)."""
    return _npc(ctx, p.npc) is not None


@conditions.register("npc_status", NpcStatusParams)
def _npc_status(p: NpcStatusParams, ctx: StoryContext) -> bool:
    """The named character exists and has this lifecycle status."""
    npc = _npc(ctx, p.npc)
    return npc is not None and npc.status.value == p.status


@conditions.register("npc_related", NpcRelatedParams)
def _npc_related(p: NpcRelatedParams, ctx: StoryContext) -> bool:
    """The named character exists and is linked to `other` (a definition id, or `player`) by `kind`."""
    if _npc(ctx, p.npc) is None:
        return False
    me, other = named.runtime_id(p.npc), _ref(p.other, ctx)
    role = "either" if p.kind in SYMMETRIC else p.role
    for link in relationships_of(ctx.session, ctx.campaign_id, me, p.kind):
        ends = (link.source_id, link.target_id)
        if (role == "source" and ends == (me, other)) or (role == "target" and ends == (other, me)) \
                or (role == "either" and set(ends) == {me, other}):
            return True
    return False


@conditions.register("npc_flag_equals", NpcFlagEqualsParams)
def _npc_flag_equals(p: NpcFlagEqualsParams, ctx: StoryContext) -> bool:
    """The named character's flag is set and equals value (type-aware: true is not 1)."""
    npc = _npc(ctx, p.npc)
    return npc is not None and p.flag in npc.flags and same_value(npc.flags[p.flag], p.value)


@conditions.register("npc_flag_at_least", NpcFlagAtLeastParams)
def _npc_flag_at_least(p: NpcFlagAtLeastParams, ctx: StoryContext) -> bool:
    """The named character exists and their numeric flag is at least value (unset counts as 0)."""
    npc = _npc(ctx, p.npc)
    if npc is None:
        return False
    current = npc.flags.get(p.flag, 0)
    return is_number(current) and current >= p.value


def _personal(p: NpcParams, ctx: StoryContext) -> awareness.PersonalState:
    npc = _npc(ctx, p.npc)
    return awareness.PersonalState() if npc is None else awareness.state_of(
        ctx.session, ctx.campaign_id, player_ref(ctx.record), npc.id)


@conditions.register("npc_acquainted", NpcParams)
def _npc_acquainted(p: NpcParams, ctx: StoryContext) -> bool:
    """The player has an established relationship (is an acquaintance) with the named character."""
    return _personal(p, ctx).acquainted


@conditions.register("npc_closeness_at_least", ClosenessParams)
def _npc_closeness_at_least(p: ClosenessParams, ctx: StoryContext) -> bool:
    """They are an acquaintance and closeness is at least value."""
    st = _personal(p, ctx)
    return st.acquainted and (st.closeness or 0) >= p.value


@conditions.register("npc_stance", StanceParams)
def _npc_stance(p: StanceParams, ctx: StoryContext) -> bool:
    """The character exists and their contact stance toward the player is open | hostile | refused."""
    return _npc(ctx, p.npc) is not None and _personal(p, ctx).stance == p.stance


# ---------------------------------------------------------------- effects

def _guarded(fn):
    try:
        return fn()
    except (named.NamedError, RelationshipError) as e:
        raise StoryEffectError(str(e)) from None


@effects.register("spawn_npc", NpcParams)
def _spawn_npc(p: NpcParams, ctx: StoryContext) -> dict:
    """Introduce a named character. A character who already exists is not created again. A new one is created only
    if the definition's `encounter.conditions` (if any) hold, so authors decide when each character may appear."""
    d = named.definitions().get(p.npc)
    if d is None:
        raise StoryEffectError(f"no named character '{p.npc}' is defined")
    if _npc(ctx, p.npc) is None and d.encounter.conditions is not None and not evaluate(d.encounter.conditions, ctx):
        return {"type": "spawn_npc", "npc": p.npc, "spawned": False, "reason": "encounter conditions not met"}
    inst = _guarded(lambda: named.instantiate(ctx.session, ctx.campaign_id, p.npc, ct=ctx.content))
    return {"type": "spawn_npc", "npc": p.npc, "spawned": inst.created, "reason": None if inst.created else "already exists"}


@effects.register("set_npc_flag", SetNpcFlagParams)
def _set_npc_flag(p: SetNpcFlagParams, ctx: StoryContext) -> dict:
    """Set a story flag on a named character."""
    _guarded(lambda: named.set_flag(ctx.session, ctx.campaign_id, p.npc, p.flag, p.value))
    return {"type": "set_npc_flag", "npc": p.npc, "flag": p.flag, "value": p.value}


@effects.register("increment_npc_flag", IncrementNpcFlagParams)
def _increment_npc_flag(p: IncrementNpcFlagParams, ctx: StoryContext) -> dict:
    """Add to a numeric flag on a named character (unset starts at 0; a non-numeric flag is an error)."""
    npc = _guarded(lambda: named.increment_flag(ctx.session, ctx.campaign_id, p.npc, p.flag, p.amount))
    return {"type": "increment_npc_flag", "npc": p.npc, "flag": p.flag, "value": npc.flags[p.flag]}


@effects.register("set_npc_status", SetNpcStatusParams)
def _set_npc_status(p: SetNpcStatusParams, ctx: StoryContext) -> dict:
    """Change a named character's lifecycle status. An immortal one cannot be killed; the dead stay dead."""
    _guarded(lambda: named.set_status(ctx.session, ctx.campaign_id, p.npc, p.status, p.cause))
    return {"type": "set_npc_status", "npc": p.npc, "status": p.status}


@effects.register("link_npc", LinkNpcParams)
def _link_npc(p: LinkNpcParams, ctx: StoryContext) -> dict:
    """Link a named character to `other` (a definition id, or `player`). Repeating it is harmless."""
    _guarded(lambda: named.link(ctx.session, ctx.campaign_id, p.npc, p.kind, _ref(p.other, ctx), self_is=p.role))
    return {"type": "link_npc", "npc": p.npc, "kind": p.kind.value, "other": p.other}


# ---------------------------------------------------------------- awareness and personal relationships

def _register(p: NpcParams, ctx: StoryContext, effect: str, call) -> dict:
    """Run an awareness.py function for an INTRODUCED named character and describe what happened."""
    npc = _guarded(lambda: _npc(ctx, p.npc))
    if npc is None:
        raise StoryEffectError(f"cannot {effect}: '{p.npc}' has not been introduced (spawn_npc or reveal_npc first)")
    try:
        reg = call(npc.id, player_ref(ctx.record))
    except awareness.AwarenessError as e:
        raise StoryEffectError(str(e)) from None
    return {"type": effect, "npc": p.npc, "changed": reg.changed, "reason": reg.reason, "acquainted": reg.acquainted,
            "closeness": reg.closeness, "stance": reg.stance}


@effects.register("reveal_npc", NpcParams)
def _reveal_npc(p: NpcParams, ctx: StoryContext) -> dict:
    """The player hears of or meets a character: they become a Notable Figure. Introduces them if they are not yet in
    the game (only when the definition's `encounter.conditions` hold). Creates no relationship."""
    d = named.definitions().get(p.npc)
    if d is None:
        raise StoryEffectError(f"no named character '{p.npc}' is defined")
    if _npc(ctx, p.npc) is None and d.encounter.conditions is not None and not evaluate(d.encounter.conditions, ctx):
        return {"type": "reveal_npc", "npc": p.npc, "changed": False, "reason": "encounter conditions not met"}
    was_aware = is_recorded(ctx.session, ctx.campaign_id, named.runtime_id(p.npc))
    _guarded(lambda: named.instantiate(ctx.session, ctx.campaign_id, p.npc, ct=ctx.content))   # introducing them records awareness
    out = _register(p, ctx, "reveal_npc", lambda npc_id, player: awareness.reveal(ctx.session, ctx.campaign_id, player, npc_id))
    out["changed"], out["reason"] = not was_aware, "already aware" if was_aware else None       # did THIS outcome make them known
    return out


@effects.register("establish_acquaintance", EstablishParams)
def _establish_acquaintance(p: EstablishParams, ctx: StoryContext) -> dict:
    """The player has personally met this character and now has a relationship with them (an Acquaintance). Only this
    outcome makes one. Repeating it changes nothing; hostile, refusing or dead characters are not befriended."""
    return _register(p, ctx, "establish_acquaintance", lambda npc_id, player: awareness.establish_acquaintance(
        ctx.session, ctx.campaign_id, player, npc_id, kind=p.kind, closeness=p.closeness, source="story"))


@effects.register("change_relationship", ChangeRelationshipParams)
def _change_relationship(p: ChangeRelationshipParams, ctx: StoryContext) -> dict:
    """Improve (positive amount) or damage (negative) an existing acquaintance. Not an acquaintance: nothing changes."""
    return _register(p, ctx, "change_relationship", lambda npc_id, player: awareness.change_closeness(
        ctx.session, ctx.campaign_id, player, npc_id, p.amount))


@effects.register("set_npc_stance", StanceParams)
def _set_npc_stance(p: StanceParams, ctx: StoryContext) -> dict:
    """hostile or refused: the character turns on the player or will not see them again. Any acquaintance ends and none
    can be made. They never become an acquaintance this way. open: restores contact."""
    return _register(p, ctx, "set_npc_stance", lambda npc_id, player: awareness.set_stance(
        ctx.session, ctx.campaign_id, player, npc_id, p.stance))


@effects.register("end_relationship", NpcParams)
def _end_relationship(p: NpcParams, ctx: StoryContext) -> dict:
    """End the personal relationship with an acquaintance. The character is not deleted; family and Notable status stay."""
    return _register(p, ctx, "end_relationship", lambda npc_id, player: awareness.end_relationship(
        ctx.session, ctx.campaign_id, player, npc_id))
