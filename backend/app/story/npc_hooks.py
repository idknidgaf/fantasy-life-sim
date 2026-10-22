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
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .. import named
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
