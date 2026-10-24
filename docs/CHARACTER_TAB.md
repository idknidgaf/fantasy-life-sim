# Character tab (People): read-only foundation

The "People" tab (`frontend/src/components/PeopleTab.tsx`) browses NPCs the player knows, in three groups. It only
displays existing data: no social actions, discovery, closeness decay or relationship progression exist yet. The tab is
labelled **People** because the nav already has a "Character" tab (the player's own sheet); the label is the `People`
entry in `NavSidebar.tsx`.

## Who is in each group (`backend/app/character_groups.py`)

All derived from existing data at read time; nothing is copied or stored per group.

| Group | Rule |
|---|---|
| **Family** | NPCs related to the player by family links (`family.family_of`: parent, child, sibling, half-sibling, grandparent, cousin, ancestors and descendants up to 4 generations), **and known** to the player. Earlier lives (`pc_<n>`) are not NPCs and are not listed. Relatives the player has never met are not listed. |
| **Acquaintances** | NPCs (persistent or named) the player has personally met and holds an established relationship with: registered by an explicit outcome (`establish_acquaintance`, see `NPC_AWARENESS.md`) and still active. A family link, an authored friend link or a mention does not make one, and ending a relationship removes them. |
| **Notable Figures** | **Named** NPCs the player knows of: introduced or revealed by a story (`spawn_npc`, `reveal_npc`), recorded as met, or linked to. No personal relationship is needed, and one ending never removes them. |

An NPC can be in several groups (a child who is also a friend is in Family and Acquaintances) but is listed once per
group (SQL over one row per NPC, plus a de-duplication on the screen). A persistent NPC who was only met, with no
relationship, is known but in none of the groups. A named NPC can be both Notable and an Acquaintance. After an heir takes
over, family and awareness carry on, but the parent's acquaintances do not (personal state belongs to one life).

## API

One change: `GET /api/npcs` takes `category=family|acquaintances|notable`, and each entry has `categories` and
`closeness`; family roles are added to `relations`. Everything else is the existing endpoint (`NPC_API.md`). The tab
makes three requests (one per group, `limit=100`) when it opens, so it is always current. No new endpoints, tables or
columns, so existing saves work unchanged.

## What an entry shows

Name, age ("Died at N" for the dead), species, status, title, relation to the player, and a closeness bar. Only data the
game has is shown:

| Field | Source / behaviour |
|---|---|
| Status | `active` Alive, `deceased` Deceased, `retired` Away (left the story), anything else Unknown |
| Title | A named character's authored `title`. Persistent NPCs have no job or profession in the data, so none is shown; their relation (Child, Friend...) is the only role |
| Gender | **Not shown: no gender is stored anywhere.** Adding it needs a data decision first |
| Closeness | The bar is drawn only when the API gives a number (0-100, clamped): the acquaintance's closeness, set and changed by story outcomes (`NPC_AWARENESS.md`). Not an acquaintance: no bar |

## Tests

Backend `tests/test_character_groups.py` (group rules, overlap, duplicates, empty states, pagination, heirs, unmet
relatives, old saves, bounded queries, and the frontend type contract). Frontend: `npm test` (Vitest + Testing Library):
`npcFormat` helpers and `PeopleTab` (switching, empty states, duplicates, information shown, closeness, errors, retry,
navigation).
