"""One fight, end to end, without a database: set up the matchup, preview it, roll it, and describe what happened.

`combat.py` holds the maths; this module connects it to characters and content:

  * `matchup`        both Powers from the existing Power system (`power.calculate_power`), then the resolver's
                     win probability, defeat severity and flee chance.
  * `preview`        what the encounter window shows BEFORE the player chooses (warnings, odds, flee chance).
  * `roll_battle`    the dice: flee attempt, then the fight, then the damage percent, each from its own seeded stream.
  * `snapshot` + `build_summary`
                     the battle summary. Rewards and consequences are the DIFFERENCE between the character before
                     and after the real game systems ran, so the summary can only ever show what was really granted.

Applying the result (damage, death, effects) is done by the callers that own those systems: `engine.resolve_event`
for legacy events and `story.service` for story events. Both use the helpers here, so they behave identically.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from . import combat
from .character import Character
from .combat import CombatConfig, DefeatSeverity
from .content import CombatDef, Content, FleeDef, OpponentDef
from .power import calculate_power
from .rules import max_health, pretty

FLEE_SUFFIX = ":flee"            # the generated Flee option of a combat choice is "<choice id>:flee"

Outcome = Literal["victory", "defeat", "fled"]


# ---------------------------------------------------------------- API shapes

class DefeatEstimate(BaseModel):
    band: str
    min_percent: int
    max_percent: int
    instant_death: bool
    label: str                          # e.g. "Estimated defeat: -50% HP"


class CombatPreview(BaseModel):
    """Everything the encounter window shows before Fight / Flee. Every figure comes from the combat resolver."""
    opponent_name: str
    player_power: float
    opponent_power: float
    win_probability: float              # an ESTIMATE of the coming fight, 0..1
    win_percent: int                    # the same, as a whole percent rounded down
    defeat: DefeatEstimate
    warning: Literal["none", "caution", "danger"]     # caution = yellow, danger = red (red wins)
    warning_label: str | None           # readable text for the warning: colour is never the only signal
    flee_available: bool
    flee_chance: float | None
    flee_percent: int | None


class BattleLine(BaseModel):
    kind: Literal["stat", "skill", "item", "trait", "stress", "happiness"]
    text: str                           # ready to display, e.g. "+1 Strength"


class BattlePower(BaseModel):
    player: float
    opponent: float


class FleeResult(BaseModel):
    chance: float                       # the chance that was rolled against
    succeeded: bool


class BattleDamage(BaseModel):
    percent: int                        # percent of maximum health the defeat inflicted
    health_lost: int
    health_before: int
    health_after: int
    max_health: int
    instant_death: bool


class BattleSummary(BaseModel):
    """The authoritative record of one resolved fight. The frontend renders it and calculates nothing."""
    id: int | None = None               # the stored record (set once saved)
    source: Literal["legacy", "story"] = "legacy"
    event_id: str
    event_title: str
    opponent_name: str
    action: Literal["fight", "flee"]    # what the player chose
    outcome: Outcome                    # how it ended: victory | defeat | fled
    flee: FleeResult | None = None      # present when the player tried to flee (failed or not)
    result_text: str
    power: BattlePower
    estimated_win_probability: float    # the PRE-battle estimate. It is not "the chance this fight was won".
    estimated_win_percent: int
    damage: BattleDamage | None = None  # only for a defeat
    died: bool = False
    cause_of_death: str | None = None
    other_consequences: list[BattleLine] = []
    rewards: list[BattleLine] = []


# ---------------------------------------------------------------- the matchup

def opponent_power(opponent: OpponentDef, ct: Content) -> float:
    """The opponent's raw Power from the SAME calculation as the player's: it is a fighter with these skills and traits."""
    fighter = Character(name=opponent.name, skills=dict(opponent.skills), traits=list(opponent.traits))
    return calculate_power(fighter, ct).total


@dataclass(frozen=True)
class Matchup:
    opponent: OpponentDef
    flee: FleeDef
    player_power: float                 # raw Power, as the Power system reports it
    opponent_power: float
    win_probability: float
    defeat: DefeatSeverity              # what the PLAYER loses if beaten
    flee_chance: float


def matchup(c: Character, ct: Content, combat_def: CombatDef, cfg: CombatConfig | None = None) -> Matchup:
    cfg = cfg or combat.get_combat_config()
    player = calculate_power(c, ct).total
    foe = opponent_power(combat_def.opponent, ct)
    return Matchup(
        opponent=combat_def.opponent, flee=combat_def.flee, player_power=player, opponent_power=foe,
        win_probability=combat.win_probability(player, foe, cfg),
        defeat=combat.defeat_severity(loser_power=player, winner_power=foe, cfg=cfg),
        flee_chance=combat.flee_chance(player, foe, cfg, combat_def.flee.modifier))


def preview(m: Matchup) -> CombatPreview:
    level = combat.warning_level(m.win_probability, m.defeat)
    return CombatPreview(
        opponent_name=m.opponent.name, player_power=m.player_power, opponent_power=m.opponent_power,
        win_probability=m.win_probability, win_percent=combat.percent_floor(m.win_probability),
        defeat=DefeatEstimate(band=m.defeat.band, min_percent=m.defeat.min_percent, max_percent=m.defeat.max_percent,
                              instant_death=m.defeat.instant_death, label=m.defeat.label),
        warning=level, warning_label=combat.warning_text(level, m.defeat),
        flee_available=m.flee.allowed,
        flee_chance=m.flee_chance if m.flee.allowed else None,
        flee_percent=combat.percent_floor(m.flee_chance) if m.flee.allowed else None)


# ---------------------------------------------------------------- the dice

@dataclass(frozen=True)
class Rngs:
    flee: random.Random
    fight: random.Random
    damage: random.Random


def rngs_for(key: str) -> Rngs:
    """Three independent, reproducible streams for one encounter, so the flee roll, the fight roll and the damage
    roll never shift each other."""
    return Rngs(random.Random(f"{key}:flee"), random.Random(f"{key}:fight"), random.Random(f"{key}:damage"))


@dataclass(frozen=True)
class Roll:
    flee_attempted: bool
    fled: bool                          # the flee attempt succeeded: the encounter ends here
    flee_chance: float | None
    won: bool | None                    # None when the player fled
    damage_percent: int                 # 0 unless the player lost
    instant_death: bool

    @property
    def outcome(self) -> Outcome:
        return "fled" if self.fled else ("victory" if self.won else "defeat")


def roll_battle(m: Matchup, *, flee: bool, rngs: Rngs) -> Roll:
    """Resolve the player's choice. Flee: one flee roll; on success the encounter is over with no consequences.
    A FAILED flee is not a second penalty: the player is caught and the encounter resolves exactly like a fight
    (same win probability, same defeat damage). Damage is only rolled for a defeat."""
    if flee:
        if not m.flee.allowed:
            raise ValueError("this encounter cannot be fled")
        if rngs.flee.random() < m.flee_chance:
            return Roll(True, True, m.flee_chance, None, 0, False)
    won = rngs.fight.random() < m.win_probability
    percent = 0 if won else m.defeat.roll_percent(rngs.damage)
    return Roll(flee, False, m.flee_chance if flee else None, won, percent, bool(not won and m.defeat.instant_death))


def damage_for(max_hp: int, roll: Roll) -> int:
    """Hit points the defeat takes: all of them for an instant death, otherwise the percent of maximum health."""
    return max_hp if roll.instant_death else combat.damage_hp(max_hp, roll.damage_percent)


def result_text(roll: Roll, flee: FleeDef, outcome_result: str | None) -> str:
    """The narrative line: the flee text when relevant, then the encounter's own result for the outcome."""
    if roll.fled:
        return flee.success_text
    default = "You win the fight." if roll.won else "You are beaten."
    parts = [flee.failure_text] if roll.flee_attempted else []
    parts.append(outcome_result or default)
    return " ".join(parts)


