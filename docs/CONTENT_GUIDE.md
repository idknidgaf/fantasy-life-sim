# Content guide

Everything in the game world is data in `backend/app/data/`. Adding a species, trait, talent or story event is
a content edit, not programming. After any edit run:

    cd backend
    python -m app.validate_content

It reports **every** mistake at once, with the file and the id. The game also refuses to start on invalid content.

| File | Holds |
|---|---|
| `skills.json` | skill categories and skills (JSON; the others are YAML) |
| `axes.yaml` | personality and alignment axes |
| `stages.yaml` | life stages and what each allows |
| `species.yaml` | species rules |
| `traits.yaml` | traits |
| `talents.yaml` | hidden talents |
| `items.yaml` | items |
| `events/*.yaml` | story events (any number of files; ids must be unique across all of them) |

## Life stages

`stages.yaml` thresholds are in **human-equivalent years**; a species' real age is scaled by `80 / lifespan`.
So a wolf (lifespan 14) is an adolescent at 2, an elf (lifespan 120) at 18. **Events should use
`min_stage` / `max_stage`, not raw ages**, so they make sense for every species. Stage ids in order:
`infancy, early_childhood, childhood, adolescence, young_adult, adulthood, middle_age, elder`.

Infants cannot decide anything. Give infancy-only events exactly one choice (life happens *to* them).

## Requirements (used by conditions, choice locks, traits and talents)

One key picks the kind; the others are its bounds. Combine with `any`, `all`, `not`.

    - {stat: wisdom, min: 20}                 # effective stat (includes trait bonuses)
    - {skill: exploration, min_level: 10}
    - {skill_count: 8, min_level: 3}           # at least 8 skills at level 3+
    - {max_top_skill: 30}                     # best skill is 30 or lower (a generalist)
    - {trait: keen_sense}
    - {flag: guild_member}                    # a story fact set by an effect
    - {counter: selfless_acts, min: 3}        # a tally raised by effects
    - {item: healing_salve}      {item_tag: valuable_offering}
    - {axis: good_evil, min: 20}              # personality or alignment axis, -100..100
    - {species: [human, elf]}  {species_category: beast}  {species_tag: predator}
    - {location_tag: city}
    - {any: [ ... ]}  {all: [ ... ]}  {not: { ... }}

## Effects (what choices and awakenings do)

Any effect may add `chance: 0.3`.

    - {stat_delta: {strength: 1}}             # clamped to the species' stat cap
    - {skill_xp: {archery: 2}}                # a skill id, or a CATEGORY (goes to your best skill in it)
    - {axis_delta: {bravery: 4}}
    - {grant_trait: pack_alpha}   {remove_trait: x}
    - {flag: name}   {clear_flag: name}
    - {counter: {mental_trials: 1}}
    - {grant_item: silver_locket}   {grant_item: {id: rough_gemstone, count: 2}}
    - {consume_item: {tag: valuable_offering}}   {consume_item: {id: healing_salve}}
    - {set_location: city}
    - {die: {cause: wolves}}
    - {trigger_event: some_event_id}          # queues a follow-up event

Skill gains are multiplied by species and trait aptitude.

## Events

    - id: forest_ancient_altar
      title: The Ancient Altar
      weight: 5                    # relative chance among eligible events
      once: true                   # optional: never repeats in one life
      trigger_only: true           # optional: never random; only fired by trigger_event / awakening
      narrative: >
        Text shown to the player.
      conditions:                  # all optional
        min_stage: adolescence
        max_stage: adulthood
        life_stages: [childhood]   # alternative to min/max
        min_age: 1                 # literal years, rarely needed
        location_tags: [ancient_forest]      # character is in ANY of these
        species: [elf]  species_categories: [humanoid, beast, monstrous]
        species_tags: [speaker, tool_user]   # species has ALL of these
        requires_all: [ ... ]  requires_any: [ ... ]  excludes: [ ... ]
      choices:
        - id: study
          text: Study the runes
          requirements: [ ... ]    # locked (shown with the reason) until met
          result: What happens.
          effects: [ ... ]
        - id: fight                # a skill check: no result/effects, use success + failure
          text: Stand and fight
          check: {stat: strength, difficulty: 50}   # or {skill: x, difficulty: n}
          success: {result: "...", effects: [ ... ]}
          failure: {result: "...", effects: [ ... ]}

A check succeeds with probability `0.5 + (value - difficulty) / 100`, kept between 5% and 95%.
Every event needs at least one choice with no requirements (otherwise the player could get stuck).
Quote any text containing `: ` (a colon and a space).

## Species

`stat_modifiers` (added to the 20-60 birth roll), `skill_aptitude` (xp multipliers by skill, category or `all`),
`innate_skills` (starting levels; also unlocks locked "supernatural" skills), `innate_traits`, `allowed_skills`
(if present, the only skills it can train), `tags`, `habitats` (possible birth locations), `lifespan`,
`base_mortality`, `stat_cap` (above 100 for supernatural beings), `event_rate`, `axis_bias`, and `labels`
(rename the work/play/rest actions, e.g. wolves "Hunt and forage").

## Traits

`modifiers` (`stat`, `skill_aptitude`, `mortality`, `unlock_skills`), `abilities`, `prerequisites`, `birth`
(`chance` plus requirements), `acquire` (rules rolled every year-end: when `requirements` hold, gain it with
`chance`). Implemented abilities: `second_wind` (a failed check may be retried). The others are reserved
for the combat system.

## Hidden talents

Rolled **once, at birth** (1%) from the talents whose `eligible` requirements the newborn meets, then saved.
Never rerolled and never shown while dormant. Dormant talents drop vague `hints` into the life story.
`awaken` is the unlock condition (`min_stage` plus `requirements`); `on_awaken` has a `narrative`, `effects`
and an optional `trigger_event` (a `trigger_only` event that starts the talent's storyline).

To test one, find a seed that rolls it:

    python -m tools.find_talent_seeds human master_of_none

then enter that seed (and species) when starting a life.
