# Power

Power is a derived rating of how strong a character is. It is **never stored**: it is recalculated from the
character's current state on every request, so it cannot go stale. Code: `backend/app/power.py`.
Configuration: `backend/app/data/power.yaml`. API: `GET /api/power`. UI: the Power tab.

    Final Power = (Stats Power + Equipment Power + Skill Power) × (1 + Total Modifier)

Components and the final value are rounded to 2 decimals. Modifiers are decimals (`0.10` = +10%).

## Base stats vs specialised stats

**Base stats** (`strength`, `dexterity`, `constitution`, `wisdom`, `charisma`) **never contribute to Power**, directly
or through any weighting. They stay what they were: inputs to event checks, encounters and requirements (for example a
Strength check that unlocks a choice about a heavy rock). Nothing about them changed.

**Specialised stats** are combat proficiencies. Only these feed Stats Power.

| Specialised stat | Purpose | Weight | Value comes from (the skill) |
|---|---|---:|---|
| Swordsmanship | Sword-based combat | 1.0 | `swordsmanship` |
| Marksmanship | Ranged combat, bows, crossbows and similar weapons | 1.0 | `archery` |
| Magic | Magical combat and spellcasting | 1.0 | `magic` |
| Martial Arts | Unarmed combat, fists and physical fighting techniques | 1.0 | `martial_arts` |

They are kept separate from base stats in the data model: base stats are `Character.stats`, the specialised stats are
read from the character's skill levels (`Character.skills`). So **no new character field, column or migration was
needed**, saved characters are unchanged, and the stats are trained by the game's existing actions and events.
Marksmanship is the display name; the underlying skill id stays `archery` so saved data and existing content keep working.

**Magic is one skill.** The old schools of magic (`elemental_magic`, `spirit_magic`, `necromancy`, `enchantment`) no
longer exist. Saved characters are migrated when they load (`rules.migrate_skills`): the schools' levels are *added*
into `magic`, exactly as Power used to total them, so Magic and Power are unchanged. Spells that add Power directly may
come later; they are not part of this system yet.

## Components

**Stats Power** = Swordsmanship + Marksmanship + Magic + Martial Arts, each at weight 1.0 (1:1). A character with 100
Swordsmanship and 100 Magic has 200 Stats Power, whatever their base stats are. All four stats are always listed in the breakdown, even at 0.

**Equipment Power** = sum of raw Power of equipped items. **There is no equipment system yet** (items are inventory
counts; nothing is "equipped"), so this is `0`, and an item sitting in the inventory never counts.

**Skill Power** = sum of raw Power for each *unlocked* skill (level > 0) listed in `power.yaml` `skill_power`
(flat, per skill id). The list is empty, because no skill grants Power yet, so this is `0`.

**Total Modifier** = the sum of all percentage modifiers: `+10%` and `+20%` make `+30%`, not `1.1 × 1.2`.
Today the only source is a trait's optional `modifiers.power` (a decimal) in `traits.yaml`; no shipped trait uses it.
The multiplier `1 + Total Modifier` is held at a minimum of 0, so Power is never negative.

## No double counting

* Base stats, and the bonuses traits give them, are not in Power at all.
* A skill may feed **one** specialised stat, and a skill that feeds a specialised stat cannot also be in `skill_power`.
  `power.yaml` that breaks this is rejected by `python -m app.validate_content`.
* A trait's **`power` modifier** is counted once in Total Modifier only. Each trait counts once even if listed twice.
* Each source (`source_type`, `source_id`) may appear once per component. Repeats from a provider are dropped, and the
  breakdown's `notes` say so.
* Inventory items are not equipment.

## The breakdown (API response)

`PowerBreakdown`: `total`, `subtotal`, `stats` / `equipment` / `skills` (each: `total`, `implemented`, `note`, `lines`),
`modifiers` (`total`, `multiplier`, `percent_text`, `lines`), `formula` (`general`, `substituted`, `result`), `notes`.
Each line carries `source_type`, `source_id`, `label`, `value`, `detail`, `inputs` (the numbers behind it) and `sources`.
The frontend renders these fields as they are and does no Power arithmetic; a test checks that the TypeScript types
match the backend model.

## Extending

* **New specialised stat** (for example Vampire Blood or Demon Soul, not implemented yet): add an entry under
  `specialised_stats` in `power.yaml` with a name, weight and the skills that make up its value. No code.
* **Skills that grant a flat Power bonus:** add `skill_id: amount` under `skill_power`. No code.
* **Different weight:** change `weight` in `power.yaml`.
* **Equipment, new modifier sources, or a stat not built from skills:** each part of the formula comes from a provider,
  `(character, content, config) -> list[PowerLine]`, in `PowerProviders` (`stats`, `equipment`, `skills`, `modifiers`).
  Replace or add one; the formula, rounding, de-duplication and breakdown do not change.

## Not implemented

Combat, victory odds, defeat damage, fleeing, equipment or skill management screens, and any Power content.