# ---------------------------------------------------------------- the summary

@dataclass(frozen=True)
class Snapshot:
    stats: dict[str, int]
    skills: dict[str, float]
    items: dict[str, int]
    traits: tuple[str, ...]
    health: int
    max_health: int
    stress: float
    happiness: float


def snapshot(c: Character, ct: Content) -> Snapshot:
    return Snapshot(dict(c.stats), dict(c.skills), dict(c.items), tuple(c.traits),
                    c.health if c.health is not None else max_health(c, ct), max_health(c, ct),
                    c.stress, c.happiness)


def _signed(x: float) -> str:
    return f"{x:+.2f}".rstrip("0").rstrip(".")


def diff_lines(before: Snapshot, after: Snapshot, ct: Content) -> tuple[list[BattleLine], list[BattleLine]]:
    """(rewards, other consequences): what the game systems really changed. A stat already at its cap shows no
    gain because nothing was gained. Rewards are stat, skill and item gains and new traits; everything else that
    got worse or merely changed (losses, stress, happiness) is a consequence."""
    rewards: list[BattleLine] = []
    other: list[BattleLine] = []

    def put(delta: float, kind: str, label: str) -> None:
        if abs(delta) < 0.005:
            return
        (rewards if delta > 0 else other).append(BattleLine(kind=kind, text=f"{_signed(delta)} {label}"))

    for stat in sorted(after.stats):
        put(after.stats[stat] - before.stats.get(stat, 0), "stat", pretty(stat))
    for skill in sorted(set(before.skills) | set(after.skills)):
        put(round(after.skills.get(skill, 0.0) - before.skills.get(skill, 0.0), 2), "skill", pretty(skill))
    for item in sorted(set(before.items) | set(after.items)):
        name = ct.items[item].name if item in ct.items else pretty(item)
        put(after.items.get(item, 0) - before.items.get(item, 0), "item", name)
    for trait in after.traits:
        if trait not in before.traits:
            rewards.append(BattleLine(kind="trait", text=f"New trait: {ct.traits[trait].name if trait in ct.traits else pretty(trait)}"))
    for trait in before.traits:
        if trait not in after.traits:
            other.append(BattleLine(kind="trait", text=f"Lost trait: {ct.traits[trait].name if trait in ct.traits else pretty(trait)}"))
    stress, happiness = round(after.stress - before.stress), round(after.happiness - before.happiness)
    if stress:
        other.append(BattleLine(kind="stress", text=f"{stress:+d} stress"))
    if happiness:
        other.append(BattleLine(kind="happiness", text=f"{happiness:+d} happiness"))
    return rewards, other


