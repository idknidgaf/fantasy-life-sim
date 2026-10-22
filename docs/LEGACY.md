# Legacy and the playable-character transition

When the playable character dies, the player can carry on as one of their living descendants. Code:
`backend/app/legacy.py`. It builds on the relationship model (`relationships.py`) and the descendants created by
`descendants.py`; the inheritance rules themselves are unchanged (`INHERITANCE.md`).

## Flow

1. **A character dies** (old age, an event, a battle: every death passes through `main._mutate`). In the same
   transaction the life is **completed**: one `LifeRecord` is written for that character, and the life's open story
   encounters are closed (`expired`, reason `death`).
2. **`GET /api/legacy`** reports the state: `alive`, `awaiting_heir` or `game_over`, the eligible heirs, and the lives
   completed so far.
3. **`POST /api/legacy/continue`** carries on as an heir (see below). The screen reloads as that character.
4. **No heir** means `game_over`. Nobody unrelated is ever chosen for you; the player can begin a new, separate life.

## Eligibility

An heir is a **living descendant of the dead character**, found through the existing `parent` links
(`relationships.eligible_descendants`):

* a child, grandchild and so on, **nearest generation first** (each is labelled `child` or `descendant`);
* a kept NPC (persistent or named) whose status is **`active`**: alive and not retired;
* never: the dead character, a **dead** or **retired** descendant, a **temporary** (generic) NPC, anyone who is not
  descended from them (a partner's other children, other families), or an earlier playable life.

Only the dead character's own line is offered, never "every NPC in the world". One heir: no list to choose from, just a
single "Continue as" button (`npc_id` may even be left out). Several: the player chooses; leaving `npc_id` out is
refused (400). A character who is not eligible is refused (409).

## What the transition does

The engine plays a `CharacterRecord`, while a descendant is an NPC, so a heir is **built into a record once, from their
own saved data**: nothing is rerolled or regenerated.

| Kept exactly as saved | Filled in, because an NPC has no field for it |
|---|---|
| name, species, **age**, base stats, skills, traits, hidden talent and its state | personality axes start at the species' bias (no roll); home picked as a newborn's is (same seeded stream); health starts full; history starts with a `legacy` entry; no items, flags or pending events |

Then, as one transaction:

* the heir's **family links are re-pointed** from their NPC id to the new character (`rekey_character`), so parents,
  children, partners and the rest are all still there, now attached to the playable character;
* the heir's NPC row is **retired**, so exactly one record stands for that person (no copy kept in sync);
* the **playable role** moves: `PlayableRole` (one row per campaign) now points at the new record. With no row, which is
  every older save, the campaign's newest record is the playable one;
* the story engine's `age` and `state` scans run for the new life, as at the start of a campaign.

The new character begins at **their own current age and stage** and ages from there. Everything the game reads (actions,
Power, combat, story state, history, saves) works on the active record, so it all follows automatically.

## The completed life is not copied

A completed life is the dead `CharacterRecord`: identity, final attributes, history, cause of death, battle records and
choice log stay exactly as they were (nothing can change a dead character). Family links keep naming its `pc_<id>`. A
`LifeRecord` only marks the life as completed and remembers who carried on (`successor_character_id`, and
`successor_npc_id`, which survives the retirement of the heir's NPC row). `GET /api/legacy` lists these lives; the new
character's history log opens with a `legacy` entry, and the Character tab shows an "Earlier lives" list.

## Safe to retry

* One `LifeRecord` per dead character (unique): a death is recorded once, however often it is processed.
* Choosing an heir **claims** the life with a conditional update (`successor_npc_id IS NULL`), so of two racing requests only
  one wins; the other gets 409 and creates nothing.
* Repeating a request that already succeeded returns the same character and changes nothing; asking for a different heir
  afterwards is refused.
* The transition is one transaction. If any step fails everything is rolled back, and it can be tried again.
* A dead character from an older save is recorded when first seen (`GET /api/legacy`), once.

## API

| Route | Result |
|---|---|
| `GET /api/legacy` | `{status, heirs[], lives[]}`. Heirs: `npc_id, name, species_*, age, relation, power, traits`. Lives, oldest first: `name, age, cause_of_death, successor_name, current` |
| `POST /api/legacy/continue` body `{"npc_id"?: string}` | 200 the new active character. 400 several heirs and none chosen. 409 alive, no heirs, not eligible, or already continued as someone else |

## Migration

None. `LifeRecord` and `PlayableRole` are new tables, created by `create_all` at startup; no existing table changes.
Existing saves keep working (no role row means "newest record"). Deleting a campaign removes its legacy rows.

## Limits

* **Nothing in the game creates descendants yet** (`INHERITANCE.md`: no API or UI for it), so until something calls
  `create_descendant` every death ends in `game_over`. The transition is ready for it.
* NPCs do not age, so a heir plays from the age they were saved at.
* An heir's attributes are those of an NPC (newborn aptitude plus a small specialised head start); the player's developed
  stats are not inherited (existing inheritance rule).
* No family-tree view, no named lore NPCs, no extra inheritance rules.
