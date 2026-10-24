# NPC progression

Persistent NPCs start age-appropriately developed and keep growing as the world's years pass. Everything tunable lives in
`backend/app/data/npc_growth.yaml` (checked by `python -m app.validate_content`); the code is `progression.py` (pure rules)
and `npc_world.py` (the yearly tick over saved NPCs). Power is never changed here: it is calculated from the result by
the one Power system.

## Birth, initial development, yearly growth

| Step | What | When |
|---|---|---|
| Birth | Base stats rolled (or inherited) at newborn level | once, when the NPC is made |
| Initial development | Growth from birth up to the NPC's age, at `initial.chance_scale` (80%) of the yearly chance, species modifiers only | once, when an NPC is generated above age 0 |
| Yearly growth | One year of growth, traits/talent/experiences included | once per year the NPC lives |

`NpcSpec.stats` are exact final values (never developed, used by authored characters). `NpcSpec.birth_stats` are newborn
values that are developed to the age. `develop: false` gives a flat newborn-level roll.

## Growth rules

Each year, per base stat (strength, dexterity, constitution, wisdom, charisma):

1. Roll against the life-stage profile's `stat_chance`, times `stat_rates`, species, trait, talent and experience
   multipliers, times a taper near the cap.
2. On a hit, a rarer **breakthrough** (`breakthrough.chance`, a `percent` of the current value, at most `max_amount`,
   at most `max_per_stat` per lifetime) or else a **small** gain (`small`).
3. Old-age **decline** (`decline`: chance, amount, which stats) is rolled separately, never below 1.

Profiles map onto the existing life stages (`stages`), scaled to each species' lifespan, so a wolf's childhood is short.
Shipped: `childhood`, `adolescence`, `adulthood`, `old_age`. Percentages such as +10% / +50% are examples to tune.

**No runaway inflation:** growth chance tapers from `caps.taper_from` x cap to nothing at the cap; the cap is
`caps.default`, per stat, or per species (plus modifier `cap` bonuses) and never above the species' `stat_cap`; a
breakthrough is bounded in absolute size and count. A cap stops growth but never shrinks a stat.

**Specialised stats** (Magic, Swordsmanship, Marksmanship, Martial Arts) are skills, separate from base stats. They grow
(`profile.specialised`) only if the NPC already trains them, or an experience `trains` them, and only if the species can
train that skill; capped by `caps.specialised`.

**Traits:** `trait_acquisition` rolls traits.yaml `acquire:` rules once a year at `chance_scale` (50%) through the normal
`grant_trait` (prerequisites apply). A persistent NPC dies of old age past its species' lifespan (`aging.die_of_old_age`),
as named characters do.

## Modifiers and life experiences

`species`, `traits`, `talents` and `experiences` entries share one shape: `chance`, `amount`, `breakthrough` (multipliers),
`cap` (added to the cap), and `stats: {<stat>: {...}}` for single stats. An experience may also `trains: [skill stat]`.

Hooks for later systems (nothing grants these yet; no school, mentor or profession system exists):

* `add_experience(npc, "education")` / `remove_experience`: id from `experiences:`; idempotent. A school, mentor or
  profession needs only an id and an entry in the yaml.
* `apply_life_event(npc, "duel:won_1", stats={...}, traits=[...], experience=...)`: a breakthrough, new traits and/or an
  experience, applied **exactly once per key** (stored in `progress.events`).

## Once per year, retry-safe, deterministic

* The campaign has a `world_year` that moves once per year the playable character ages (Age Up) and never goes back, even
  when an heir takes over at an older age. The playable character's age-up never calls progression; they only share the
  clock.
* Each persistent NPC stores `progress` (`Npc.progress`): `aged_year` (the world year its age is current for) and
  `developed_age` (growth is applied once per age above this). `advance_world(campaign, year)` brings NPCs **up to** a
  year: repeating it, retrying a request or reloading a save changes nothing.
* Each year draws from `Random(f"{npc.seed}:npc:growth:{age}")` (initial development `:development:{age}`), always the
  same number of dice, so the same saved state replays identically. `sync_npc_to_year` is pure.
* Not touched: named characters (own lifecycle, authored stats), generic/temporary NPCs (never persisted), deceased or
  retired NPCs, other campaigns. Read endpoints never run any of this.

## Saves

There is no data migration. An NPC with no `progress` (saved by an older version) is stamped as fully developed for its
age the next time it is saved or ticked: **no growth is back-filled**. `Campaign.world_year` is added to an old campaign
table in place by `init_db`. `KnownNpc` is a new table. The tracked save backup was removed from the repository, so a
fresh game starts clean.
