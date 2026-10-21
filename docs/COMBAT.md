# Combat

Combat resolves a fight between the player and an opponent using **raw Power** from the existing Power system
(`power.calculate_power`). Power is never recomputed or re-implemented here. Code: `backend/app/combat.py` (maths),
`backend/app/battle.py` (matchup, dice, summary), `backend/app/battle_store.py` (saved summaries),
`backend/app/engine.py` (applies the result). Config: `backend/app/data/combat.yaml`. UI: the existing encounter
window (`EventPanel`) and `BattleSummary`. Tests: `backend/tests/test_combat.py`.

Three questions are answered separately: **who wins** (probability), **what losing costs** (Power ratio), **can I get away** (flee).
Win probability never looks at damage, and damage never looks at probability.

## Normalisation and win probability

Both Powers are normalised **together**, never one at a time:

    P(win) = a^k / (a^k + b^k)        a, b = effective Powers, k = sensitivity (default 1, i.e. a / (a + b))

* **Effective Power**: raw Power below `power_floor` (default 1) counts as the floor. NaN and negatives count as the floor; infinity counts as the largest finite float.
* **Zero Power**: a new character (Power 0) is an ordinary fighter at the floor. Two Power-0 fighters are an even match (50%). No division by zero.
* **Extreme Power**: computed from the log of the ratio, so 1e300 cannot overflow. Only the ratio matters, so it is scale-free and suits uncapped Power.
* **Never certain**: the result is clamped to `[win_chance_floor, 1 - win_chance_floor]` (1%..99%). The weaker side always has a chance; the stronger never has a guarantee.
* Equal Power 50%; 10× Power ≈ 91%; mirror matchups sum to 1.

## Defeat damage (percent of **maximum** health, loser only)

`D` = winner's effective Power ÷ loser's effective Power. Bands are half-open and include their lower bound, compared as exact fractions (so exactly `3`, `3.9`, `10` fall in the higher band).

| D | Damage |
|---|---|
| ≥ 10 | 100% and **instant death** |
| ≥ 3.9 | 90% |
| ≥ 3 | 50% |
| ≥ 2 and < 3 | random whole number 5–10% |
| ≥ 1.5 and < 2 | 5% |
| < 1.5 (equal, small disadvantage, or an upset where the loser was stronger) | 1% |

Hit points = `ceil(max_health × percent / 100)`, at least 1. The winner takes no defeat damage. Damage and death go through
`engine.apply_battle_damage`: health drops via the normal vitals; 0 health or instant death sets `alive = False` and the cause of death, and the existing
life-cycle logs the death once. Combat outcome effects may not contain damage or `die` (rejected by content validation), so a defeat is never punished twice.

## Pre-battle warnings

Computed by the resolver, never by enemy-specific rules, and they never block Fight.

* **Red (danger)**: estimated defeat damage ≥ 50%. Label: "Deadly: losing means instant death" or "Dangerous: losing costs 50% or more of your health".
* **Yellow (caution)**: estimated win probability < 50%. Label: "Risky: your estimated win chance is under 50%".
* Red takes precedence. Each warning has an icon (☠ / ⚠) and text. The button also shows "Estimated win chance: N%" and "Estimated defeat: -N% HP".

## Fleeing

Every combat choice automatically gets a generated **Flee** option (`<choice id>:flee`) next to Fight, unless the encounter sets `flee.allowed: false`.

    flee chance = clamp(f_sens-shaped chance of (your Power × flee.bias) vs opponent, + encounter flee.modifier, flee.min_chance .. flee.max_chance)

It falls as the opponent's advantage grows, is never impossible (min 5%) or certain (max 95%), and is tunable in `combat.yaml` and per encounter (`flee.modifier`).

* **Success**: the encounter ends. No rewards, no damage, no outcome effects.
* **Failure**: the player is caught and the encounter resolves exactly like choosing Fight (same win probability, same defeat damage). There is no extra penalty on top.
* The flee roll, fight roll and damage roll use separate seeded streams, and the attempt and its result are stored in `CombatRecord`.

## Encounters

A choice with a `combat:` block (opponent, `victory`, `defeat`, optional `flee`) is resolved by combat instead of a stat check. The opponent is described by skills and
traits and its Power comes from the same Power calculation as the player's. `victory`/`defeat` hold only rewards and non-damage consequences.
Converted legacy events: the village bully, the wolf pack, the tournament, the bandits, and the pack-leader challenge.

## Battle summary

Resolution is one transaction: character, queue and a `CombatRecord` (the summary, stored as JSON) are saved together or not at all. Resolving again does nothing
(the event is no longer pending), so retries and reloads cannot repeat damage or rewards. The API returns the open summary in `character.battle_summary`
until the player presses **Continue** (`POST /api/battles/{id}/acknowledge`, idempotent). The encounter queue is untouched; the next encounter simply appears after Continue.

The summary shows: outcome (`Victory`, `Defeat`, `Fled Successfully`, `Flee Failed — Victory/Defeat`), Power comparison, the **estimated** pre-battle win chance (labelled as an estimate),
a **Consequences** section only if damage/death/other losses occurred, and a **Rewards** list (`No rewards gained` when empty). Rewards and consequences are the difference between the
character before and after the real game systems ran, so only what was actually granted is shown. The frontend renders them and calculates nothing.

## Not included

Story-engine combat content (the story library is empty; story events can adopt the same helpers later), enemy AI, equipment, battle animations or a separate battle screen.
