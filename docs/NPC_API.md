# NPC API and family queries

Read-only endpoints for the future Character tab (code: `app/npc_api.py`). They act on the **active campaign and its
playable character**, like the story routes. The app has no accounts, so the campaign is the boundary. They never change
anything: no commit, no ageing or growth, nothing generated is saved.

## Who counts as "known"

An NPC is known to a campaign when it has a persistent `KnownNpc` row (`known.record_known(session, campaign_id, npc_id,
source)`: story introduction via `spawn_npc`, an encounter, a birth into the family, `manual`) **or** is linked by any
relationship to the current playable character. Merely existing, being generated or saved does not count; temporary
(generic) NPCs never do. It belongs to the campaign, so an heir still knows who their parent knew.

Unknown, missing, generic, other-campaign NPCs are all the same `404 {"detail": "No such NPC"}`.

## Endpoints (all under `/api`)

### `GET /npcs?limit=25&offset=0&category=&status=&species=`

`limit` 1-100 (default 25), `offset` >= 0, `category` one of `family|acquaintances|notable` (see `CHARACTER_TAB.md`), `status`
one of `active|deceased|retired`, `species` an id. Out of range or unknown values -> 422. Oldest first. Each NPC is listed
once; `total` counts the filtered set.

```json
{"total": 2, "limit": 25, "offset": 0, "items": [{
  "id": "npc_7917...", "name": "Halania Brookwood", "category": "persistent", "status": "active",
  "species": {"id": "human", "name": "Human", "category": "humanoid"}, "age": 0,
  "stage": {"id": "infancy", "name": "Infant", "description": "..."},
  "power": 0.0, "relations": ["child"], "title": null, "categories": ["family"], "closeness": null, "contact": "open"}]}
```

`relations` is how they relate to the player: direct links (`parent`, `child`, `mentor`, `mentee`, `friend`, `partner`,
`rival`) and family roles (`sibling`, `half_sibling`, `grandparent`, `cousin`, `ancestor`, `descendant`), without repeats.
`categories` lists every Character-tab group the NPC belongs to. `closeness` (0-100) is the acquaintance's closeness
and `null` for anyone who is not an acquaintance. `contact` is `open`, `hostile` or `refused` (`NPC_AWARENESS.md`).

### `GET /npcs/{npc_id}`

The summary fields plus:

```json
{"surname": "Brookwood", "description": "", "cause_of_death": null,
 "stats": {"strength": 48, "dexterity": 42, "constitution": 35, "wisdom": 46, "charisma": 54},
 "effective_stats": {"...": 0}, "stat_cap": 100,
 "specialised_stats": {"magic": 0.0, "swordsmanship": 12.5, "marksmanship": 0.0, "martial_arts": 0.0},
 "traits": [{"id": "keen_sense", "name": "...", "description": "...", "tier": "common", "effects": "..."}],
 "talent": null,
 "power_breakdown": {"total": 12.5, "subtotal": 0, "stats": {}, "equipment": {}, "skills": {}, "modifiers": {}, "formula": {}, "notes": []},
 "met": {"source": "birth", "first_met_year": 0}}
```

`stats`/`effective_stats` are `null` if never generated. `talent` appears only once awakened. `power` equals
`power_breakdown.total` (the Power system's number). `met` is `null` when known only through a relationship. `id` is the
stable character id. Malformed ids (including `pc_*`) -> 400.

### `GET /npcs/{npc_id}/family`

```json
{"id": "npc_...", "parents": [{"id": "pc_1", "kind": "player", "name": "Aren Brookwood", "known": true, "status": "active"}],
 "children": [], "grandparents": [],
 "siblings": [{"id": "npc_...", "kind": "npc", "name": "...", "known": true, "status": "active", "relation": "full|half", "shared_parents": ["pc_1"]}],
 "cousins": [{"id": "npc_...", "kind": "npc", "name": "...", "known": true, "status": "active", "shared_grandparents": ["npc_..."]}],
 "truncated": false}
```

Relatives the family has not met have `name: null, status: null, known: false` (their id only). Each group is capped at 50
(`truncated`). Constant number of queries however large the family.

No internal fields are returned (database ids, seeds, generation specs, flags, progression state, authored definition
internals); a dormant talent never is.

## Family queries (`app/family.py`, plus `relationships.py`)

All read-only, batched, and safe with missing parents or deleted characters. Nothing prohibits siblings or cousins: they
are identified so a game rule can decide (`inheritance.yaml` `parents.forbidden_relations`, empty by default).

| Function | Result |
|---|---|
| `shared_parents(session, cid, a, b)` | parents in common |
| `siblings_of` / `full_siblings_of` / `half_siblings_of` | `Sibling(ref, kind full\|half, shared_parents)`. Full: the same known parents; one shared parent, or a one-parent child, is half |
| `cousins_of` | first cousins with `shared_grandparents` (needs known grandparents) |
| `relations_between(a, b)` | "b is a's ..." any of `parent child sibling half_sibling cousin ancestor descendant` |
| `family_of(ref)` | everything above in one bundle |
| `relationships.ancestors_of / descendants_of` | bounded by `max_depth` |
| `validate_family(session, cid)` | `FamilyIssue`s: `self_parent`, `duplicate_link`, `too_many_parents`, `circular_ancestry`, `dangling_link`. Read-only; for verifying a reloaded save |

The write path (`relationships.add_relationship` / `add_parent`) already refuses self-parentage, circular ancestry, more
than two parents, and treats a repeated parent link as the same link.
