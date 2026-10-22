# Named NPCs (authored lore characters)

Authored, lore-important characters: a legendary Sword Saint, a powerful mage, a ruler. Written as data in
`backend/app/data/named_characters.yaml`, brought into a game by story rules, and tracked as ordinary NPCs. Code:
`npc.py` (the definition model and loader), `named.py` (runtime), `story/npc_hooks.py` (story engine types).

There are **no named characters yet**; the file ships empty. Check what you write with
`cd backend && python -m app.validate_content`.

## Three things kept apart

| | Where | Mutable at runtime? |
|---|---|---|
| **Definition** | `named_characters.yaml`, version controlled | no. Title, lore, description and affiliations are only ever read from here, never copied into a save |
| **Instance** | an `Npc` in the existing `NpcRecord` JSON, id `named_<definition id>` | yes: current age, status, location, story flags, the definition version it was made from |
| **Links** | the existing relationship table | yes |

No new table and no migration: the instance is the existing NPC storage, with four optional fields on `Npc`
(`definition`, `definition_version`, `flags`, `location`).

## Definition format

```yaml
characters:
  - id: sword_saint_kael          # stable definition id; the runtime id is "named_sword_saint_kael". Never rename it.
    version: 1                    # bump when you change the definition (see "Updating a definition")
    name: Kael Auren              # exactly as written: never name-generated
    title: The Sword Saint        # optional
    species: human
    age: 74                       # the STARTING age
    description: ...              # optional, short, shown in the encounter window
    lore: ...                     # optional
    affiliations: [silver_order]  # optional plain tags; no faction simulation
    location: northern_keep       # optional starting location
    stats: {strength: 70, dexterity: 80, constitution: 60, wisdom: 55, charisma: 40}
    specialised_stats: {swordsmanship: 95}
    traits: [keen_sense]
    talent: ...
    generate: {stats: false, stat_range: [20, 60], combat: false, combat_focus: 1, combat_level: [5, 25],
               random_traits: 0, talent_chance: 0.0}
    lifecycle: {ageing: normal, lifespan_multiplier: 1.0}
    status: active
    relationships:
      - {kind: mentor, other: young_apprentice}
    flags: {met_player: false}
    encounter: {conditions: {type: age_at_least, value: 12}}
```

Only `id`, `name`, `species` and `age` are required. A field you leave out is left out of the character.

Validation (all problems are reported at once): unique ids and names; species, traits, talent and specialised stats must
exist; base stats must be known, at least 1 and within the species cap; every relationship target must be another defined
character, kinds must exist, duplicates and self-links are refused, a character may have at most two parents and nobody is
their own ancestor; `encounter.conditions` must be valid story conditions; flag keys and affiliations must be slugs; unknown
fields are refused.

## Starting attributes: fixed, or generated on purpose

**Authored values are fixed**: base stats, specialised stats, traits and talent are used exactly as written and are never
randomised. Nothing is generated unless `generate` allows it, and what is neither authored nor allowed stays unset.

| `generate` | Fills in | Only when |
|---|---|---|
| `stats: true` (+ `stat_range`) | base stats you did not list. You may list some and generate the rest | |
| `combat: true` (+ `combat_focus`, `combat_level`) | specialised stats | `specialised_stats` is left out (authored values are fixed) |
| `random_traits: N` | N extra traits on top of the authored ones | |
| `talent_chance: p` | a hidden talent | none is authored |

Generation uses the existing NPC generator. The seed is made from the **campaign seed and the definition id**: it is the
same every time within one campaign, differs between campaigns, and the result is **saved with the character** and never
rolled again, so later changes to the generator cannot alter a character who already exists.

**Power is never stored.** It is calculated from the saved skills by the existing Power system (`npc.npc_power`), like
anyone else's.

## Instantiation

Nothing is created by itself, and every game does not get every character. A story rule introduces them with the
`spawn_npc` effect (or code calls `named.instantiate`), and only if the definition's `encounter.conditions` hold.

* **Once only.** The id is stable and unique per campaign, so referencing a character from many encounters, retrying,
  or reloading returns the character who already exists, with their runtime state. A dead or retired character still
  exists and is not created again.
* **Per campaign.** The same definition in two campaigns is two independent characters.

## Updating a definition

Bump `version` when you edit a definition. A character who already exists is **never reset**. When next referenced:

| Updated | Never touched (runtime state) |
|---|---|
| the display name; the version recorded; flags the new version adds (never an existing value); links the new version adds | age, status, location, species, base and specialised stats, traits, talent, existing flags, existing links |

A definition describes where a character *starts*. Removing a definition leaves existing characters alone.

## Lifecycle

```yaml
lifecycle: {ageing: normal}                                # default: ages, dies of old age past the species lifespan
lifecycle: {ageing: longevity, lifespan_multiplier: 3}     # the same, with the lifespan multiplied
lifecycle: {ageing: immortal}                              # does not age and cannot die, by age or by story effect
```

Most named characters are `normal`; immortality is a choice, per character. Introduced named characters age one year
whenever the player does (Age Up), and only they: the rule is a simple deterministic one (no world simulation). An
immortal one can still be retired from the story. The dead stay dead.

## Story engine

Stories refer to a character by **definition id**; `other` may also be `player`.

| Conditions | True when |
|---|---|
| `npc_exists {npc}` | the character has been introduced (alive or not) |
| `npc_status {npc, status}` | `active`, `deceased` or `retired` |
| `npc_related {npc, kind, other, role?}` | they are linked to `other`; `role`: `source`, `target` or `either` (default) |
| `npc_flag_equals {npc, flag, value}` | the flag equals value (type-aware) |
| `npc_flag_at_least {npc, flag, value}` | a numeric flag is at least value (unset is 0) |

| Effects | Does |
|---|---|
| `spawn_npc {npc}` | introduces them once, if `encounter.conditions` hold |
| `set_npc_flag {npc, flag, value}`, `increment_npc_flag {npc, flag, amount?}` | change their flags |
| `set_npc_status {npc, status, cause?}` | an immortal cannot be made deceased |
| `link_npc {npc, kind, other, role?}` | relate them to another character or the player; repeating is harmless |

An event may also carry `npc: <id>`: the character the encounter is with. The existing encounter window then shows their
name, title and description. It is a reference only; add `npc_*` conditions to decide when the encounter can happen.

These are ordinary story-engine types, so they use the same queue, one-at-a-time window, transactions (a failing
effect rolls the whole choice back), choice log and save/load. References to undefined characters are caught by the content
validator. Add a new character by writing a definition, and a new encounter by writing an event. No new handler code.

## With inheritance and legacy

* A named character can be a parent of descendants (`create_descendant`), as any NPC can.
* A named character is **never offered as an heir**: becoming the player would retire the one instance that stories and
  their definition refer to. Only ordinary persistent descendants carry on (see `LEGACY.md`). Named characters, their flags
  and their links are untouched by a transition and are not created twice.

## Not implemented

A catalogue of characters, new storylines, faction or world simulation (ageing is the one simple rule above), automatic
spawning, and named heirs.
