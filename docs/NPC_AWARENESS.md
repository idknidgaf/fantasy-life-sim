# NPC awareness and relationship registration

Three states are kept apart. Nothing registers by itself: an NPC becomes Notable or an Acquaintance only when an event
outcome (or later a social action) says so. Code: `backend/app/awareness.py` (the rules), `backend/app/story/npc_hooks.py`
(the story effects and conditions).

| State | What it means | Where it lives | Survives the others ending? |
|---|---|---|---|
| **Notable / known** | The player has heard of or met the NPC (story, dialogue, an encounter). The group Notable Figures is the *named* NPCs that are known | A `KnownNpc` row (one per NPC per campaign) | Yes: it is never removed by a relationship ending |
| **Acquaintance** | The player has personally met the NPC and has an established relationship with them (persistent *or* named) | The same row: `holder`, `acquainted`, `closeness` (0-100), `stance`, `acquainted_year` | n/a |
| **Family** | Parent links (`family.py`) | `CharacterRelationship` parent links | Yes: nothing here adds or removes one |

A named NPC can be both Notable and an Acquaintance. A child can be Family and an Acquaintance. Each group lists an NPC once.

## Story outcomes (effects)

The character must already be introduced, like every other NPC effect: put `spawn_npc` or `reveal_npc` earlier in the
outcome. Otherwise the effect fails and the whole choice rolls back. A refusal is **not** an error: the result carries
`changed: false` and a `reason` (`they are hostile`, `already an acquaintance`...), so one outcome can serve every case.

| Effect | Does |
|---|---|
| `reveal_npc {npc}` | Introduces them if needed (the definition's `encounter.conditions` apply) and makes them a Notable Figure. No relationship |
| `establish_acquaintance {npc, kind?, closeness?}` | They have personally met. `kind`: `friend`, `partner` or `rival` adds that link. Starting closeness is 10 unless given. Refused if they are hostile, refuse contact, or are dead/gone. Repeating it changes nothing (closeness is not reset) |
| `change_relationship {npc, amount}` | Improve (+) or damage (-) an existing acquaintance, clamped to 0-100. Reaching 0 does not end it. Not an acquaintance: nothing changes |
| `set_npc_stance {npc, stance}` | `hostile` or `refused`: any acquaintance ends (closeness and personal links go) and a new one is refused. They never become an acquaintance this way. `open` restores contact. The NPC becomes known of |
| `end_relationship {npc}` | Ends the acquaintance and its friend/partner/rival/mentor links. The character, family links and Notable status stay |

Conditions to branch on them: `npc_acquainted {npc}`, `npc_closeness_at_least {npc, value}`, `npc_stance {npc, stance}`.

Existing effects keep working: `spawn_npc` still introduces a character and so makes them known (a Notable Figure), but
never an acquaintance. `link_npc` and the links a definition authors make a relationship *link*, not an acquaintance.
An NPC merely named in an event's text, `npc:` field or conditions is not even introduced.

```yaml
choices:
  - id: share_a_drink
    effects:
      - {type: spawn_npc, npc: old_smith}
      - {type: establish_acquaintance, npc: old_smith, kind: friend}
  - id: pick_a_fight
    effects:
      - {type: spawn_npc, npc: old_smith}
      - {type: set_npc_stance, npc: old_smith, stance: hostile}     # known of, never an acquaintance
  - id: part_ways
    conditions: {type: npc_acquainted, npc: old_smith}
    effects:
      - {type: end_relationship, npc: old_smith}
```

## Identity and persistence

* One `KnownNpc` row per `(campaign, NPC id)` (unique), on the stable id of the NPC record. Events never create a second
  character or row: a named character is created once (`named.instantiate`), every later event reuses it.
* Python callers use `awareness.reveal / establish_acquaintance / change_closeness / set_stance / end_relationship`
  directly with any kept NPC's id (persistent or named). Each is idempotent and returns a `Registration`.
* Personal state belongs to one playable life (`holder`, a `pc_<n>`). When an heir takes over they are aware of who their
  parent knew and keep the family, but are not their parent's friends until they establish it themselves.
* A save from before this change gains the five columns in place (`init_db`) and loads as aware but not acquainted.
* Registered by the API read side: `closeness` and `contact` on `GET /api/npcs` entries.

Not here (separate tasks): the social-action budget, closeness decay, new interaction menus, any new UI.