def build_summary(*, source: Literal["legacy", "story"], event_id: str, event_title: str, m: Matchup, roll: Roll,
                  text: str, before: Snapshot, after: Character, health_lost: int, ct: Content) -> BattleSummary:
    """The summary of a resolved fight. `after` is the character once everything has been applied."""
    rewards, other = diff_lines(before, snapshot(after, ct), ct)
    died = not after.alive
    damage = None
    if roll.damage_percent > 0:
        damage = BattleDamage(percent=roll.damage_percent, health_lost=health_lost, health_before=before.health,
                              health_after=after.health if after.health is not None else 0,
                              max_health=before.max_health, instant_death=roll.instant_death)
    return BattleSummary(
        source=source, event_id=event_id, event_title=event_title, opponent_name=m.opponent.name,
        action="flee" if roll.flee_attempted else "fight", outcome=roll.outcome,
        flee=FleeResult(chance=roll.flee_chance, succeeded=roll.fled) if roll.flee_attempted else None,
        result_text=text, power=BattlePower(player=m.player_power, opponent=m.opponent_power),
        estimated_win_probability=m.win_probability, estimated_win_percent=combat.percent_floor(m.win_probability),
        damage=damage, died=died, cause_of_death=after.cause_of_death if died else None,
        other_consequences=other, rewards=[] if died else rewards)
