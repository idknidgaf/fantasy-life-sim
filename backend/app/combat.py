"""Combat maths: win probability, defeat severity, flee chance and risk warnings. Pure calculation: no database,
no character, no UI. The inputs are two *raw Power* numbers, taken from `power.calculate_power`; this module never
computes Power itself. See docs/COMBAT.md for the reasoning and the tables.

Three separate questions, answered separately on purpose:

  1. WHO WINS?        `win_probability(a, b)`      one shared normalisation of both Powers -> a probability.
  2. WHAT DOES LOSING COST?  `defeat_severity(loser, winner)`   the raw Power ratio -> a damage band.
  3. CAN I GET AWAY?  `flee_chance(a, b)`          the same shape as (1), configurable in combat.yaml.

(1) never looks at damage and (2) never looks at the probability, so tuning one cannot change the other.

Zero and extreme Power
----------------------
Power is uncapped, and a new character has 0. Both Powers are first passed through `effective_power`: anything
below `power_floor` (default 1) counts as `power_floor`; NaN and negatives count as the floor; infinity counts as
the largest finite float. Everything downstream uses the effective values, so there is no division by zero, and
two Power-0 fighters are simply an even match. Probabilities are computed from the *log* of the ratio, so
Powers like 1e300 cannot overflow.
"""
from __future__ import annotations

import math
import random
import sys
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .content import DATA_DIR, ContentError

COMBAT_FILE = DATA_DIR / "combat.yaml"

# Defeat damage is a percentage of MAXIMUM health. `D` is the loser's disadvantage: winner Power / loser Power
# (both effective). Bands are half-open, [lower, next lower): a ratio exactly on a boundary belongs to the band
# that starts there. Compared as exact fractions, so 3.9 is 3.9 and not 3.8999999999999999.
#
#   D >= 10      lethal   100%  instant death
#   D >= 3.9     severe    90%
#   D >= 3       heavy     50%
#   D >= 2       minor    random whole number 5-10%
#   D >= 1.5     minor      5%
#   D <  1.5     minor      1%     (equal, a small disadvantage, or the loser was actually the stronger one)
SEVERITY_BANDS: tuple[tuple[Fraction, str, int, int, bool], ...] = (
    (Fraction(10), "lethal", 100, 100, True),
    (Fraction("3.9"), "severe", 90, 90, False),
    (Fraction(3), "heavy", 50, 50, False),
    (Fraction(2), "minor", 5, 10, False),
    (Fraction("1.5"), "minor", 5, 5, False),
    (Fraction(0), "minor", 1, 1, False),
)

CAUTION_BELOW = 0.5       # yellow: estimated win probability is below this
DANGER_AT_PERCENT = 50    # red: estimated defeat damage is at least this many percent of max health

Warning = Literal["none", "caution", "danger"]


# ---------------------------------------------------------------- configuration (data/combat.yaml)

class FleeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sensitivity: float = Field(default=1.0, gt=0)     # how sharply the odds follow the Power ratio
    bias: float = Field(default=1.0, gt=0)            # multiplies YOUR Power when fleeing (>1: easier to flee than to fight)
    min_chance: float = Field(default=0.05, gt=0, le=1)
    max_chance: float = Field(default=0.95, gt=0, le=1)

    @model_validator(mode="after")
    def _ordered(self):
        if self.min_chance > self.max_chance:
            raise ValueError("min_chance is greater than max_chance")
        return self


class CombatConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")          # a typo'd key is an error, not silently ignored
    power_floor: float = Field(default=1.0, gt=0)
    sensitivity: float = Field(default=1.0, gt=0)
    win_chance_floor: float = Field(default=0.01, gt=0, lt=0.5)
    flee: FleeConfig = Field(default_factory=FleeConfig)


def load_combat_config(path: Path = COMBAT_FILE) -> CombatConfig:
    """Load and validate combat.yaml. A missing file means the defaults."""
    raw: object = {}
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as e:
            raise ContentError([f"{path.name}: could not be read: {e}"])
    try:
        return CombatConfig.model_validate(raw)
    except ValidationError as e:
        raise ContentError([f"{path.name}: {'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()])


@lru_cache
def get_combat_config() -> CombatConfig:
    return load_combat_config()


# ---------------------------------------------------------------- normalisation and probability

def effective_power(raw: float, cfg: CombatConfig | None = None) -> float:
    """The Power used for every combat calculation: raw Power, but never below the floor and always finite."""
    cfg = cfg or get_combat_config()
    value = float(raw)
    if math.isnan(value) or value < cfg.power_floor:
        return cfg.power_floor
    if math.isinf(value):
        return sys.float_info.max
    return value


def _relative_chance(own: float, other: float, sensitivity: float) -> float:
    """own^k / (own^k + other^k), computed from the log-ratio so huge Powers cannot overflow."""
    x = sensitivity * (math.log(other) - math.log(own))
    return 0.0 if x >= 700 else 1.0 / (1.0 + math.exp(x))


def win_probability(player_power: float, opponent_power: float, cfg: CombatConfig | None = None) -> float:
    """Probability that the player wins.

    Both Powers are normalised TOGETHER: P(win) = a^k / (a^k + b^k) with a, b the effective Powers and k the
    sensitivity (1 by default, so it is simply a / (a + b)). Only the ratio matters, so the method is
    scale-free and works for any Power. Properties:

      * equal Power -> 0.5;  10x the opponent -> ~0.91;  mirror images sum to exactly 1
      * strictly increasing in the player's Power and decreasing in the opponent's
      * clamped to [win_chance_floor, 1 - win_chance_floor] (1%..99%): the stronger side is never certain
        to win and the weaker side always has a small chance
    """
    cfg = cfg or get_combat_config()
    p = _relative_chance(effective_power(player_power, cfg), effective_power(opponent_power, cfg), cfg.sensitivity)
    return min(1.0 - cfg.win_chance_floor, max(cfg.win_chance_floor, p))


def flee_chance(player_power: float, opponent_power: float, cfg: CombatConfig | None = None,
                modifier: float = 0.0) -> float:
    """Probability that fleeing succeeds. The same shape as `win_probability`, tuned by the `flee` block of
    combat.yaml (sensitivity, bias, min/max), plus an optional per-encounter `modifier` (added to the
    probability, so +0.2 means 20 points easier). It falls as the opponent's advantage grows and is never below
    `flee.min_chance`: fleeing is always possible."""
    cfg = cfg or get_combat_config()
    f = cfg.flee
    p = _relative_chance(effective_power(player_power, cfg) * f.bias, effective_power(opponent_power, cfg), f.sensitivity)
    return min(f.max_chance, max(f.min_chance, p + modifier))


def percent_floor(probability: float) -> int:
    """A probability as a whole percent, rounded DOWN so a displayed figure never overstates the odds."""
    return math.floor(round(probability * 100, 6))


# ---------------------------------------------------------------- defeat severity

@dataclass(frozen=True)
class DefeatSeverity:
    """What losing costs, as a percentage of the loser's maximum health."""
    band: Literal["minor", "heavy", "severe", "lethal"]
    min_percent: int
    max_percent: int
    instant_death: bool
    ratio: float                 # the disadvantage ratio D that selected the band (winner / loser, effective)

    def roll_percent(self, rng: random.Random) -> int:
        """The damage percent for an actual defeat. Only the minor 5-10% band is random."""
        return self.min_percent if self.min_percent == self.max_percent else rng.randint(self.min_percent, self.max_percent)

    @property
    def label(self) -> str:
        if self.instant_death:
            return "Estimated defeat: instant death (-100% HP)"
        if self.min_percent == self.max_percent:
            return f"Estimated defeat: -{self.min_percent}% HP"
        return f"Estimated defeat: -{self.min_percent}% to -{self.max_percent}% HP"


def _exact(x: float) -> Fraction:
    return Fraction(repr(float(x)))


def defeat_severity(loser_power: float, winner_power: float, cfg: CombatConfig | None = None) -> DefeatSeverity:
    """Damage the LOSER takes, from the ratio of the winner's raw Power to the loser's (floored, see module doc).
    Independent of how likely the defeat was. A loser who was actually the stronger side (an upset) has a ratio
    below 1 and takes the smallest band."""
    cfg = cfg or get_combat_config()
    ratio = _exact(effective_power(winner_power, cfg)) / _exact(effective_power(loser_power, cfg))
    for lower, band, lo, hi, instant in SEVERITY_BANDS:
        if ratio >= lower:
            return DefeatSeverity(band, lo, hi, instant, round(float(ratio), 4))
    raise AssertionError("unreachable: the last band starts at 0")


def damage_hp(max_health: int, percent: int) -> int:
    """Hit points for a damage percent: rounded up, and at least 1 whenever the percent is above 0."""
    return 0 if percent <= 0 else max(1, math.ceil(max_health * percent / 100))


# ---------------------------------------------------------------- pre-battle warning

def warning_level(win_prob: float, severity: DefeatSeverity) -> Warning:
    """Red (danger) beats yellow (caution): red when losing would cost 50% of max health or more, yellow when
    the estimated win probability is below 50%. Both come from the resolver's own numbers."""
    if severity.min_percent >= DANGER_AT_PERCENT:
        return "danger"
    if win_prob < CAUTION_BELOW:
        return "caution"
    return "none"


def warning_text(level: Warning, severity: DefeatSeverity) -> str | None:
    """Readable text for the warning, so colour is never the only signal."""
    if level == "danger":
        return ("Deadly: losing means instant death" if severity.instant_death
                else "Dangerous: losing costs 50% or more of your health")
    if level == "caution":
        return "Risky: your estimated win chance is under 50%"
    return None
